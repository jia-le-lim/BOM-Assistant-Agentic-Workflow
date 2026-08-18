"""Statistical sizing engine + universal (Excel) ingestion tests.

The workflow suite (test_api_flow etc.) is pinned to the rule engine via
BOM_ENGINE=rules in conftest; these tests exercise the statistical engine that
is the production default (engine_statistical.run) plus .xlsx upload.
"""

import io

import pandas as pd

from conftest import ENG, make_row, rows_to_csv, upload

WINS = (5, 30, 90, 180, 365, 547)


def _row(**kw) -> dict:
    base = {
        "item_id": "A", "unitprice": "10", "max_qty": "2", "rop_qty": "1",
        "min_qty": "0", "contractual_lead_time": "30", "order_qty_multiple": "1",
        "sfm_criticality": "M", "frequencymonthswithusage": "8",
        **{f"last_{w}_day_cnsmptn_qty": "0" for w in WINS},
    }
    base.update({k: str(v) for k, v in kw.items()})
    return base


def _run(rows):
    from app.engine_statistical import run
    return run(pd.DataFrame(rows))


def test_constant_consumer_is_sized_and_monotonic():
    r = _run([_row(last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
                   last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
                   last_547_day_cnsmptn_qty=18)]).iloc[0]
    assert r["route"] == "active" and r["consumable"] == "constant"
    mx, rp, mn = (r["factory_recommended_new_max"], r["factory_recommended_new_rop"],
                  r["factory_recommended_new_min"])
    assert mx >= rp >= mn >= 0
    assert "CONSTANT_CONSUMER" in r["reason_code"]
    assert 0 < r["confidence_score"] <= 1
    assert r["model_version"] == "stat-v1"
    assert isinstance(r["explanation"], str) and r["explanation"]


def test_high_volume_is_flagged_for_review():
    r = _run([_row(item_id="B", sfm_criticality="H", frequencymonthswithusage=12,
                   last_5_day_cnsmptn_qty=5, last_30_day_cnsmptn_qty=30,
                   last_90_day_cnsmptn_qty=90, last_180_day_cnsmptn_qty=180,
                   last_365_day_cnsmptn_qty=365, last_547_day_cnsmptn_qty=540)]).iloc[0]
    assert r["mu_day"] > 0.1
    assert "HIGH_VOLUME_REVIEW" in r["reason_code"]
    assert r["review_required"] == "Y" and r["risk_level"] == "High"
    assert r["confidence_score"] <= 0.5


def test_dormant_noncritical_zeroed_but_review():
    r = _run([_row(item_id="C", sfm_criticality="L", frequencymonthswithusage=0)]).iloc[0]
    assert r["route"] == "dormant"
    assert r["factory_recommended_new_max"] == 0
    assert r["review_required"] == "Y"
    assert "DORMANT_NONCRITICAL_ZERO" in r["reason_code"]


def test_dormant_critical_gets_keepalive_not_zero():
    r = _run([_row(item_id="D", sfm_criticality="H", max_qty=0,
                   order_qty_multiple=2)]).iloc[0]
    assert r["factory_recommended_new_max"] >= 1        # recom = 0 trap avoided
    assert "DORMANT_CRITICAL_KEEPALIVE" in r["reason_code"]


def test_end_to_end_statistical_engine(client, monkeypatch):
    monkeypatch.setenv("BOM_ENGINE", "statistical")     # override conftest's "rules"
    rows = [make_row(item_id="A", module="TCB",
                     last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
                     last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
                     last_547_day_cnsmptn_qty=18, frequencymonthswithusage=8,
                     sfm_criticality="M", contractual_lead_time=30,
                     max_qty=2, rop_qty=1, min_qty=0)]
    up = upload(client, rows_to_csv(rows))
    assert up.status_code == 200, up.text
    bid = up.json()["batch_id"]
    run_r = client.post(f"/run-recommendation?batch_id={bid}", headers=ENG)
    assert run_r.status_code == 200, run_r.text
    body = run_r.json()
    assert body["rows_scored"] == 1
    assert "CONSTANT_CONSUMER" in body["top_reason_codes"]


def test_xlsx_upload_is_accepted(client):
    rows = [make_row(item_id="X1", module="TCB"), make_row(item_id="X2", module="TCB")]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    r = client.post(
        "/upload-bom-file",
        files={"file": ("month.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"label": "xl", "module_filter": "TCB"},
        headers=ENG,
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows_loaded"] == 2
