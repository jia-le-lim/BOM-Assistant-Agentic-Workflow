"""Endpoints the review console depends on: batch list + batch summary."""

from conftest import ENG, SENIOR, VIEWER, upload


def scored(client, synth_csv):
    b = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b


def test_list_batches(client, synth_csv):
    b = scored(client, synth_csv)
    rows = client.get("/batches", headers=VIEWER).json()
    assert len(rows) == 1
    assert rows[0]["batch_id"] == b
    assert rows[0]["status"] == "scored"
    assert rows[0]["scored_rule_version"] == "0.2.0-tcb"
    assert rows[0]["quarantined_count"] == 3


def test_batch_summary_shape(client, synth_csv):
    b = scored(client, synth_csv)
    s = client.get(f"/batches/{b}/summary", headers=VIEWER).json()
    assert s["scored"] == 7
    # statuses must cover every scored row exactly once
    assert sum(s["statuses"].values()) == 7
    assert s["statuses"]["pending_review"] == 4
    assert s["statuses"]["auto_cleared"] == 3
    assert sum(s["risk_levels"].values()) == 7
    assert sum(s["actions"].values()) == 7
    assert "ZERO_RECOMMENDATION_OVERRIDE" in s["reason_codes"]
    assert s["exposure_total_usd"] >= s["exposure_pending_usd"] > 0
    assert s["export_ready_rows"] == 0


def test_summary_tracks_workflow(client, synth_csv):
    b = scored(client, synth_csv)
    # High-risk accept -> awaiting_senior -> approve -> reviewed & exportable
    client.post(f"/review/100005?batch_id={b}", json={"decision": "accept"}, headers=ENG)
    s = client.get(f"/batches/{b}/summary", headers=VIEWER).json()
    assert s["statuses"]["awaiting_senior"] == 1
    assert s["export_ready_rows"] == 0        # not yet approved

    client.post(f"/review/100005/approve?batch_id={b}", headers=SENIOR)
    s = client.get(f"/batches/{b}/summary", headers=VIEWER).json()
    assert s["statuses"].get("awaiting_senior", 0) == 0
    assert s["statuses"]["reviewed"] == 1
    assert s["export_ready_rows"] == 1


def test_summary_404(client):
    assert client.get("/batches/999/summary", headers=VIEWER).status_code == 404
