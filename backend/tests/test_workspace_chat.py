"""Page-aware answers and editable settings drafts, across both chat endpoints."""

import json

import pytest

from conftest import ADMIN, ENG, VIEWER, upload


def page(path="/config/dormant", **updates):
    return {"path": path, "title": "Dormant stocking rules" if path.endswith("dormant") else "Rules & criticality",
            "active_section": "Dormant rules", **updates}


def ask(client, question, context=None, headers=ENG, stream=False, **extra):
    response = client.post("/chat/stream" if stream else "/chat", headers=headers,
                           json={"question": question, "page_context": context or page(), **extra})
    assert response.status_code == 200, response.text
    if stream:
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "complete", events[-1]
        return events[-1]
    return response.json()


@pytest.mark.parametrize("stream", [False, True])
def test_pasted_rules_fill_every_row_without_saving(client, stream):
    before = client.get("/config/dormant-rules", headers=ENG).json()
    question = "Fill these parts:\nitem_id,policy,quantity\n000123,hold_current,\nABC-12,fixed_qty,4\n000456,zero,"
    result = ask(client, question, stream=stream)
    assert result["intent"] == "configure"
    assert result["batch_id"] is None
    draft, = result["page_actions"]
    assert draft["rows"] == [
        {"scope": "item", "match_key": "000123", "policy": "hold_current", "fixed_qty": None},
        {"scope": "item", "match_key": "ABC-12", "policy": "fixed_qty", "fixed_qty": 4},
        {"scope": "item", "match_key": "000456", "policy": "zero", "fixed_qty": None},
    ]
    assert client.get("/config/dormant-rules", headers=ENG).json() == before
    saved = client.get(f"/chat/sessions/{result['session_id']}", headers=ENG).json()
    assert saved["turns"][0]["question"] == question
    # The normal page submission still works and retains its two-person rule.
    for row in draft["rows"]:
        response = client.post("/config/dormant-rules", json=row, headers=ENG)
        assert response.status_code == 200, response.text
    rows = client.get("/config/dormant-rules", headers=ENG).json()["rules"]
    added = [row for row in rows if row["match_key"] in {"000123", "ABC-12", "000456"}]
    assert len(added) == 3 and all(not row["confirmed"] for row in added)


@pytest.mark.parametrize("role,expected", [(ENG, False), (VIEWER, False), (ADMIN, True)])
def test_thresholds_require_admin_and_only_fill(client, role, expected):
    before = client.get("/config/rules", headers=ADMIN).json()
    result = ask(client, "high_cost_threshold=1800 autoclear_reliable=on version=demo-2",
                 page("/config", active_section="Thresholds"), headers=role)
    assert bool(result["page_actions"]) is expected
    if expected:
        draft = result["page_actions"][0]
        assert draft["updates"] == {"high_cost_threshold": 1800, "autoclear_reliable": True}
        assert draft["rule_version"] == "demo-2"
    assert client.get("/config/rules", headers=ADMIN).json() == before


def test_machine_criticality_bulk_draft(client):
    result = ask(client, "machine_type\tcriticality\nKnS TCX3\tHigh\nASM Phoenix\tMedium", page("/config"))
    assert result["page_actions"][0]["rows"] == [
        {"pattern": "KnS TCX3", "criticality": "High", "service_level_target": None},
        {"pattern": "ASM Phoenix", "criticality": "Medium", "service_level_target": None}]


def test_mixed_policies_in_prose_are_kept_separate(client):
    result = ask(client, "Hold current for 000111; keep 000222 at 4; zero 000333")
    assert [row["policy"] for row in result["page_actions"][0]["rows"]] == ["hold_current", "fixed_qty", "zero"]
    ambiguous = ask(client, "hold current for 000111 keep 000222 at 4")
    assert not ambiguous["page_actions"]


def test_page_context_resets_batch_and_answers_visible_section(client, synth_csv):
    batch = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch}", headers=ENG)
    first = ask(client, "current max for this item", page(
        f"/batches/{batch}/items/100005", batch_id=batch, item_id="100005", title="Item 100005"))
    assert "100005" in first["answer"]
    result = ask(client, "Where am I looking now?", page("/config", active_section="Machine criticality",
        selected_text="The engine reads confirmed entries only."), session_id=first["session_id"])
    assert result["batch_id"] is None
    assert "Machine criticality" in result["answer"]
    assert "confirmed entries only" in result["answer"]
    assert any(source["type"] == "page_context" for source in result["sources"])


def test_mismatched_batch_is_rejected(client):
    response = client.post("/chat", headers=ENG, json={"question": "where am I?",
        "batch_id": 42, "page_context": page()})
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/", "/chat", "/config", "/config/dormant"])
def test_page_questions_work_without_uploaded_batches(client, path):
    result = ask(client, "What can I do on this page?", page(path))
    assert result["sources"] and result["batch_id"] is None
    assert path in result["answer"]


def test_navigation_is_an_allowlisted_browser_action(client):
    result = ask(client, "Open dormant rules", page("/"))
    assert result["page_actions"] == [{"kind": "navigate", "path": "/config/dormant"}]


def tool_context(client, question="hold current", role="engineer", path="/config/dormant"):
    from app.agent.tools import ToolContext
    from app.db import get_conn
    return ToolContext(get_conn(), {"user": "alice", "role": role}, None, question,
                       page_context=page(path))


@pytest.mark.parametrize("args", [
    {"section": "dormant_rules", "rows": [{"scope": "item", "match_key": "001234", "policy": "fixed_qty", "fixed_qty": 3}]},
    {"section": "dormant_rules", "rows": [{"scope": "item", "match_key": "001234", "policy": "fixed_qty"}]},
    {"section": "dormant_rules", "rows": [{"scope": "item", "match_key": "001234", "policy": "hold_current", "confirmed": True}]},
    {"section": "dormant_rules", "rows": [{"scope": "item", "match_key": "001234", "policy": "hold_current"}] * 2},
])
def test_invalid_or_invented_draft_stages_nothing(client, args):
    from app.agent.tools import dispatch
    ctx = tool_context(client)
    try:
        result = json.loads(dispatch(ctx, "fill_settings_form", args))
        assert "error" in result and not ctx.page_actions
    finally:
        ctx.conn.close()


@pytest.mark.parametrize("role,path", [("viewer", "/config/dormant"), ("engineer", "/")])
def test_tool_boundary_checks_role_and_page(client, role, path):
    from app.agent.tools import dispatch
    ctx = tool_context(client, role=role, path=path)
    try:
        result = json.loads(dispatch(ctx, "fill_settings_form", {"section": "dormant_rules", "rows": [
            {"scope": "item", "match_key": "001234", "policy": "hold_current"}]}))
        assert "error" in result and not ctx.page_actions
        assert "error" in json.loads(dispatch(ctx, "navigate_to_page", {"path": "//example.com"}))
    finally:
        ctx.conn.close()


def test_large_part_dump_and_bounded_context(client):
    result = ask(client, "hold current for these parts\n" + "\n".join(str(100000 + i) for i in range(400)))
    assert len(result["page_actions"][0]["rows"]) == 400
    response = client.post("/chat", headers=ENG, json={"question": "hi", "page_context": page(visible_text="x" * 8001)})
    assert response.status_code == 422


def test_context_reaches_branch_and_redacts_free_text(client, monkeypatch):
    from app.llm.echo import EchoProvider
    recorded = []

    class RecordingProvider(EchoProvider):
        def chat(self, messages, tools):
            recorded.extend(message.content for message in messages)
            return super().chat(messages, tools)

    monkeypatch.setattr("app.agent.loop.get_provider", lambda: RecordingProvider())
    first = ask(client, "Where am I?", page("/", title="Batches"))
    ask(client, "What can I do on this page?", page("/config", active_section="Thresholds"), session_id=first["session_id"])
    assert any("Where am I?" == message for message in recorded)
    assert any('"active_section": "Thresholds"' in message for message in recorded)
    monkeypatch.setenv("LLM_REDACT_PROMPTS", "1")
    recorded.clear()
    ask(client, "Where am I?", page(visible_text="PRIVATE_SENTINEL", selected_text="PRIVATE_SENTINEL",
                                   form_state={"pattern": "PRIVATE_SENTINEL"}))
    assert all("PRIVATE_SENTINEL" not in message for message in recorded)
