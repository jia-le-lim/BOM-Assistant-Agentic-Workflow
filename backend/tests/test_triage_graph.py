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
