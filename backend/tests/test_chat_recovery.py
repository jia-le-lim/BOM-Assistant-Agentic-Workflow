"""Regressions for real fallback failures, with controlled provider failure modes.

Routing assertions inspect actual database results; live model checks live in
scripts/check_chat_retrieval.py because the offline stub cannot validate Qwen.
"""

import json

import pytest

from conftest import ENG, VIEWER, make_row, rows_to_csv, upload
from app.agent import tools as T
from app.agent.graph import run_chat
from app.agent.loop import MAX_TOOL_CALLS, run_agent
from app.agent.prompts import DONT_KNOW
from app.db import get_conn
from app.llm.provider import Response, ToolCall

ACTOR = {"user": "alice", "role": "engineer"}


@pytest.fixture()
def inventory(client):
    rows = [make_row(item_id=f"00{i:04}", item_desc=f"CABLE {i}", max_qty=i,
                     rop_qty=2, min_qty=1) for i in range(1, 29)]
    rows += [make_row(item_id="009000", item_desc="SENSOR CABLE"),
             make_row(item_id="009001", item_desc="WIRING HARNESS"),
             make_row(item_id="009002", item_desc="VACUUM TUBING"),
             make_row(item_id="000001", stockroom_id="25", item_desc="CABLE SECOND ROOM", max_qty=99),
             make_row(item_id="009003", item_desc="CABLE QUARANTINED", last_30_day_cnsmptn_qty=-1)]
    return upload(client, rows_to_csv(rows)).json()["batch_id"]


def query(bid, name="search_items", actor=ACTOR, **args):
    conn = get_conn()
    try:
        ctx = T.ToolContext(conn, actor, bid, "test retrieval")
        return json.loads(T.dispatch(ctx, name, args)), ctx
    finally:
        conn.close()


def score(client, bid):
    result = client.post(f"/run-recommendation?batch_id={bid}", headers=ENG)
    assert result.status_code == 200, result.text


def provider(monkeypatch, responses, intent="lookup"):
    class Scripted:
        name, model = "scripted", "recovery-test"

        def __init__(self):
            self.responses = iter(responses)
            self.offered = []

        def chat(self, messages, tools):
            if messages[0].content.startswith("Classify the engineer's"):
                return Response(content=intent)
            self.offered.append({t.name for t in tools})
            value = next(self.responses)
            if isinstance(value, Exception):
                raise value
            return value
    instance = Scripted()
    monkeypatch.setattr("app.agent.graph.get_provider", lambda: instance)
    monkeypatch.setattr("app.agent.loop.get_provider", lambda: instance)
    return instance


def call(name, **args):
    return Response(tool_calls=[ToolCall("t1", name, args)])


def test_category_works_before_scoring_without_similarity_cache(inventory):
    data, ctx = query(inventory, category="Cables", limit=20)
    assert data["total_count"] == 30  # 28 cables + harness + second stockroom
    assert data["returned_count"] == 20 and data["has_more"]
    assert all(row["part_category"] == "cable" for row in data["items"])
    assert all(row["status"] == "unscored" for row in data["items"])
    assert ctx.sources[0]["batch_id"] == inventory
    second, _ = query(inventory, category="cable", limit=20, offset=20)
    assert second["total_count"] == 30 and second["returned_count"] == 10
    assert not second["has_more"]
    ids = lambda rows: {(r["item_id"], r["stockroom_id"]) for r in rows}
    assert not ids(data["items"]) & ids(second["items"])
    assert "009000" not in {r["item_id"] for r in data["items"] + second["items"]}
    assert "009003" not in {r["item_id"] for r in data["items"] + second["items"]}


def test_description_and_stockroom_preserve_full_identity(inventory):
    data, _ = query(inventory, query="tubing")
    assert data["total_count"] == 1
    assert data["items"][0]["item_id"] == "009002"
    first, _ = query(inventory, query="000001", stockroom_id="24")
    second, _ = query(inventory, query="000001", stockroom_id="25")
    assert first["items"][0]["current_max"] == 1
    assert second["items"][0]["current_max"] == 99


def test_literal_search_does_not_treat_sql_wildcards_as_match_all(inventory):
    for value in ("%", "_", "' OR 1=1 --"):
        result, _ = query(inventory, query=value)
        assert result["total_count"] == 0


@pytest.mark.parametrize("args", [{"category": "nonexistent"}, {"risk_level": "severe"},
                                  {"limit": 0}, {"limit": True}, {"offset": -1},
                                  {"uncovered_dormant": "false"}, {"query": ["cable"]}])
def test_invalid_filters_are_explicit_not_silently_ignored(inventory, args):
    data, ctx = query(inventory, **args)
    assert data["error"]
    assert not ctx.sources
    assert ctx.notices


def test_empty_search_is_grounded_and_page_count_is_not_total(inventory):
    data, ctx = query(inventory, query="not-present")
    assert data["items"] == [] and data["total_count"] == 0
    assert ctx.sources
    data, _ = query(inventory, category="cable", offset=500)
    assert data["items"] == [] and data["total_count"] == 30


def test_counts_and_workspace_isolation(client, inventory):
    other = upload(client, rows_to_csv([make_row(item_id="999999", item_desc="CABLE")])).json()["batch_id"]
    score(client, inventory)
    result, _ = query(inventory, "list_review_queue", category="cable", limit=1)
    assert result["total_count"] == 30 and result["returned_count"] == 1
    result, _ = query(other, category="cable")
    assert result["total_count"] == 1 and result["items"][0]["item_id"] == "999999"


def test_unscored_missing_and_absent_workspace_have_actionable_messages(inventory):
    for bid, args, phrase in [(inventory, {"risk_level": "High"}, "has not been scored"),
                               (999999, {}, "not found or unavailable"), (None, {}, "Choose a workspace")]:
        data, _ = query(bid, **args)
        assert phrase in data["error"]


def test_uncovered_dormant_uses_confirmed_rules_and_role_gate(client, inventory):
    score(client, inventory)
    conn = get_conn()
    try:
        conn.execute("UPDATE recommendation_result SET route='dormant' WHERE batch_id=?", (inventory,))
        conn.execute("INSERT INTO user_dormant_rule_config (owner_user, scope, match_key, policy, fixed_qty, set_by, confirmed) VALUES ('alice','item','000001','fixed_qty',2,'test',1)")
        conn.execute("INSERT INTO user_dormant_rule_config (owner_user, scope, match_key, policy, fixed_qty, set_by, confirmed) VALUES ('alice','item','000002','fixed_qty',2,'test',0)")
        conn.commit()
    finally:
        conn.close()
    result, _ = query(inventory, "list_review_queue", category="cable", uncovered_dormant=True, limit=100)
    assert result["total_count"] == 28
    assert "000001" not in {r["item_id"] for r in result["items"]}
    assert "000002" in {r["item_id"] for r in result["items"]}
    assert all(r["current_min"] is not None for r in result["items"])
    denied, ctx = query(inventory, uncovered_dormant=True, actor={"user": "view", "role": "viewer"})
    assert denied["response_status"] == "permission_denied"
    assert not ctx.sources


@pytest.mark.parametrize("intent", ["lookup", "advisory", "configure", "unknown", '"lookup"'])
def test_category_search_is_reachable_even_if_routed_to_settings(monkeypatch, inventory, intent):
    provider(monkeypatch, [call("search_items", category="cable"), Response(content="")], intent)
    conn = get_conn()
    try:
        result = run_chat(conn, "show me what the item that catogorise in cable", inventory, ACTOR)
    finally:
        conn.close()
    assert "30 matching rows" in result["answer"]
    assert result["response_status"] == "records_only"
    assert result["sources"]


def test_no_tool_selection_recovers_once_without_offering_writes(monkeypatch, inventory):
    stub = provider(monkeypatch, [Response(content="No category tool exists"),
                                 call("search_items", category="cable"), Response(content="")])
    conn = get_conn()
    try:
        result = run_agent(conn, "list cables", inventory, ACTOR)
    finally:
        conn.close()
    assert result["sources"] and result["answer"] != DONT_KNOW
    assert not stub.offered[1] & T.WRITE_TOOLS


def test_source_free_invented_answer_is_never_accepted(monkeypatch, inventory):
    provider(monkeypatch, [Response(content="The stock level is 999.")] * 2)
    conn = get_conn()
    try:
        result = run_chat(conn, "list cables", inventory, ACTOR)
    finally:
        conn.close()
    assert result["answer"] == DONT_KNOW
    assert result["response_status"] == "no_tool_selected"
    assert result["model_calls"] == 3  # classifier + first attempt + one recovery


def test_ambiguity_survives_source_grounding_guard(monkeypatch, client, inventory):
    score(client, inventory)
    provider(monkeypatch, [call("get_recommendation", item_id="000001"), Response(content="Pick one?")])
    conn = get_conn()
    try:
        result = run_chat(conn, "why item 000001", inventory, ACTOR)
    finally:
        conn.close()
    assert result["response_status"] == "clarification"
    assert "stockroom" in result["answer"] and "24" in result["answer"] and "25" in result["answer"]
    assert result["sources"] == []


@pytest.mark.parametrize(("kind", "question"), [
    ("quantity", "increase item 000001 a little"),
    ("item", "what is the current max"),
    ("spending", "how much did we spend on parts"),
    ("unsupported", "can you place an order for this part"),
])
def test_code_owned_clarification_cannot_carry_invented_facts(monkeypatch, inventory, kind, question):
    provider(monkeypatch, [call("clarify_request", kind=kind), Response(content="The correct Max is 999")])
    conn = get_conn()
    try:
        result = run_chat(conn, question, inventory, ACTOR)
    finally:
        conn.close()
    assert "999" not in result["answer"]
    assert "clarify_request" in [call["name"] for call in result["tool_calls"]]
    assert result["response_status"] in {"clarification", "unsupported"}
    assert not result["sources"]


def test_model_failure_after_retrieval_preserves_records(monkeypatch, inventory):
    provider(monkeypatch, [call("search_items", query="tubing"), RuntimeError("provider secret")])
    conn = get_conn()
    try:
        result = run_chat(conn, "find tubing", inventory, ACTOR)
    finally:
        conn.close()
    assert "009002" in result["answer"] and "provider secret" not in result["answer"]
    assert result["response_status"] == "records_only"


def test_model_outage_before_retrieval_is_not_reported_as_missing_data(monkeypatch, inventory):
    provider(monkeypatch, [RuntimeError("provider secret")])
    conn = get_conn()
    try:
        result = run_chat(conn, "find tubing", inventory, ACTOR)
    finally:
        conn.close()
    assert result["response_status"] == "model_error"
    assert "unavailable" in result["answer"] and "secret" not in result["answer"]


def test_read_error_and_malformed_arguments_are_distinct_from_no_results(inventory):
    class Broken:
        def execute(self, *args):
            raise RuntimeError("password=do-not-expose")
    ctx = T.ToolContext(Broken(), ACTOR, inventory, "find cable")
    data = json.loads(T.dispatch(ctx, "search_items", {"category": "cable"}))
    assert data["response_status"] == "data_error"
    assert "password" not in json.dumps(data)
    data = json.loads(T.dispatch(ctx, "search_items", ["cable"]))
    assert data["response_status"] == "invalid_arguments"


def test_parallel_tool_calls_cannot_exceed_budget(monkeypatch, inventory):
    provider(monkeypatch, [Response(tool_calls=[ToolCall(str(i), "search_items", {"query": "tubing"})
                                               for i in range(MAX_TOOL_CALLS + 4)]), Response(content="")])
    conn = get_conn()
    try:
        result = run_agent(conn, "find tubing", inventory, ACTOR)
    finally:
        conn.close()
    assert len(result["tool_calls"]) == MAX_TOOL_CALLS
    assert "009002" in result["answer"]


@pytest.mark.parametrize("stream", [False, True])
def test_operational_status_survives_stream_and_history(client, inventory, monkeypatch, stream):
    provider(monkeypatch, [call("clarify_request", kind="quantity"), Response(content="untrusted")], "propose")
    monkeypatch.setattr("app.routers.chat._predict_next_steps", lambda *args: None)
    response = client.post("/chat/stream" if stream else "/chat", headers=ENG,
                           json={"batch_id": inventory, "question": "increase item 000001 a little"})
    assert response.status_code == 200, response.text
    result = json.loads(response.text.splitlines()[-1]) if stream else response.json()
    saved = client.get(f"/chat/sessions/{result['session_id']}", headers=ENG).json()["turns"][0]
    assert saved["response_status"] == result["response_status"] == "clarification"
    assert saved["response_reason"] == result["response_reason"]
    assert saved["fallback"] is False
    assert "untrusted" not in saved["answer"]
    assert client.get(f"/chat/sessions/{result['session_id']}", headers=VIEWER).status_code == 404


def test_current_levels_available_before_scoring_and_missing_stays_unknown(inventory):
    data, _ = query(inventory, "get_current_values", item_id="000001", stockroom_id="25")
    assert data["current_max"] == 99
    conn = get_conn()
    try:
        row = conn.execute("SELECT payload FROM bom_rows WHERE batch_id=? AND item_id='009002'", (inventory,)).fetchone()
        payload = json.loads(row["payload"])
        payload["max_qty"] = ""
        conn.execute("UPDATE bom_rows SET payload=? WHERE batch_id=? AND item_id='009002'", (json.dumps(payload), inventory))
        conn.commit()
    finally:
        conn.close()
    data, _ = query(inventory, "get_current_values", item_id="009002")
    assert data["current_max"] is None
    ambiguous, _ = query(inventory, "get_current_values", item_id="000001")
    assert ambiguous["response_status"] == "clarification"


def test_ambiguous_stockrooms_are_redacted(monkeypatch, inventory):
    monkeypatch.setenv("LLM_REDACT_PROMPTS", "1")
    data, _ = query(inventory, "get_current_values", item_id="000001")
    assert "24" not in data["error"] and "25" not in data["error"]


def test_malformed_provider_json_does_not_become_an_unfiltered_search():
    from types import SimpleNamespace
    from app.llm.nyra import NyraProvider
    malformed = SimpleNamespace(id="bad", function=SimpleNamespace(name="search_items", arguments='{broken'))
    wire = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[malformed]))])
    model = object.__new__(NyraProvider)
    model.model = "test"
    model._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: wire)))
    response = model.chat([], [])
    assert response.tool_calls[0].arguments is None
    data = json.loads(T.dispatch(T.ToolContext(None, ACTOR, 1, "find cable"), "search_items", response.tool_calls[0].arguments))
    assert data["response_status"] == "invalid_arguments"


def test_partial_source_from_failed_read_cannot_ground_answer(monkeypatch, inventory):
    spec, _ = T.REGISTRY["search_items"]
    def broken(ctx, **kwargs):
        ctx.sources.append({"type": "bom_rows", "batch_id": inventory})
        raise RuntimeError("query failed")
    monkeypatch.setitem(T.REGISTRY, "search_items", (spec, broken))
    data, ctx = query(inventory)
    assert data["response_status"] == "data_error" and not ctx.sources


def test_unoffered_tool_reports_a_routing_error_not_missing_data(monkeypatch, inventory):
    provider(monkeypatch, [call("run_assist"), Response(content="Done")])
    conn = get_conn()
    try:
        result = run_chat(conn, "show cable items", inventory, ACTOR)
    finally:
        conn.close()
    assert result["response_status"] == "invalid_arguments"
    assert result["answer"] != DONT_KNOW and not result["sources"]


@pytest.mark.parametrize("question,expected", [
    ("show me what the item that catogorise in cable", {"category": "cable"}),
    ("How many cables? Show the first 5.", {"category": "cable", "limit": 5}),
    ("Find items whose description contains tubing.", {"query": "tubing"}),
])
def test_offline_provider_preserves_discovery_filters(client, inventory, question, expected):
    result = client.post("/chat", headers=ENG, json={"batch_id": inventory, "question": question}).json()
    assert result["tool_calls"][0]["name"] == "search_items"
    assert result["tool_calls"][0]["args"] == expected
    assert result["sources"] and result["answer"] != DONT_KNOW


def test_route_filter_uses_the_engine_route_names(client, inventory):
    score(client, inventory)
    conn = get_conn()
    try:
        conn.execute("UPDATE recommendation_result SET route='active' WHERE batch_id=? AND item_id='009002'", (inventory,))
        conn.commit()
    finally:
        conn.close()
    result, _ = query(inventory, route="active")
    assert result["total_count"] == 1 and result["items"][0]["item_id"] == "009002"


@pytest.mark.parametrize("batch_id", [True, 0, -1, "13"])
def test_invalid_model_workspace_does_not_silently_select_another(inventory, batch_id):
    result, ctx = query(inventory, batch_id=batch_id)
    assert result["response_status"] == "invalid_arguments" and not ctx.sources


def test_narration_failure_after_rop_proposal_keeps_staged_fields(monkeypatch, client, inventory):
    score(client, inventory)
    provider(monkeypatch, [call("propose_change", item_id="009002", proposed_rop=3), RuntimeError("model failed")], "propose")
    conn = get_conn()
    try:
        result = run_chat(conn, "set item 009002 ROP to 3", inventory, ACTOR)
        assert conn.execute("SELECT COUNT(*) AS n FROM pending_change").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM review_history").fetchone()["n"] == 0
    finally:
        conn.close()
    assert result["response_status"] == "records_only"
    assert "ROP 3" in result["answer"] and "Max None" not in result["answer"]
    assert "has not been applied" in result["answer"]
