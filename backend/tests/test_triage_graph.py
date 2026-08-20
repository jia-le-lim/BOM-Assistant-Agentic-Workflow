"""Phase-1 triage runs offline and persists explanations only."""

import sqlite3

from conftest import ENG, VIEWER, upload


def test_synthesis_cannot_clear_a_divergence(monkeypatch):
    from app.agent.specialists import synthesis
    from app.llm.provider import Response

    class UnsafeProvider:
        name = "unsafe"
        model = "unsafe-stub"

        def chat(self, messages, tools):
            return Response(content=(
                '{"tier":"clear_candidate","priority_score":1,'
                '"rationale":"clear it","confidence":1,'
                '"focus_question":"none"}'))

    monkeypatch.setattr("app.agent.specialists.get_provider",
                        lambda: UnsafeProvider())
    verdict = synthesis({
        "recommendation": {"item_id": "X", "agreement": "diverge",
                           "risk_level": "Low", "confidence": 0.95},
        "features": {"critical": False, "high_exposure": False},
        "clear_confidence_threshold": 0.8,
    })
    assert verdict["triage_tier"] == "review"


def test_per_item_triage_costs_one_item(client, synth_csv, db_file):
    """The console path. Triage is the only step that spends model calls, so
    opening one row must not pay for the whole batch."""
    batch_id = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG)

    queue = client.get(f"/recommendations?batch_id={batch_id}"
                       "&review_required=Y&limit=50", headers=ENG).json()["items"]
    target = queue[0]

    r = client.post("/triage/run",
                    json={"batch_id": batch_id, "item_id": target["item_id"],
                          "stockroom_id": target["stockroom_id"]}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["triaged"] == 1

    saved = client.get(f"/triage/{batch_id}", headers=ENG).json()
    assert saved["total"] == 1, "a per-item run triaged more than the item"
    assert saved["items"][0]["item_id"] == target["item_id"]


def test_per_item_refresh_keeps_other_items(client, synth_csv, db_file):
    """Scoped delete: re-running one row must not throw away every other row's
    triage -- that would be thousands of model calls lost to one button press."""
    batch_id = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG)
    queue = client.get(f"/recommendations?batch_id={batch_id}"
                       "&review_required=Y&limit=50", headers=ENG).json()["items"]
    first, second = queue[0], queue[1]

    for item in (first, second):
        client.post("/triage/run",
                    json={"batch_id": batch_id, "item_id": item["item_id"],
                          "stockroom_id": item["stockroom_id"]}, headers=ENG)
    assert client.get(f"/triage/{batch_id}", headers=ENG).json()["total"] == 2

    again = client.post("/triage/run",
                        json={"batch_id": batch_id, "item_id": first["item_id"],
                              "stockroom_id": first["stockroom_id"],
                              "refresh": True}, headers=ENG)
    assert again.status_code == 200, again.text
    assert again.json()["triaged"] == 1
    assert client.get(f"/triage/{batch_id}", headers=ENG).json()["total"] == 2


def test_per_item_triage_works_on_an_unflagged_row(client, synth_csv):
    """An engineer who opens a row and presses the button outranks the engine's
    own review_required flag."""
    batch_id = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG)
    cleared = client.get(f"/recommendations?batch_id={batch_id}"
                         "&review_required=N&limit=50", headers=ENG).json()["items"]
    if not cleared:
        return                                  # nothing auto-cleared in this fixture
    target = cleared[0]
    r = client.post("/triage/run",
                    json={"batch_id": batch_id, "item_id": target["item_id"],
                          "stockroom_id": target["stockroom_id"]}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["triaged"] == 1


def test_triage_graph_runs_offline_and_obeys_budget(client, synth_csv, db_file):
    batch_id = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG)

    run = client.post("/triage/run", json={"batch_id": batch_id},
                      headers=ENG)
    assert run.status_code == 200, run.text
    summary = run.json()
    assert summary["triaged"] == summary["candidates"] > 0
    assert summary["llm_calls_used"] <= summary["llm_call_budget"]

    saved = client.get(f"/triage/{batch_id}", headers=ENG).json()
    assert saved["total"] == summary["triaged"]
    assert {row["triage_tier"] for row in saved["items"]} <= {
        "clear_candidate", "review", "escalate"}
    assert all(0 <= row["priority_score"] <= 100 for row in saved["items"])
    assert all(row["demand_narrative"] for row in saved["items"])
    assert all(row["provider"] == "echo" for row in saved["items"])
    assert any(row["procurement_narrative"] for row in saved["items"])

    limited = client.post("/triage/run",
                          json={"batch_id": batch_id, "llm_call_budget": 7,
                                "refresh": True},
                          headers=ENG).json()
    assert limited["triaged"] == 1
    assert limited["llm_calls_used"] <= 7
    assert limited["budget_exhausted"] is True

    assert client.post("/triage/run", json={"batch_id": batch_id},
                       headers=VIEWER).status_code == 403

    conn = sqlite3.connect(db_file)
    try:
        review_required = conn.execute(
            "SELECT COUNT(*) FROM recommendation_result "
            "WHERE review_required='Y'").fetchone()[0]
        assert saved["total"] == review_required
    finally:
        conn.close()


def test_narratives_are_plain_prose_and_capped():
    """The card sits beside the engine numbers. Markdown source rendered as
    literal text is worse than no formatting, and 2,300 characters buries the
    recommendation it is meant to support.

    Regression: deepseek returned '## Review History' plus a GFM table.
    """
    from app.agent.specialists import MAX_NARRATIVE_CHARS, _plain

    raw = (
        "Here's the summary for item **500794916**: ## Review History "
        "(3 prior decisions)\n"
        "| Batch | Decision | Reviewer |\n"
        "|-------|----------|----------|\n"
        "| 10 | historical | pengchin |\n"
        "- bullet one\n"
        "* bullet two\n\n\n"
        "Route is `active` and the class is **constant**."
    )
    out = _plain(raw)
    for marker in ("|", "##", "**", "`", "- bullet", "* bullet"):
        assert marker not in out, f"{marker!r} survived: {out!r}"
    assert "500794916" in out
    assert "bullet one" in out                 # text kept, marker removed
    assert "\n\n" not in out


def test_plain_caps_at_a_sentence_boundary():
    from app.agent.specialists import MAX_NARRATIVE_CHARS, _plain

    long = ("This is a sentence about the part. " * 60).strip()
    out = _plain(long)
    assert len(out) <= MAX_NARRATIVE_CHARS
    assert out.endswith("."), f"cut mid-thought: {out[-40:]!r}"


def test_plain_handles_empty_and_none():
    from app.agent.specialists import _plain
    assert _plain("") == ""
    assert _plain(None) == ""


def test_specialist_prompts_demand_a_conclusion_not_a_recap():
    """The reviewer sees every stored value on the same screen. Reciting them
    back costs tokens and buries the one thing they cannot read off it."""
    from app.agent import prompts
    for name in ("TRIAGE_HISTORY_SYSTEM", "TRIAGE_DEMAND_SYSTEM",
                 "TRIAGE_PROCUREMENT_SYSTEM"):
        text = getattr(prompts, name)
        assert "Do NOT restate" in text, name
        assert "One sentence." in text, name
        assert "no markdown" in text.lower(), name
        assert prompts.NO_SIGNAL in text, name


def test_synthesis_prompt_forbids_repeating_the_numbers():
    from app.agent import prompts
    text = prompts.TRIAGE_SYNTHESIS_SYSTEM
    assert "never repeat them" in text
    assert "at most two sentences" in text
    assert "No markdown" in text


def test_conclusion_fields_are_capped(monkeypatch):
    """rationale is the first thing the reviewer reads. Uncapped it came back at
    521 characters restitching the specialist lines -- two sentences by
    punctuation, a paragraph by eye."""
    from app.agent.specialists import (MAX_FOCUS_CHARS, MAX_RATIONALE_CHARS,
                                       synthesis)
    from app.llm.provider import Response

    long_rationale = "This sentence explains the driver in detail. " * 20
    long_focus = "Can the engineer confirm the demand cessation is permanent? " * 8

    class VerboseProvider:
        name, model = "verbose", "verbose-stub"

        def chat(self, messages, tools):
            import json as _json
            return Response(content=_json.dumps({
                "tier": "review", "priority_score": 50,
                "rationale": "## Summary\n" + long_rationale,
                "confidence": 0.5, "focus_question": long_focus}))

    monkeypatch.setattr("app.agent.specialists.get_provider",
                        lambda: VerboseProvider())
    v = synthesis({
        "recommendation": {"item_id": "X", "agreement": "match",
                           "risk_level": "Low", "confidence": 0.5},
        "features": {"critical": False, "high_exposure": False},
        "clear_confidence_threshold": 0.8,
    })
    assert len(v["rationale"]) <= MAX_RATIONALE_CHARS
    assert len(v["focus_question"]) <= MAX_FOCUS_CHARS
    assert "##" not in v["rationale"], "markdown survived into the conclusion"


def test_stream_reports_every_phase_and_persists_once(client, synth_csv, db_file):
    """The streamed run is the same run, observed.

    It must emit the phases it announced, finish with the same summary the
    blocking endpoint returns, and write exactly one triage row -- streaming
    must not cost a second pass over the graph.
    """
    import json as _json

    batch_id = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG)
    queue = client.get(f"/recommendations?batch_id={batch_id}"
                       "&review_required=Y&limit=50", headers=ENG).json()["items"]
    target = queue[0]

    r = client.post("/triage/run/stream",
                    json={"batch_id": batch_id, "item_id": target["item_id"],
                          "stockroom_id": target["stockroom_id"]}, headers=ENG)
    assert r.status_code == 200, r.text
    events = [_json.loads(line) for line in r.text.splitlines() if line.strip()]
    kinds = [e["type"] for e in events]

    assert kinds[0] == "start" and kinds[-1] == "complete"
    assert events[-1]["summary"]["triaged"] == 1

    announced = [p["node"] for e in events if e["type"] == "item_start"
                 for p in e["phases"]]
    assert announced, "no phase plan was streamed"
    ran = [e["node"] for e in events if e["type"] == "phase"]
    assert set(announced) == set(ran), (announced, ran)
    assert "persist" in ran

    done = next(e for e in events if e["type"] == "item_done")
    assert done["item_id"] == target["item_id"]
    assert done["triage_tier"] in {"clear_candidate", "review", "escalate"}

    saved = client.get(f"/triage/{batch_id}", headers=ENG).json()
    assert saved["total"] == 1, "streaming triaged more rows than it announced"


def test_stream_rejects_an_unscored_batch_as_400(client, synth_csv):
    """The plain endpoint's 400 contract survives streaming: the first event is
    drawn before the response starts, so a bad request never arrives as an
    error buried inside a 200 stream."""
    batch_id = upload(client, synth_csv).json()["batch_id"]
    r = client.post("/triage/run/stream", json={"batch_id": batch_id}, headers=ENG)
    assert r.status_code == 400, r.text


def test_stream_needs_a_review_role(client, synth_csv):
    r = client.post("/triage/run/stream", json={"batch_id": 1}, headers=VIEWER)
    assert r.status_code == 403
