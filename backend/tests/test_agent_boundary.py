"""The safety properties. These are the point of the design, so they get
explicit tests rather than being implied by the happy path.

  1. chat can stage, but cannot decide
  2. a staged proposal is invisible to the WINGS export until a human confirms
  3. the agent cannot invent a quantity
  4. no retrieved source -> "I don't know"
  5. read-only roles are never even offered the write tool
  6. mem0 stays off and uninvoked by default
"""

import json
import sqlite3

import pytest
from conftest import ADMIN, ENG, SENIOR, VIEWER, upload


def scored_batch(client, csv_bytes):
    b = upload(client, csv_bytes).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b


def rows(db_file, sql):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# -- 1 & 2: the write boundary -------------------------------------------

def test_chat_stages_but_never_decides(client, synth_csv, db_file):
    scored_batch(client, synth_csv)

    r = client.post("/chat", json={"question": "increase item 100005 max to 3"},
                    headers=ENG)
    assert r.status_code == 200, r.text

    pending = rows(db_file, "SELECT item_id, proposed_max, status FROM pending_change")
    assert pending == [("100005", 3, "pending")], pending

    # The decisive table must be untouched. This is the property that keeps an
    # LLM parse out of the audit trail and out of WINGS.
    assert rows(db_file, "SELECT COUNT(*) FROM review_history")[0][0] == 0


def export_csv(client, batch_id):
    """/export/wings returns CSV, with counts in X- headers."""
    r = client.get(f"/export/wings?batch_id={batch_id}", headers=ENG)
    assert r.status_code == 200, r.text
    return r.text, r.headers


def test_staged_change_is_invisible_to_export_until_confirmed(
        client, synth_csv, db_file):
    b = scored_batch(client, synth_csv)
    client.post("/chat", json={"question": "set item 100005 max to 3"},
                headers=ENG)

    body, _ = export_csv(client, b)
    assert "100005" not in body

    pid = rows(db_file, "SELECT pending_id FROM pending_change")[0][0]
    conf = client.post("/review/100005/confirm-pending",
                       json={"pending_id": pid, "decision": "override",
                             "final_max": 3, "final_rop": 2, "final_min": 1},
                       headers=ENG).json()
    assert conf["requires_senior_approval"] is True
    assert conf["status"] == "awaiting_senior"

    # An override still needs a second person -- confirming from chat does not
    # bypass the approval chain.
    body, hdrs = export_csv(client, b)
    assert "100005" not in body
    assert int(hdrs["X-Awaiting-Senior"]) >= 1

    client.post(f"/review/100005/approve?batch_id={b}", headers=SENIOR)
    body, _ = export_csv(client, b)
    assert "100005" in body
    line = next(ln for ln in body.splitlines() if ln.startswith("100005"))
    # item,stockroom,cur_max,cur_rop,cur_min,new_max,...
    assert line.split(",")[5] == "3", line


def test_confirm_is_recorded_as_a_normal_review(client, synth_csv, db_file):
    """A chat-originated decision must be indistinguishable downstream."""
    scored_batch(client, synth_csv)
    client.post("/chat", json={"question": "set item 100005 max to 3"},
                headers=ENG)
    pid = rows(db_file, "SELECT pending_id FROM pending_change")[0][0]
    client.post("/review/100005/confirm-pending",
                json={"pending_id": pid, "final_max": 3, "final_rop": 2,
                      "final_min": 1}, headers=ENG)

    hist = client.get("/history/100005", headers=VIEWER).json()["reviews"]
    assert len(hist) == 1
    assert hist[0]["reviewer"] == "alice"          # the human, not the model
    assert hist[0]["rule_version"] == "0.2.0-tcb"
    assert rows(db_file, "SELECT status FROM pending_change")[0][0] == "confirmed"


def test_discard_leaves_no_decision(client, synth_csv, db_file):
    scored_batch(client, synth_csv)
    client.post("/chat", json={"question": "set item 100005 max to 3"},
                headers=ENG)
    pid = rows(db_file, "SELECT pending_id FROM pending_change")[0][0]
    client.post(f"/review/100005/discard-pending?pending_id={pid}", headers=ENG)
    assert rows(db_file, "SELECT status FROM pending_change")[0][0] == "discarded"
    assert rows(db_file, "SELECT COUNT(*) FROM review_history")[0][0] == 0


def test_confirm_enforces_max_rop_min_ordering(client, synth_csv, db_file):
    """A proposal that sets only `max` can otherwise merge below the existing
    ROP. The console validates this on input; the confirm path has to validate
    the merged result."""
    scored_batch(client, synth_csv)
    client.post("/chat", json={"question": "set item 100005 max to 3"},
                headers=ENG)
    pid = rows(db_file, "SELECT pending_id FROM pending_change")[0][0]

    bad = client.post("/review/100005/confirm-pending",
                      json={"pending_id": pid, "final_max": 1,
                            "final_rop": 5, "final_min": 0}, headers=ENG)
    assert bad.status_code == 400
    assert "max >= rop >= min" in bad.json()["detail"]
    assert rows(db_file, "SELECT COUNT(*) FROM review_history")[0][0] == 0
    assert rows(db_file, "SELECT status FROM pending_change")[0][0] == "pending"


# -- 3: the agent cannot invent a number ----------------------------------

def test_propose_change_rejects_unstated_numbers(client, synth_csv, db_file):
    """PRD section 8: the LLM never generates Min/Max/ROP. Enforced in code --
    a value absent from the engineer's own message is refused."""
    from app.agent.tools import ToolContext, ToolError, propose_change
    from app.db import get_conn

    scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        ctx = ToolContext(conn=conn, actor={"user": "alice", "role": "engineer"},
                          batch_id=1, question="please bump item 100005 a bit")
        with pytest.raises(ToolError, match="does not appear"):
            propose_change(ctx, item_id="100005", proposed_max=7)
        assert conn.execute(
            "SELECT COUNT(*) c FROM pending_change").fetchone()["c"] == 0
    finally:
        conn.close()


def test_vague_instruction_stages_nothing(client, synth_csv, db_file):
    scored_batch(client, synth_csv)
    client.post("/chat", json={"question": "increase item 100005 a little"},
                headers=ENG)
    assert rows(db_file, "SELECT COUNT(*) FROM pending_change")[0][0] == 0


def test_unknown_item_is_refused(client, synth_csv, db_file):
    """The roster rotates -- an item may simply not be in this month's extract."""
    scored_batch(client, synth_csv)
    r = client.post("/chat", json={"question": "set item 999999 max to 3"},
                    headers=ENG).json()
    assert rows(db_file, "SELECT COUNT(*) FROM pending_change")[0][0] == 0
    assert "know" in r["answer"].lower() or "not" in r["answer"].lower()


# -- 4: no source -> no answer --------------------------------------------

def test_no_source_means_dont_know(client, synth_csv):
    scored_batch(client, synth_csv)
    r = client.post("/chat", json={"question": "what is the meaning of life"},
                    headers=VIEWER).json()
    assert "I don't know" in r["answer"]
    assert r["sources"] == []


def _stream_events(client, question, headers, **extra):
    with client.stream("POST", "/chat/stream",
                       json={"question": question, **extra}, headers=headers) as response:
        assert response.status_code == 200, response.text
        return [json.loads(line) for line in response.iter_lines() if line]


def test_chat_stream_exposes_grounded_progress(client, synth_csv, db_file):
    scored_batch(client, synth_csv)
    events = _stream_events(client, "why item 100007?", VIEWER)
    kinds = [event["type"] for event in events]

    assert kinds[0] == "request"
    assert "model_start" in kinds
    assert any(event.get("name") == "get_recommendation"
               for event in events if event["type"] == "tool_start")
    assert any(event.get("status") == "ok"
               for event in events if event["type"] == "tool_result")
    assert "answer_delta" in kinds
    assert kinds[-1] == "complete"
    assert "100007" in events[-1]["answer"]
    assert rows(db_file, "SELECT COUNT(*) FROM conversation_turn")[0][0] == 1


def test_chat_stream_reports_fallback_reason(client, synth_csv):
    scored_batch(client, synth_csv)
    events = _stream_events(client, "what is the meaning of life", VIEWER)

    fallback = next(event for event in events if event["type"] == "fallback")
    assert "No authoritative data source" in fallback["reason"]
    assert "I don't know" in events[-1]["answer"]
    assert events[-1]["sources"] == []


def test_only_first_turn_streams_model_generated_next_steps(
        client, synth_csv, monkeypatch):
    scored_batch(client, synth_csv)
    prediction = {
        "intent": "Inspect the highest-exposure records",
        "suggestions": [
            {"label": "Filter high risk", "prompt": "Show high-risk review items"},
            {"label": "Summarize batch", "prompt": "Summarize the current batch"},
        ],
    }
    monkeypatch.setattr(
        "app.routers.chat._predict_next_steps", lambda _question, _answer: prediction)

    first = _stream_events(client, "top exposure items", ENG)
    kinds = [event["type"] for event in first]
    assert "prediction_start" in kinds
    assert next(event for event in first
                if event["type"] == "prediction_complete")["suggestions"] == prediction["suggestions"]
    assert first[-1]["next_steps"] == prediction

    second = _stream_events(
        client,
        prediction["suggestions"][0]["prompt"],
        ENG,
        session_id=first[-1]["session_id"],
    )
    assert not any(event["type"].startswith("prediction_") for event in second)
    assert second[-1]["next_steps"] is None


def test_chat_history_restores_complete_user_session(client):
    first = client.post(
        "/chat", json={"question": "first saved question"}, headers=ENG).json()
    second = client.post(
        "/chat",
        json={"question": "second saved question", "session_id": first["session_id"]},
        headers=ENG,
    ).json()
    client.post(
        "/chat",
        json={"question": "another user's question", "session_id": first["session_id"]},
        headers=VIEWER,
    )

    sessions = client.get("/chat/sessions", headers=ENG).json()["sessions"]
    assert sessions == [{
        "session_id": first["session_id"],
        "title": "first saved question",
        "updated_at": sessions[0]["updated_at"],
        "turn_count": 2,
    }]

    saved = client.get(
        f"/chat/sessions/{first['session_id']}", headers=ENG).json()
    assert saved["session_id"] == second["session_id"]
    assert [turn["question"] for turn in saved["turns"]] == [
        "first saved question", "second saved question"]
    assert all(isinstance(turn["tool_calls"], list) for turn in saved["turns"])
    assert client.get(
        f"/chat/sessions/{first['session_id']}", headers=SENIOR).status_code == 404


# -- 5: read-only roles never see the write tool ---------------------------

def test_viewer_is_not_offered_the_write_tool(client, synth_csv, db_file):
    scored_batch(client, synth_csv)
    r = client.post("/chat", json={"question": "set item 100005 max to 3"},
                    headers=VIEWER)
    assert r.status_code == 200
    assert rows(db_file, "SELECT COUNT(*) FROM pending_change")[0][0] == 0


def test_tool_specs_exclude_writes_when_disallowed():
    from app.agent import tools as T
    names = {s.name for s in T.specs(allow_writes=False)}
    assert "propose_change" not in names
    assert "get_recommendation" in names
    assert {s.name for s in T.specs(allow_writes=True)} - names == {"propose_change"}


def test_assist_cannot_write_decisions_or_recommendations(
        client, synth_csv, db_file):
    """The advisory layer reads and annotates; it never sizes or decides.

    Replaces the same assertion against the LangGraph triage, removed
    2026-09-04 -- nothing called it. assist is the only advisory writer left.
    """
    batch_id = scored_batch(client, synth_csv)
    before = rows(db_file, "SELECT * FROM recommendation_result ORDER BY item_id")

    r = client.post(f"/assist/run?batch_id={batch_id}", headers=ENG)
    assert r.status_code == 200, r.text

    assert rows(db_file, "SELECT COUNT(*) FROM review_history")[0][0] == 0
    assert rows(db_file, "SELECT * FROM recommendation_result ORDER BY item_id") == before
    columns = {r[1] for r in rows(db_file, "PRAGMA table_info(assist_result)")}
    assert not columns & {"new_max", "new_rop", "new_min",
                          "final_max", "final_rop", "final_min"}


def test_read_only_agent_rejects_unoffered_write_call(
        client, synth_csv, db_file, monkeypatch):
    from app.agent.loop import run_agent
    from app.db import get_conn
    from app.llm.provider import Response, ToolCall

    class BadProvider:
        name = "bad"
        model = "bad-stub"

        def chat(self, messages, tools):
            return Response(tool_calls=[ToolCall(
                "c1", "propose_change",
                {"item_id": "100005", "proposed_max": 3})])

    batch_id = scored_batch(client, synth_csv)
    monkeypatch.setattr("app.agent.loop.get_provider", lambda: BadProvider())
    conn = get_conn()
    try:
        run_agent(conn, "set item 100005 max to 3", batch_id,
                  {"user": "alice", "role": "engineer"},
                  allow_writes=False, max_model_calls=1)
    finally:
        conn.close()
    assert rows(db_file, "SELECT COUNT(*) FROM pending_change")[0][0] == 0


# -- 6: everything is logged ----------------------------------------------

def test_every_turn_is_logged_verbatim(client, synth_csv, db_file):
    """conversation_turn is the future ML label corpus, so the engineer's own
    words must be stored unmodified, not a paraphrase."""
    scored_batch(client, synth_csv)
    q = "why item 100007?"
    client.post("/chat", json={"question": q}, headers=ENG)

    turns = rows(db_file, "SELECT question, answer, provider, tool_calls "
                          "FROM conversation_turn")
    assert len(turns) == 1
    assert turns[0][0] == q                       # verbatim, not normalised
    assert turns[0][2] == "echo"
    assert "get_recommendation" in turns[0][3]

    entities = {r[0] for r in rows(db_file, "SELECT DISTINCT entity FROM audit_log")}
    assert "chat" in entities


def test_source_utterance_is_preserved_on_staging(client, synth_csv, db_file):
    scored_batch(client, synth_csv)
    q = "we burned three last quarter, set item 100005 max to 3"
    client.post("/chat", json={"question": q}, headers=ENG)
    got = rows(db_file, "SELECT source_utterance, parsed_by FROM pending_change")
    assert got[0][0] == q
    assert got[0][1].startswith("echo:")           # provenance of the parse


# -- 7: mem0 is off and not imported --------------------------------------

def test_mem0_disabled_by_default(client, synth_csv):
    import sys

    from app.memory import enabled, search_memory
    assert enabled() is False
    assert search_memory("anything", user_id="alice") == []
    assert "mem0" not in sys.modules, "mem0 must not be imported when disabled"


def test_redaction_covers_what_actually_leaves_the_process(
        client, synth_csv, db_file, monkeypatch):
    """LLM_REDACT_PROMPTS has to mask the tool RESULT, not just the arguments.

    The result string becomes a `tool` message and goes to the provider
    verbatim, so with an external endpoint it is the only thing standing
    between PRD 5.1 fields and a third party.
    """
    from app.agent.tools import ToolContext, dispatch
    from app.db import get_conn

    b = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        ctx = ToolContext(conn=conn, actor={"user": "alice", "role": "engineer"},
                          batch_id=b, question="context for item 100005")

        monkeypatch.setenv("LLM_REDACT_PROMPTS", "1")
        masked = dispatch(ctx, "get_current_values", {"item_id": "100005"})
        assert '"stockroom_id": "[redacted]"' in masked, masked

        monkeypatch.setenv("LLM_REDACT_PROMPTS", "0")
        plain = dispatch(ctx, "get_current_values", {"item_id": "100005"})
        assert '"stockroom_id": "24"' in plain, plain
    finally:
        conn.close()


def test_health_reports_wiring(client):
    """/health must make it obvious which backend a process is on -- the same
    image runs on SQLite under test and Supabase in production."""
    h = client.get("/health").json()
    assert h["database"] == "sqlite (test-only)"
    assert h["llm_provider"] == "echo"
    assert h["mem0_enabled"] is False
