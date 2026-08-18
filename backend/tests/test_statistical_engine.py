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


def _run_cfg(rows, cfg):
    from app.engine_statistical import run
    return run(pd.DataFrame(rows), cfg)


_CONST = dict(last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
              last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
              last_547_day_cnsmptn_qty=18)


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


def test_agreement_none_when_no_benchmark():
    r = _run([_row(last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
                   last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
                   last_547_day_cnsmptn_qty=18)]).iloc[0]
    assert r["agreement"] == "none"
    assert "MATCHES_FACTORY" not in r["reason_code"]
    assert "DIVERGES_FACTORY" not in r["reason_code"]


def test_agreement_match_and_diverge():
    base = dict(last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
                last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
                last_547_day_cnsmptn_qty=18)
    r0 = _run([_row(**base)]).iloc[0]
    mx, rp, mn = (int(r0["factory_recommended_new_max"]),
                  int(r0["factory_recommended_new_rop"]),
                  int(r0["factory_recommended_new_min"]))
    match = _run([_row(factory_recommended_new_max=mx, factory_recommended_new_rop=rp,
                       factory_recommended_new_min=mn, **base)]).iloc[0]
    assert match["agreement"] == "match"
    assert "MATCHES_FACTORY" in match["reason_code"]
    assert match["confidence_score"] >= r0["confidence_score"]

    div = _run([_row(factory_recommended_new_max=mx + 100,
                     factory_recommended_new_rop=rp + 100,
                     factory_recommended_new_min=mn + 100, **base)]).iloc[0]
    assert div["agreement"] == "diverge"
    assert "DIVERGES_FACTORY" in div["reason_code"]


def test_benchmark_never_changes_sizing():
    """The engineer benchmark is a scoring signal only -- Min/ROP/Max must be
    identical whether or not factory_recommended_new_* is present (no leakage)."""
    base = dict(last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
                last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
                last_547_day_cnsmptn_qty=18)
    a = _run([_row(**base)]).iloc[0]
    b = _run([_row(factory_recommended_new_max=7, factory_recommended_new_rop=4,
                   factory_recommended_new_min=1, **base)]).iloc[0]
    for c in ("factory_recommended_new_max", "factory_recommended_new_rop",
              "factory_recommended_new_min"):
        assert a[c] == b[c]


def test_noop_vs_current_autoclears():
    """A base-flagged (high-volume) row that equals current -> nothing to action
    (opt-in lever: the no-op tolerance is off by default)."""
    cfg = {"autoclear_noop_abs": 1, "autoclear_noop_rel": 0.10}
    hv = dict(last_5_day_cnsmptn_qty=5, last_30_day_cnsmptn_qty=30,
              last_90_day_cnsmptn_qty=90, last_180_day_cnsmptn_qty=180,
              last_365_day_cnsmptn_qty=365, last_547_day_cnsmptn_qty=540)
    r0 = _run([_row(unitprice=50, frequencymonthswithusage=12, **hv)]).iloc[0]
    assert r0["review_required"] == "Y"                      # high-volume flags it
    mx, rp, mn = (int(r0["factory_recommended_new_max"]),
                  int(r0["factory_recommended_new_rop"]),
                  int(r0["factory_recommended_new_min"]))
    r = _run_cfg([_row(unitprice=50, frequencymonthswithusage=12,
                       max_qty=mx, rop_qty=rp, min_qty=mn, **hv)], cfg).iloc[0]
    assert r["review_required"] == "N"
    assert "NOOP_VS_CURRENT" in r["reason_code"]


def test_immaterial_change_autoclears():
    """A cheap part that changed -> a wrong call costs little (opt-in lever)."""
    rows = [_row(unitprice=10, max_qty=100, rop_qty=50, min_qty=10, **_CONST)]
    assert _run(rows).iloc[0]["review_required"] == "Y"      # off by default
    r = _run_cfg(rows, {"autoclear_immaterial_usd": 200}).iloc[0]
    assert r["review_required"] == "N"
    assert "IMMATERIAL_VALUE" in r["reason_code"]


def test_high_value_change_stays_flagged():
    """A material change on an expensive part is never auto-cleared."""
    r = _run([_row(unitprice=100000, max_qty=100, rop_qty=50, min_qty=10, **_CONST)]).iloc[0]
    assert r["review_required"] == "Y"


def test_critical_is_never_autocleared():
    r = _run([_row(sfm_criticality="H", unitprice=10, max_qty=100, **_CONST)]).iloc[0]
    assert r["review_required"] == "Y" and r["risk_level"] == "High"


def test_autoclear_does_not_change_sizing():
    """The accuracy invariant: the review policy flips the flag, never the sizes."""
    rows = [_row(unitprice=10, max_qty=100, rop_qty=50, min_qty=10, **_CONST)]
    flagged = _run(rows).iloc[0]                                      # lever off -> Y
    cleared = _run_cfg(rows, {"autoclear_immaterial_usd": 200}).iloc[0]  # on -> N
    assert cleared["review_required"] == "N" and flagged["review_required"] == "Y"
    for c in ("factory_recommended_new_max", "factory_recommended_new_rop",
              "factory_recommended_new_min"):
        assert cleared[c] == flagged[c]


def test_reliable_lever_is_opt_in():
    rows = [_row(unitprice=300, frequencymonthswithusage=12,
                 max_qty=100, rop_qty=50, min_qty=10, **_CONST)]
    assert _run(rows).iloc[0]["review_required"] == "Y"      # default: lever off
    on = _run_cfg(rows, {"autoclear_reliable": True}).iloc[0]
    assert on["review_required"] == "N" and "RELIABLE_STABLE" in on["reason_code"]


# --- PRD v3.2 demand-model + policy levers (all opt-in, default off) ---

_RAMP = dict(last_30_day_cnsmptn_qty=6, last_90_day_cnsmptn_qty=15,
             last_180_day_cnsmptn_qty=18, last_365_day_cnsmptn_qty=20,
             last_547_day_cnsmptn_qty=22)
_HI = dict(last_30_day_cnsmptn_qty=10, last_90_day_cnsmptn_qty=30,
           last_180_day_cnsmptn_qty=60, last_365_day_cnsmptn_qty=120,
           last_547_day_cnsmptn_qty=180)


def test_blended_estimator_lifts_recent_ramp():
    single = _run([_row(**_RAMP)]).iloc[0]["mu_day"]
    blended = _run_cfg([_row(**_RAMP)], {"demand_estimator": "blended"}).iloc[0]["mu_day"]
    assert blended > single


def test_trend_adjust_inflates_on_ramp():
    plain = _run([_row(**_RAMP)]).iloc[0]["mu_day"]
    trend = _run_cfg([_row(**_RAMP)], {"trend_adjust": True}).iloc[0]["mu_day"]
    assert trend > plain


def test_cost_aware_sl_shrinks_expensive_part():
    base = _row(unitprice=100000, sfm_criticality="M", **_HI)
    default = _run([base]).iloc[0]
    aware = _run_cfg([base], {"service_level_mode": "cost_aware"}).iloc[0]
    assert aware["factory_recommended_new_max"] <= default["factory_recommended_new_max"]
    assert aware["factory_recommended_new_rop"] <= default["factory_recommended_new_rop"]


def test_cost_aware_sl_holds_or_raises_cheap_part():
    base = _row(unitprice=1, sfm_criticality="L", **_CONST)
    default = _run([base]).iloc[0]
    aware = _run_cfg([base], {"service_level_mode": "cost_aware"}).iloc[0]
    assert aware["factory_recommended_new_max"] >= default["factory_recommended_new_max"]


def test_lead_time_sigma_widens_sizing():
    base = _row(contractual_lead_time=30, sfm_mean_lt_cd=90, **_CONST)
    default = _run([base]).iloc[0]
    wide = _run_cfg([base], {"lead_time_sigma": True}).iloc[0]
    assert wide["factory_recommended_new_max"] >= default["factory_recommended_new_max"]


def test_doi_cap_limits_max():
    base = _row(**_HI)
    default = _run([base]).iloc[0]
    capped = _run_cfg([base], {"policy_max_doi_days": 20}).iloc[0]
    assert capped["factory_recommended_new_max"] <= default["factory_recommended_new_max"]
    assert "DOI_CAP" in capped["reason_code"]


def test_excess_netting_reduces_max():
    base = _row(qry_eoh_excess_qty=3, **_HI)
    default = _run([base]).iloc[0]
    netted = _run_cfg([base], {"policy_excess_netting": True}).iloc[0]
    assert netted["factory_recommended_new_max"] <= default["factory_recommended_new_max"]
    assert "EXCESS_NETTED" in netted["reason_code"]


def test_levers_default_off_match_baseline():
    """Every new lever defaults to the current sizing (no silent regression)."""
    rows = [_row(**_CONST)]
    base = _run(rows).iloc[0]
    same = _run_cfg(rows, {"demand_estimator": "single", "trend_adjust": False,
                           "lead_time_sigma": False,
                           "service_level_mode": "criticality"}).iloc[0]
    for c in ("factory_recommended_new_max", "factory_recommended_new_rop",
              "factory_recommended_new_min"):
        assert base[c] == same[c]


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
