import json
from types import SimpleNamespace

import httpx
import pytest

from app.memory_rest import MemorySchemaMismatch, SupabaseRestMemory


@pytest.fixture
def store(monkeypatch):
    from app import memory_rest

    calls = []
    rows = []
    requests = []
    vector = [0.6, 0.8]

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, params))
            return SimpleNamespace(
                fetchone=lambda: {"dimensions": 2}, fetchall=lambda: list(rows),
            )

        def close(self):
            pass

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"embedding": vector}]})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(respond),
    ))
    monkeypatch.setattr(memory_rest.rest_conn, "connect", Conn)
    client = SupabaseRestMemory(
        base_url="http://localhost:11434/v1", api_key="ollama", model="test-embed",
        dimensions=2, collection="test_memories", timeout_s=180,
    )
    calls.clear()
    return SimpleNamespace(client=client, calls=calls, rows=rows,
                           requests=requests, vector=vector)


def test_preference_and_owner_are_bound_values_and_model_is_recorded(store):
    text = "I prefer two spares; '); DROP TABLE test_memories; --"
    result = store.client.add(text, user_id="alice", infer=False)
    sql, params = store.calls[0]
    assert text not in sql
    assert json.loads(params[1]) == [0.6, 0.8]
    payload = json.loads(params[2])
    assert payload["data"] == text
    assert payload["user_id"] == "alice"
    assert payload["embedding_model"] == "test-embed"
    assert result["results"][0]["id"] == params[0]
    request = store.requests[0]
    assert str(request.url) == "http://localhost:11434/v1/embeddings"
    assert json.loads(request.content)["dimensions"] == 2


def test_search_scopes_user_and_model_before_limit(store):
    store.rows.append({"id": "test-id", "score": 0.9,
                       "payload": {"data": "two spares", "user_id": "alice"}})
    result = store.client.search("spares", user_id="alice", limit=3)
    sql, params = store.calls[0]
    assert "WHERE payload->>'user_id' = ? AND payload->>'embedding_model' = ?" in sql
    assert sql.index("WHERE") < sql.index("ORDER BY") < sql.index("LIMIT")
    assert params[1:3] == ("alice", "test-embed")
    assert params[-1] == 3
    assert result["results"][0]["memory"] == "two spares"


@pytest.mark.parametrize("vector", [[1.0], [float("nan"), 1.0], [0.0, 0.0], [True, 1.0]])
def test_invalid_embedding_never_writes_to_storage(store, vector):
    store.vector[:] = vector
    # httpx's JSON encoder itself rejects NaN, also before any write.
    with pytest.raises(ValueError):
        store.client.add("preference", user_id="alice")
    assert store.calls == []


@pytest.mark.parametrize("user", ["", "   ", None])
def test_missing_user_never_embeds_or_reads(store, user):
    with pytest.raises(ValueError):
        store.client.search("spares", user_id=user)
    assert store.requests == []
    assert store.calls == []


def test_schema_mismatch_fails_before_using_collection(monkeypatch):
    from app import memory_rest

    conn = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(fetchone=lambda: {"dimensions": 1536}),
        close=lambda: None,
    )
    monkeypatch.setattr(memory_rest.rest_conn, "connect", lambda: conn)
    with pytest.raises(MemorySchemaMismatch):
        SupabaseRestMemory(base_url="http://localhost:11434/v1", api_key="ollama",
                           model="test", dimensions=1024, collection="old_memories",
                           timeout_s=180)


def test_collection_name_cannot_inject_sql():
    with pytest.raises(ValueError, match="collection"):
        SupabaseRestMemory(base_url="http://localhost:11434/v1", api_key="ollama",
                           model="test", dimensions=1024, collection='bad"; DROP TABLE x',
                           timeout_s=180)
