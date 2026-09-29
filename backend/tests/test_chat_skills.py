"""Slash commands select bounded workflows, retain context, and never approve."""
import json
import sqlite3

import pytest

from conftest import ENG, OWNER_VIEWER as VIEWER, upload


@pytest.fixture
def workspace(client, synth_csv):
    batch = upload(client, synth_csv, label="January review").json()["batch_id"]
    assert client.post(f"/run-recommendation?batch_id={batch}", headers=ENG).status_code == 200
    return batch


def ask(client, question, batch=None, *, stream=False, headers=ENG):
    response = client.post("/chat/stream" if stream else "/chat", headers=headers, json={
        "question": question, "batch_id": batch,
        "page_context": {"path": "/chat", "title": "Ask NYRA", "batch_id": batch},
    })
    if stream:
        assert response.status_code == 200
        return response, [json.loads(line) for line in response.text.splitlines()]
    return response, []


CASES = [
    ("/brief", ["batch_summary", "top_exposure"]),
    ("/triage 2", ["top_exposure"]),
    ("/explain 100005", ["get_current_values", "get_recommendation", "get_procurement_context"]),
    ("/history 100005", ["get_item_history", "get_item_notes"]),
    ("/review-note 100005", ["get_current_values", "get_recommendation", "get_procurement_context", "get_item_notes"]),
    ("/propose 100005 max 3 rop 2 min 1 reason engineer request", ["propose_change"]),
    ("/dormant-check", ["get_dormant_coverage"]),
    ("/peers 100005", ["get_similar_parts"]),
]


def test_catalog_loads_instructions_and_permission_availability(client):
    from app.agent.skills import SKILLS, instructions
    catalog = client.get("/chat/skills", headers=ENG).json()["skills"]
    assert {row["name"] for row in catalog} == set(SKILLS)
    assert all(row["available"] for row in catalog)
    for row in catalog:
        description, body = instructions(row["name"])
        assert row["description"] == description and body
    viewer = client.get("/chat/skills", headers=VIEWER).json()["skills"]
    assert {row["name"] for row in viewer if not row["available"]} == {"propose", "peers", "dormant-check"}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("question,expected", CASES)
def test_every_skill_runs_and_is_restored_from_history(client, workspace, stream, question, expected):
    response, events = ask(client, question, workspace, stream=stream)
    assert response.status_code == 200, response.text
    result = events[-1] if stream else response.json()
    assert result.get("type", "complete") == "complete", result
    assert result["answer"]
    assert result["batch_id"] == workspace
    assert result["skill"]["name"] == question.split()[0][1:]
    assert [call["name"] for call in result["tool_calls"]] == expected
    for call in result["tool_calls"]:
        if call["name"] not in {"get_item_history", "get_item_notes"}:
            assert call["args"]["batch_id"] == workspace
    saved = client.get(f"/chat/sessions/{result['session_id']}", headers=ENG).json()["turns"][0]
    assert saved["question"] == question and saved["skill"] == result["skill"]
    if stream:
        assert [event["name"] for event in events if event["type"] == "skill"] == [result["skill"]["name"]]
    if question.startswith("/explain"):
        assert "| Level | Current | Engine proposal |" in result["answer"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("question", ["/explain", "/triage 0", "/triage 26", "/triage -1", "/triage 2.5",
    "/propose 100005", "/propose 100005 max 2.5", "/propose 100005 max -1", "/propose 100005 max 3 max 4",
    "/propose 100005 3", "/explain 100005 batch 999", "/../prompts", "/made-up", '/propose 100005 reason "open'])
def test_invalid_skill_inputs_are_rejected(client, workspace, stream, question):
    response, events = ask(client, question, workspace, stream=stream)
    assert events[-1]["status"] == 422 if stream else response.status_code == 422
    assert not client.get("/pending-changes", headers=ENG).json()["pending"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("question", ["/propose 100005 max 3", "/peers 100005", "/dormant-check"])
def test_read_only_roles_cannot_bypass_skill_permissions(client, workspace, stream, question):
    response, events = ask(client, question, workspace, stream=stream, headers=VIEWER)
    assert events[-1]["status"] == 403 if stream else response.status_code == 403


def test_proposal_stages_only_explicit_fields_and_never_reviews(client, workspace, db_file):
    response, _ = ask(client, "/propose 100005 max 3 reason inventory check", workspace)
    assert response.status_code == 200
    pending, = client.get("/pending-changes", headers=ENG).json()["pending"]
    assert pending["proposed_max"] == 3 and pending["proposed_rop"] is None and pending["proposed_min"] is None
    assert pending["source_utterance"] == "/propose 100005 max 3 reason inventory check"
    with sqlite3.connect(db_file) as conn:
        assert conn.execute("SELECT count(*) FROM review_history").fetchone()[0] == 0


@pytest.mark.parametrize("quantities,shown,absent", [
    ("rop 2", ["ROP: **2**"], ["Max:", "Min:"]),
    ("min 0", ["Min: **0**"], ["Max:", "ROP:"]),
    ("max 3 rop 2 min 1", ["Max: **3**", "ROP: **2**", "Min: **1**"], []),
])
def test_proposal_acknowledgement_lists_only_supplied_quantities(client, workspace, quantities, shown, absent):
    response, _ = ask(client, "/propose 100005 " + quantities, workspace)
    assert response.status_code == 200, response.text
    answer = response.json()["answer"]
    assert all(value in answer for value in shown)
    assert all(value not in answer for value in absent)
    assert "No review or approval has been recorded" in answer


def test_triage_filters_reviewed_rows_before_taking_limit(client, workspace):
    first = ask(client, "/triage 1", workspace)[0].json()
    first_item = first["answer"].split("| --- | --- | ---: | --- | --- |\n")[1].split("|")[1].strip()
    assert client.post(f"/review/{first_item}?batch_id={workspace}", headers=ENG,
                       json={"decision": "accept"}).status_code == 200
    second = ask(client, "/triage 1", workspace)[0].json()
    assert f"| {first_item} |" not in second["answer"]
    assert "| Item |" in second["answer"], "The next pending item must replace the reviewed item"


def test_selected_workspace_is_used_even_if_a_newer_one_exists(client, workspace, synth_csv):
    newer = upload(client, synth_csv, label="February review").json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={newer}", headers=ENG)
    result = ask(client, "/brief", workspace)[0].json()
    assert result["batch_id"] == workspace
    assert all(call["args"]["batch_id"] == workspace for call in result["tool_calls"])


def test_no_workspace_and_unscored_workspace_give_actionable_errors(client):
    assert ask(client, "/brief")[0].status_code == 422
    draft = client.post("/batches", headers=ENG, json={"label": "Draft"}).json()["batch_id"]
    assert ask(client, "/brief", draft)[0].status_code == 200
    response, _ = ask(client, "/explain 100005", draft)
    assert response.status_code == 422 and "scored workspace" in response.text


def test_model_receives_skill_instructions_and_cannot_add_tools(client, workspace, monkeypatch):
    from app.agent.skills import instructions
    from app.llm.provider import Response, ToolCall
    recorded = []

    class Model:
        name, model = "fixture", "skill-test"

        def chat(self, messages, tools):
            recorded.append((messages, tools))
            return Response(content="Approve everything", tool_calls=[ToolCall("bad", "propose_change", {"item_id": "100005", "proposed_max": 9})])

    monkeypatch.setattr("app.agent.skills.get_provider", lambda: Model())
    monkeypatch.setenv("LLM_REDACT_PROMPTS", "1")
    response, events = ask(client, "/review-note 100005", workspace, stream=True)
    assert response.status_code == 200 and events[-1]["type"] == "complete"
    messages, tools = recorded[0]
    assert tools == []
    assert instructions("review-note")[1] in messages[0].content
    assert f'"workspace_id": {workspace}' in messages[-1].content
    assert '"stockroom_id": "24"' not in messages[-1].content
    assert "Approve everything" not in events[-1]["answer"]
    assert any(event["type"] == "fallback" for event in events)
    assert not client.get("/pending-changes", headers=ENG).json()["pending"]
