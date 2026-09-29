import sys
from types import SimpleNamespace

from conftest import ENG


def test_memory_endpoints_disabled_by_default(client):
    r = client.get("/memory/search", params={"q": "anything"}, headers=ENG)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert "mem0" not in sys.modules

    r = client.get("/memory/status", headers=ENG)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert "mem0" not in sys.modules

    r = client.post("/memory", json={"text": "remember this"}, headers=ENG)
    assert r.status_code == 503


def test_memory_add_and_search_when_enabled(client, monkeypatch):
    from app import memory

    captured = {"add": [], "search": []}

    class FakeClient:
        def add(self, text, *, user_id: str, infer: bool = True, **kw):
            captured["add"].append({"text": text, "user_id": user_id, "infer": infer,
                                    "metadata": kw.get("metadata")})

        def search(self, query, *, user_id: str, limit: int = 5, **_kw):
            captured["search"].append({"query": query, "user_id": user_id, "limit": limit})
            return {"results": [{"memory": "phoenix under $50", "score": 0.9}]}

    fake_client = FakeClient()

    class FakeMemory:
        @staticmethod
        def from_config(_config):
            return fake_client

    monkeypatch.setenv("MEM0_ENABLED", "1")
    monkeypatch.setenv("MEM0_VECTOR_STORE", "pgvector")
    monkeypatch.setenv("MEM0_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("MEM0_DATABASE_URL", "postgresql://example.invalid/postgres")
    monkeypatch.delenv("MEM0_DIR", raising=False)
    monkeypatch.setitem(sys.modules, "mem0", SimpleNamespace(Memory=FakeMemory))
    monkeypatch.setattr(memory, "_client", None)
    monkeypatch.setattr(memory, "_unavailable", False)
    monkeypatch.setattr(memory, "_runtime_dir", None)
    monkeypatch.setattr(memory, "_last_error", None)

    r = client.post(
        "/memory",
        json={"text": "I don't stock Phoenix consumables under $50"},
        headers=ENG,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert captured["add"][0]["user_id"] == "alice"
    assert captured["add"][0]["infer"] is False
    assert captured["add"][0]["metadata"] == {"embedding_model": "qwen3-embedding:0.6b"}

    r = client.get("/memory/search", params={"q": "Phoenix"}, headers=ENG)
    assert r.status_code == 200
    got = r.json()
    assert got["enabled"] is True
    assert got["count"] == 1
    assert got["results"][0]["memory"] == "phoenix under $50"

    r = client.get("/memory/status", params={"probe": "true"}, headers=ENG)
    assert r.status_code == 200
    status = r.json()
    assert status["enabled"] is True
    assert status["ready"] is True
