"""Bulk review + triage summary -- the manual-review reduction path.

These exercise the statistical engine (BOM_ENGINE=statistical) so route/
consumable/agreement are populated; the workflow suite stays on the rule engine.
"""

from conftest import ENG, make_row, rows_to_csv, upload

WINS = (5, 30, 90, 180, 365, 547)


def _constant(item_id: str, **kw) -> dict:
    """A steady low-volume consumer with a big gap to current -> pending_review,
    high confidence, no benchmark -> bulk-acceptable. unitprice keeps it material
    (above the immaterial auto-clear floor) so it stays in the queue."""
    return make_row(
        item_id=item_id, module="TCB", sfm_criticality="M",
        frequencymonthswithusage=8, contractual_lead_time=30, unitprice=500,
        last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
        last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
        last_547_day_cnsmptn_qty=18,
        max_qty=100, rop_qty=50, min_qty=10,
        factory_recommended_new_max="", factory_recommended_new_rop="",
        factory_recommended_new_min="", **kw)


def _high_volume(item_id: str) -> dict:
    return make_row(
        item_id=item_id, module="TCB", sfm_criticality="H",
        frequencymonthswithusage=12, contractual_lead_time=30,
        last_5_day_cnsmptn_qty=5, last_30_day_cnsmptn_qty=30,
        last_90_day_cnsmptn_qty=90, last_180_day_cnsmptn_qty=180,
        last_365_day_cnsmptn_qty=365, last_547_day_cnsmptn_qty=540,
        max_qty=1, factory_recommended_new_max="",
        factory_recommended_new_rop="", factory_recommended_new_min="")


def _scored(client, monkeypatch, rows) -> int:
    monkeypatch.setenv("BOM_ENGINE", "statistical")
    up = upload(client, rows_to_csv(rows))
    assert up.status_code == 200, up.text
    bid = up.json()["batch_id"]
    r = client.post(f"/run-recommendation?batch_id={bid}", headers=ENG)
    assert r.status_code == 200, r.text
    return bid


def test_summary_has_triage_breakdowns(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_constant(f"P{i}") for i in range(5)])
    s = client.get(f"/batches/{bid}/summary", headers=ENG).json()
    for k in ("consumables", "routes", "agreements", "bulk_acceptable", "pareto"):
        assert k in s, s.keys()
    assert s["consumables"].get("constant", 0) == 5
    assert s["bulk_acceptable"] >= 5
    assert "items_for_80pct" in s["pareto"]


def test_bulk_accept_via_filter_clears_pending(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_constant(f"P{i}") for i in range(5)])
    s0 = client.get(f"/batches/{bid}/summary", headers=ENG).json()
    pending0 = s0["statuses"].get("pending_review", 0)
    assert pending0 == 5

    r = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "accept",
        "filters": {"consumable": "constant", "min_confidence": 0.8}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reviewed"] == 5 and body["awaiting_senior"] == 0

    s1 = client.get(f"/batches/{bid}/summary", headers=ENG).json()
    assert s1["statuses"].get("pending_review", 0) == pending0 - 5
    assert s1["statuses"].get("reviewed", 0) == 5


def test_bulk_reject_via_explicit_items(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_constant(f"P{i}") for i in range(3)])
    r = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "reject",
        "items": [{"item_id": "P0"}, {"item_id": "P1"}]})
    assert r.status_code == 200, r.text
    assert r.json()["reviewed"] == 2


def test_bulk_skips_non_pending(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_constant("P0")])
    first = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "accept", "items": [{"item_id": "P0"}]})
    assert first.json()["reviewed"] == 1
    # already reviewed -> the second pass touches nothing
    again = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "accept", "items": [{"item_id": "P0"}]})
    assert again.json()["reviewed"] == 0 and again.json()["skipped"] == 1


def test_high_risk_excluded_from_filter_but_gated_when_explicit(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_high_volume("HV")])
    # exclude_high_risk defaults True -> the filter selects nothing
    r = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "accept", "filters": {"route": "active"}})
    assert r.json()["selected"] == 0

    # explicit accept still records it, but behind the senior-approval gate
    r2 = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "accept", "items": [{"item_id": "HV"}]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["awaiting_senior"] == 1 and r2.json()["reviewed"] == 0


def test_bulk_requires_a_target(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_constant("P0")])
    r = client.post("/review/bulk", headers=ENG,
                    json={"batch_id": bid, "decision": "accept"})
    assert r.status_code == 422
