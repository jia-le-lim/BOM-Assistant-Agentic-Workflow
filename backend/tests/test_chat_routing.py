"""Intent -> tool routing accuracy.

test_agent_boundary.py asserts the safety properties; this module asserts the
*usefulness* one -- that a legitimate question reaches the tool that can answer
it. Both matter: a spurious "I don't know" is a wrong answer with good manners.

Routing runs on EchoProvider (llm/echo.py), which is the only provider CI can
reach. It is a regex ladder, not a model, so what this measures is the contract
the ladder and the ToolSpec descriptions agree on -- not model judgement. Do not
"improve" it by teaching EchoProvider to read the system prompt; that would make
the stub a model and the test a tautology.
"""

import json
import sqlite3

import pytest
from conftest import ENG, upload

CASES = [
    # (question, expected tool or None)
    ("why item 100005?",                      "get_recommendation"),
    ("explain the recommendation for 100007", "get_recommendation"),
    ("what is item 100005 max right now?",    "get_current_values"),
    ("current min for 100005",                "get_current_values"),
    ("history 100005",                        "get_item_history"),
    ("what notes are on item 100005",         "get_item_notes"),
    ("top exposure items",                    "top_exposure"),
    ("highest value items left to review",    "top_exposure"),
    ("show me the review queue",              "list_review_queue"),
    ("list the high risk items",              "list_review_queue"),
    ("which items are set to Decrease",       "list_review_queue"),
    ("batch summary",                         "batch_summary"),
    ("what thresholds is the engine using",   "explain_rules"),
    ("set item 100005 max to 3",              "propose_change"),
    ("increase item 100005 a little",         None),   # vague -> no stage
    ("what is the capital of France",         None),   # off-domain
]


def scored_batch(client, csv_bytes):
    b = upload(client, csv_bytes).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b


def tools_used(db_file) -> list[str]:
    """Which tools the last turn actually ran.

    conversation_turn.tool_calls is the record the audit path already keeps
    (loop.log_turn), so routing is asserted from the same place a reviewer would
    look, rather than by reaching into the provider.
    """
    conn = sqlite3.connect(db_file)
    try:
        raw = conn.execute("SELECT tool_calls FROM conversation_turn "
                           "ORDER BY turn_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return [c["name"] for c in json.loads(raw[0])] if raw else []


def test_route_extracts_filters_and_respects_precedence():
    """The cases above assert which tool ran; these assert the arguments and the
    orderings that a tool name alone cannot show.

    Routed directly rather than through /chat because the filter arguments are
    not visible in the rendered answer.
    """
    from app.agent import tools as T
    from app.llm.echo import EchoProvider

    p = EchoProvider()
    write = {s.name for s in T.specs(allow_writes=True)}
    read_only = {s.name for s in T.specs(allow_writes=False)}

    def route(q, available=None):
        c = p._route(q, available if available is not None else write)
        return None if c is None else (c.name, c.arguments)

    # Filters reach the tool, title-cased to match the schema enums.
    assert route("list the high risk items") == (
        "list_review_queue", {"risk_level": "High"})
    assert route("which items are set to decrease") == (
        "list_review_queue", {"action": "Decrease"})

    # A filtered count is a list question, not a whole-batch summary. This is
    # the precedence that batch_summary used to swallow.
    assert route("how many high risk items are left")[0] == "list_review_queue"
    assert route("batch summary")[0] == "batch_summary"

    # A write verb inside a question about the past does not stage anything.
    # An explicit quantity is the discriminator, and there is none here.
    assert route("change the history for 100005") == (
        "get_item_history", {"item_id": "100005"})

    # ...but a real staging request keeps working when its rationale happens to
    # mention a note, which is exactly what a rationale tends to do.
    assert route("set item 100005 max to 3 (note: bench spare)") == (
        "propose_change", {"item_id": "100005", "proposed_max": 3,
                           "rationale": "set item 100005 max to 3 "
                                        "(note: bench spare)"})

    # "know" contains "now": the current-values branch must not fire on it.
    assert route("I do not know about item 100005 max") is None

    # "setting" contains "set". A read must not be mistaken for a write, and the
    # item id must never be captured as the proposed quantity.
    assert route("what is the current max setting for item 100005") == (
        "get_current_values", {"item_id": "100005"})
    assert route("raise the max for item 100005") is None

    # LIST_RE owns "show"/"list", which is also how the rule set and the batch
    # summary get asked for.
    assert route("show me the thresholds")[0] == "explain_rules"
    assert route("list the active rules")[0] == "explain_rules"
    assert route("show the batch summary")[0] == "batch_summary"

    # The prompt's tool-choice table promises this phrasing reaches top_exposure.
    assert route("which items should I look at first")[0] == "top_exposure"

    # Recall is item-agnostic; naming a part must not make it unreachable.
    assert route("do you remember what I usually do for item 100005")[0] == (
        "recall_context")

    # The write tool is gated on what the role was actually offered.
    assert route("set item 100005 max to 3", read_only) is None


@pytest.mark.parametrize("question,expected", CASES)
def test_routes_to_expected_tool(client, synth_csv, db_file, question, expected):
    scored_batch(client, synth_csv)
    r = client.post("/chat", json={"question": question}, headers=ENG)
    assert r.status_code == 200, r.text
    used = tools_used(db_file)
    if expected is None:
        # An off-domain question legitimately runs no tool, and a vague one may
        # still consult a read tool -- the property that matters is that nothing
        # was staged.
        assert "propose_change" not in used, used
    else:
        assert expected in used, f"{question!r} -> {used}, wanted {expected}"
