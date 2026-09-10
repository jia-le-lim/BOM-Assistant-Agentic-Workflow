"""The graph routes to a branch, and a branch cannot exceed its tool subset.

test_agent_boundary.py owns the safety properties and test_chat_routing.py owns
flat intent->tool routing. This file owns the property neither can see: that a
question reaches the right BRANCH, and that a branch is offered only its own
tools -- which is the whole reason the router is a graph rather than one flat
tool list.

Two of those properties are safety properties in their own right and get
hostile providers rather than happy-path stubs:

  * a read-only role routed at a write branch is downgraded, not served
  * an action card is staged, never executed -- `review_history` stays empty
"""

import json
import sqlite3

from conftest import ENG, VIEWER, upload


def scored_batch(client, csv_bytes):
    b = upload(client, csv_bytes).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b


def rows(db_file, sql, params=()):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def tools_used(db_file) -> list[str]:
    """Which tools the last turn ran, read from the audit record itself."""
    conn = sqlite3.connect(db_file)
    try:
        raw = conn.execute("SELECT tool_calls FROM conversation_turn "
                           "ORDER BY turn_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return [c["name"] for c in json.loads(raw[0])] if raw else []


# -- the subsetting contract ------------------------------------------------

def test_every_intent_group_names_real_tools():
    """A typo in INTENT_TOOLS would silently shrink a branch's tool set."""
    from app.agent import tools as T

    for intent, names in T.INTENT_TOOLS.items():
        unknown_names = sorted(n for n in names if n not in T.REGISTRY)
        assert not unknown_names, f"{intent} names missing tools: {unknown_names}"


def test_every_tool_is_reachable_from_some_branch():
    """A tool in no branch is a tool the chat layer can never call."""
    from app.agent import tools as T

    reachable = set().union(*T.INTENT_TOOLS.values())
    assert set(T.REGISTRY) - reachable == set()


def test_branch_is_offered_only_its_own_tools(client, synth_csv, monkeypatch):
    """The point of the graph. A branch that could see every tool would be the
    flat loop again, and `propose_change` would be one misclassification away
    from every read question."""
    from app.agent import graph as G
    from app.agent import tools as T
    from app.db import get_conn
    from app.llm.provider import Response

    scored_batch(client, synth_csv)
    seen: list[set[str]] = []

    class RecordingProvider:
        name, model = "recording", "recording-stub"

        def chat(self, messages, tools):
            if tools:
                seen.append({t.name for t in tools})
            return Response(content="", model=self.model, provider=self.name)

    monkeypatch.setattr("app.agent.loop.get_provider",
                        lambda: RecordingProvider())

    conn = get_conn()
    try:
        for intent in ("lookup", "assist", "advisory", "propose", "action"):
            seen.clear()
            G._branch({"conn": conn, "question": "anything at all",
                       "batch_id": None,
                       "actor": {"user": "alice", "role": "engineer"},
                       "session_id": "s", "allow_writes": True}, intent)
            for offered in seen:
                extra = sorted(offered - T.INTENT_TOOLS[intent])
                assert not extra, f"{intent} was offered {extra}"
    finally:
        conn.close()


# -- routing ----------------------------------------------------------------

def test_assist_question_reaches_the_assist_branch(client, synth_csv, db_file):
    batch_id = scored_batch(client, synth_csv)
    client.post(f"/assist/run?batch_id={batch_id}", headers=ENG)

    r = client.post("/chat",
                    json={"question": "show the items assist flagged for review",
                          "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "assist"
    assert "list_assist_queue" in tools_used(db_file)


def test_advisory_question_reaches_the_advisory_branch(client, synth_csv, db_file):
    batch_id = scored_batch(client, synth_csv)

    r = client.post("/chat",
                    json={"question": "dormant rule coverage for this batch",
                          "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "advisory"
    assert "get_dormant_coverage" in tools_used(db_file)


def test_unclassifiable_intent_falls_back_to_lookup(client, synth_csv, db_file,
                                                    monkeypatch):
    """A classifier that answers nonsense must cost latency, not the answer.
    Routing to `unknown` here would turn a good question into "I don't know"."""
    from app.llm.echo import EchoProvider
    from app.llm.provider import Response

    scored_batch(client, synth_csv)

    class BabblingProvider(EchoProvider):
        name, model = "babble", "babble-stub"

        def chat(self, messages, tools):
            if messages and messages[0].content.startswith(
                    "Classify the engineer's"):
                return Response(content="banana", model=self.model,
                                provider=self.name)
            return super().chat(messages, tools)

    monkeypatch.setattr("app.agent.graph.get_provider",
                        lambda: BabblingProvider())
    monkeypatch.setattr("app.agent.loop.get_provider",
                        lambda: BabblingProvider())

    r = client.post("/chat", json={"question": "why item 100007?"}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "lookup"
    assert "get_recommendation" in tools_used(db_file)


# -- the cost gate ----------------------------------------------------------

def test_run_assist_is_gated_on_first_ask(client, synth_csv, db_file):
    """One model call per live row is real money. Asking must not spend it.

    The answer has to carry the row count too: a gate the engineer cannot read
    is a gate they cannot pass, and "I don't know" is not a cost estimate.
    """
    from app.assist.chain import live_rows
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        expected = len(live_rows(conn, batch_id))
    finally:
        conn.close()

    r = client.post("/chat", json={"question": f"run assist on batch {batch_id}",
                                   "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "run_assist" in tools_used(db_file)
    assert rows(db_file, "SELECT COUNT(*) FROM assist_result")[0][0] == 0

    assert "I don't know" not in body["answer"], \
        "the no-source control swallowed the cost gate"
    assert str(expected) in body["answer"], \
        "the engineer was not told how many rows they are approving"


def make_live(conn, batch_id) -> int:
    """Give the batch rows the assist chain will actually work on.

    The suite pins BOM_ENGINE=rules (conftest), and that engine leaves `route`
    empty, so `live_rows()` is 0 for the synthetic fixture and every assist
    assertion below would hold vacuously -- `rows_assisted == 0 == expected`
    proves nothing. Marking rows active is the smallest way to make the chain
    real without switching the whole file to the statistical engine.
    """
    conn.execute("UPDATE recommendation_result SET route='active' "
                 "WHERE batch_id=?", (batch_id,))
    conn.commit()
    from app.assist.chain import live_rows
    return len(live_rows(conn, batch_id))


def test_run_assist_runs_only_when_confirmed(client, synth_csv, db_file):
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        expected = make_live(conn, batch_id)
        assert expected > 0, "the fixture must have live rows to assist"

        ctx = T.ToolContext(conn=conn,
                            actor={"user": "alice", "role": "engineer"},
                            batch_id=batch_id, question="run assist")
        gated = json.loads(T.dispatch(ctx, "run_assist", {"batch_id": batch_id}))
        assert gated["gated"] is True
        assert gated["live_rows"] == expected
        # The gate MUST ground itself. With no source, run_agent's no-source
        # control replaces the whole answer with "I don't know", the engineer
        # never sees the row count, and the confirm path is unreachable.
        assert ctx.sources and ctx.sources[-1]["type"] == "assist_gate"
        assert rows(db_file, "SELECT COUNT(*) FROM assist_result")[0][0] == 0

        done = json.loads(T.dispatch(ctx, "run_assist",
                                     {"batch_id": batch_id, "confirm": True}))
        assert done["rows_assisted"] == expected
        conn.commit()
    finally:
        conn.close()

    assert rows(db_file, "SELECT COUNT(*) FROM assist_result")[0][0] == expected


def test_run_assist_refuses_a_read_only_role(client, synth_csv):
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        ctx = T.ToolContext(conn=conn, actor={"user": "eve", "role": "viewer"},
                            batch_id=batch_id, question="run assist")
        out = json.loads(T.dispatch(ctx, "run_assist",
                                    {"batch_id": batch_id, "confirm": True}))
    finally:
        conn.close()
    assert "review role" in out["error"]


# -- the action-card boundary ----------------------------------------------

def stage_a_proposal(client, batch_id) -> int:
    r = client.post("/chat", json={"question": "set item 100005 max to 3",
                                   "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    pending = client.get("/pending-changes?status=pending", headers=ENG).json()
    assert pending["pending"], "the propose branch staged nothing"
    return pending["pending"][0]["pending_id"]


def test_action_card_records_nothing(client, synth_csv, db_file):
    """Property 7 of the boundary: the agent stages a card, a human presses it.

    If this ever fails, an LLM parse has reached `review_history` and therefore
    the WINGS export -- the one thing the whole design forbids.
    """
    batch_id = scored_batch(client, synth_csv)
    pending_id = stage_a_proposal(client, batch_id)

    r = client.post(
        "/chat",
        json={"question": f"confirm pending {pending_id} for item 100005",
              "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["intent"] == "action"
    assert body["staged_action"] is not None
    assert body["staged_action"]["executed"] is False
    assert "stage_review_action" in tools_used(db_file)

    # The card must carry the PROPOSAL, not just the ids. The console builds its
    # confirm request out of these fields; when they were missing it posted the
    # staged max with rop=0/min=0, which passes `max >= rop >= min` and lands a
    # silent zero in review_history and then the export.
    card = body["staged_action"]
    assert card["kind"] == "confirm_pending"
    assert card["pending_id"] == pending_id
    assert card["proposed_max"] == 3
    assert card["batch_id"] == batch_id
    assert "stockroom_id" in card

    assert rows(db_file, "SELECT COUNT(*) FROM review_history")[0][0] == 0
    status = rows(db_file, "SELECT status FROM pending_change WHERE pending_id=?",
                  (pending_id,))[0][0]
    assert status == "pending", "the card executed itself"


def test_action_card_is_refused_for_an_already_confirmed_proposal(
        client, synth_csv):
    """A card the endpoint would reject must never be offered: an engineer who
    presses confirm must not get a 400."""
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    pending_id = stage_a_proposal(client, batch_id)
    confirmed = client.post(
        "/review/100005/confirm-pending",
        json={"pending_id": pending_id, "decision": "override",
              "final_max": 3, "final_rop": 1, "final_min": 0}, headers=ENG)
    assert confirmed.status_code == 200, confirmed.text

    conn = get_conn()
    try:
        ctx = T.ToolContext(conn=conn,
                            actor={"user": "alice", "role": "engineer"},
                            batch_id=batch_id, question="confirm it")
        out = json.loads(T.dispatch(ctx, "stage_review_action",
                                    {"kind": "confirm_pending",
                                     "item_id": "100005",
                                     "pending_id": pending_id}))
    finally:
        conn.close()
    assert "already" in out["error"]


def test_cancel_stages_a_discard_not_a_confirm(client, synth_csv):
    """"cancel" is a discard. Staging it as confirm_pending would show the
    engineer who asked to cancel a card whose button records the override."""
    batch_id = scored_batch(client, synth_csv)
    pending_id = stage_a_proposal(client, batch_id)

    r = client.post(
        "/chat",
        json={"question": f"cancel pending {pending_id} for item 100005",
              "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["staged_action"]["kind"] == "discard_pending"


def test_a_stock_level_request_is_not_a_dormant_rule(client, synth_csv, db_file):
    """"stock" is in KEEP_RE, so the dormant-rule branch used to claim any
    sentence containing it. "set the stock max for 100005 at 5" asks to stage a
    pending change on the scored batch; answering it with a standing rule would
    size that part on every future engine run instead."""
    batch_id = scored_batch(client, synth_csv)

    r = client.post("/chat",
                    json={"question": "set the stock max for 100005 at 5",
                          "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    assert "propose_dormant_rule" not in tools_used(db_file)
    assert rows(db_file, "SELECT COUNT(*) FROM dormant_rule_config "
                         "WHERE scope='item'")[0][0] == 0


def test_assist_rows_survive_a_failure_later_in_the_turn(client, synth_csv,
                                                         db_file):
    """run_assist has already spent a model call per row by the time it returns.
    A provider that dies on the summarising pass must not take those rows with
    it -- chat_stream rolls the transaction back."""
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        expected = make_live(conn, batch_id)
        assert expected > 0, "the fixture must have live rows to assist"
        ctx = T.ToolContext(conn=conn,
                            actor={"user": "alice", "role": "engineer"},
                            batch_id=batch_id, question="run assist")
        T.dispatch(ctx, "run_assist", {"batch_id": batch_id, "confirm": True})
        # No commit here on purpose: the tool must have committed itself.
        conn.rollback()
    finally:
        conn.close()

    assert rows(db_file, "SELECT COUNT(*) FROM assist_result")[0][0] == expected, \
        "a rollback after run_assist threw away rows that were paid for"


def test_advisory_reads_match_their_endpoint_role_gate(client, synth_csv):
    """Peer outliers and dormant coverage are REVIEW_ROLES over HTTP
    (routers/similarity.py, routers/rules_config.py). A chat tool that reads the
    same rows without the same check makes /chat a way around the endpoint."""
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        ctx = T.ToolContext(conn=conn, actor={"user": "eve", "role": "viewer"},
                            batch_id=batch_id, question="outliers")
        for name in ("get_similarity_outliers", "get_dormant_coverage"):
            out = json.loads(T.dispatch(ctx, name, {"batch_id": batch_id}))
            assert "review role" in out.get("error", ""), name
    finally:
        conn.close()


def test_viewer_is_downgraded_not_served(client, synth_csv, db_file):
    """A read-only role that asks for a write must still get an answer, and must
    reach no tool that stages anything."""
    batch_id = scored_batch(client, synth_csv)

    r = client.post("/chat", json={"question": "set item 100005 max to 3",
                                   "batch_id": batch_id}, headers=VIEWER)
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "lookup"
    assert "propose_change" not in tools_used(db_file)
    assert rows(db_file, "SELECT COUNT(*) FROM pending_change")[0][0] == 0


# -- empty-state edges ------------------------------------------------------

def test_assist_verdict_before_assist_has_run(client, synth_csv):
    """No assist_result row is "I don't know", not an invented verdict."""
    batch_id = scored_batch(client, synth_csv)

    r = client.post("/chat",
                    json={"question": "what does assist say about item 100005",
                          "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    assert "I don't know" in r.json()["answer"]
    assert r.json()["sources"] == []


def test_history_is_scoped_to_the_asking_user(client, synth_csv):
    """session_id is client-supplied. Selecting on it alone replays one
    engineer's questions and answers into another's classify prompt -- and with
    an external provider configured, off the box."""
    from app.agent.graph import _history
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    r = client.post("/chat", json={"question": "why item 100007?",
                                   "batch_id": batch_id}, headers=ENG)
    session_id = r.json()["session_id"]

    conn = get_conn()
    try:
        assert _history(conn, session_id, "alice"), "the owner lost their own history"
        assert _history(conn, session_id, "eve") == []
        assert _history(conn, session_id, None) == []
    finally:
        conn.close()


def test_dormant_coverage_on_a_batch_without_dormant_rows(client, synth_csv):
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        dormant = conn.execute(
            "SELECT COUNT(*) AS n FROM recommendation_result "
            "WHERE batch_id=? AND route='dormant'", (batch_id,)).fetchone()["n"]
        ctx = T.ToolContext(conn=conn,
                            actor={"user": "alice", "role": "engineer"},
                            batch_id=batch_id, question="dormant coverage")
        out = json.loads(T.dispatch(ctx, "get_dormant_coverage",
                                    {"batch_id": batch_id}))
    finally:
        conn.close()

    if dormant == 0:
        assert out == {"_empty": True}
    else:
        assert out["dormant_rows"] == dormant
        assert out["matched"] + out["uncovered"] == dormant


def test_list_assist_queue_rejects_an_unknown_verdict(client, synth_csv):
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        ctx = T.ToolContext(conn=conn,
                            actor={"user": "alice", "role": "engineer"},
                            batch_id=batch_id, question="assist queue")
        out = json.loads(T.dispatch(ctx, "list_assist_queue",
                                    {"verdict": "looks_fine"}))
    finally:
        conn.close()
    assert "verdict must be one of" in out["error"]


# -- the stream -------------------------------------------------------------

def test_stream_reports_the_branch_it_chose(client, synth_csv):
    """The console draws this as a trace step, so the branch has to be visible."""
    scored_batch(client, synth_csv)
    with client.stream("POST", "/chat/stream",
                       json={"question": "why item 100007?"}, headers=ENG) as r:
        assert r.status_code == 200
        events = [json.loads(line) for line in r.iter_lines() if line]

    kinds = [e["type"] for e in events]
    assert kinds[0] == "request", "the entry event must still lead the stream"
    assert "classify" in kinds
    classify = next(e for e in events if e["type"] == "classify")
    assert classify["intent"] == "lookup"
    assert events[-1]["intent"] == "lookup"
