"""Statistical sizing engine + universal (Excel) ingestion tests.

The workflow suite (test_api_flow etc.) is pinned to the rule engine via
BOM_ENGINE=rules in conftest; these tests exercise the statistical engine that
is the production default (engine_statistical.run) plus .xlsx upload.
"""

import io

import pandas as pd

from conftest import ENG, SENIOR, make_row, rows_to_csv, upload

from app.engine_statistical import _drift_ok

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
    assert r["model_version"] == "stat-v2"
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


# --- benchmark ladder: grading a month nobody has reviewed yet -------------
# factory_recommended_new_* only exists on a COMPLETED cycle. Without a fallback
# rung a fresh upload scores "none" on every row and every agreement-gated
# signal downstream (safe_clear, demand_only, bulk_acceptable) goes dark.

def _sized():
    """What the engine proposes for a plain _CONST row, no benchmark present."""
    r = _run([_row(**_CONST)]).iloc[0]
    return (int(r["factory_recommended_new_max"]),
            int(r["factory_recommended_new_rop"]),
            int(r["factory_recommended_new_min"]))


def _prior(**kw):
    mx, rp, mn = _sized()
    kw.setdefault("prior_final_max", mx)
    kw.setdefault("prior_final_rop", rp)
    kw.setdefault("prior_final_min", mn)
    return _run([_row(**kw, **_CONST)]).iloc[0]


def test_factory_column_outranks_the_prior_review():
    """The engineer's number for THIS cycle wins. Otherwise a reviewed month
    would silently be graded against stale history."""
    mx, rp, mn = _sized()
    r = _prior(factory_recommended_new_max=mx, factory_recommended_new_rop=rp,
               factory_recommended_new_min=mn,
               prior_final_max=mx + 500, prior_final_rop=rp + 500,
               prior_final_min=mn + 500, prior_c365=12)
    assert r["agreement_source"] == "factory"
    assert r["agreement"] == "match"
    assert "MATCHES_FACTORY" in r["reason_code"]


def test_prior_review_is_the_benchmark_on_an_unreviewed_month():
    """The point of the ladder: a blank factory column is no longer unknowable."""
    r = _prior(prior_c365=12)
    assert r["agreement_source"] == "prior_review"
    assert r["agreement"] == "match"
    assert "MATCHES_PRIOR_REVIEW" in r["reason_code"]


def test_prior_review_can_diverge():
    r = _prior(prior_final_max=9999, prior_final_rop=9999, prior_final_min=9999,
               prior_c365=12)
    assert r["agreement"] == "diverge"
    assert r["agreement_source"] == "prior_review"
    assert "DIVERGES_PRIOR_REVIEW" in r["reason_code"]


def test_drifted_demand_invalidates_the_precedent():
    """365d demand tripled since that review. Reproducing the old number is not
    agreement with the engineer -- it is agreement with a dead decision."""
    r = _prior(prior_c365=4)
    assert (r["agreement"], r["agreement_source"]) == ("none", "")


def test_unverifiable_staleness_is_not_a_benchmark():
    """Strict, because this rung feeds auto-clear (specialists.safe_clear)."""
    r = _prior()                                  # no prior_c365 at all
    assert (r["agreement"], r["agreement_source"]) == ("none", "")


def test_drift_band_edges():
    assert _drift_ok(12, 12)
    assert _drift_ok(15, 12)            # exactly +25%
    assert not _drift_ok(15.1, 12)
    assert not _drift_ok(float("nan"), 12)
    assert not _drift_ok(12, float("nan"))
    # The floor is on the FRACTION (0.25 * max(|then|, 1)), not on the delta:
    # a part waking from zero consumption has no valid precedent.
    assert not _drift_ok(1, 0)
    assert _drift_ok(0, 0)


def test_prior_match_lifts_confidence_less_than_factory():
    """A precedent is weaker evidence than the engineer's own current number.

    Sized off a SPORADIC row: a constant consumer already sits at 0.9 and both
    lifts clip against the 0.95 ceiling, hiding the ordering.
    """
    sporadic = dict(last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
                    last_365_day_cnsmptn_qty=0, last_547_day_cnsmptn_qty=0)
    r0 = _run([_row(**sporadic)]).iloc[0]
    assert r0["consumable"] == "sporadic"
    sized = {"max": int(r0["factory_recommended_new_max"]),
             "rop": int(r0["factory_recommended_new_rop"]),
             "min": int(r0["factory_recommended_new_min"])}
    prior = _run([_row(prior_final_max=sized["max"], prior_final_rop=sized["rop"],
                       prior_final_min=sized["min"], prior_c365=0,
                       **sporadic)]).iloc[0]
    factory = _run([_row(factory_recommended_new_max=sized["max"],
                         factory_recommended_new_rop=sized["rop"],
                         factory_recommended_new_min=sized["min"],
                         **sporadic)]).iloc[0]
    assert prior["agreement_source"] == "prior_review"
    assert (r0["confidence_score"] < prior["confidence_score"]
            < factory["confidence_score"])


def test_prior_benchmark_never_changes_sizing():
    """Same no-leakage guarantee as the factory column, for the new rung."""
    a = _run([_row(**_CONST)]).iloc[0]
    b = _prior(prior_final_max=9999, prior_final_rop=9999, prior_final_min=9999,
               prior_c365=12)
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


# The population the anchoring levers exist for: one issue a year, nothing in the
# last quarter. mu_LT lands near 0.3, so the quantile returns Max=1 -- which is the
# level already in force, i.e. inside the snap band. _CONST is deliberately NOT
# reused here: at 12/year it sizes to Max 3 against a current of 1 and never enters
# the band, which is the whole point of the lever (analysis/s26_small_delta_tuning).
_LOW = dict(frequencymonthswithusage=1, max_qty=1, rop_qty=0, min_qty=0,
            last_180_day_cnsmptn_qty=1, last_365_day_cnsmptn_qty=1,
            last_547_day_cnsmptn_qty=1)
_OTD = dict(replenishment_policy="Order To Demand",
            prior_final_max=2, prior_final_rop=1, prior_final_min=0)
# Anchoring is ON by default, so "no anchoring" has to be asked for explicitly --
# _run() is no longer the unanchored baseline these tests contrast against.
_OFF = {"continuity_snap": 0, "prior_anchor_policy": ""}


def test_continuity_snap_holds_the_current_level():
    base = _row(**_LOW)
    default = _run_cfg([base], _OFF).iloc[0]
    assert abs(default["factory_recommended_new_max"] - 1) <= 1, "fixture must sit in the band"
    snapped = _run_cfg([base], {"continuity_snap": 1}).iloc[0]
    assert snapped["factory_recommended_new_max"] == 1
    assert snapped["factory_recommended_new_rop"] == 0
    assert snapped["factory_recommended_new_min"] == 0
    assert "CONTINUITY_SNAP" in snapped["reason_code"]
    assert snapped["factory_recommendation_action"] == "Maintain"


def test_continuity_snap_leaves_a_far_engine_number_alone():
    base = _row(max_qty=99, rop_qty=50, min_qty=10, **_HI)
    default = _run_cfg([base], _OFF).iloc[0]
    snapped = _run_cfg([base], {"continuity_snap": 1}).iloc[0]
    assert snapped["factory_recommended_new_max"] == default["factory_recommended_new_max"]
    assert "CONTINUITY_SNAP" not in snapped["reason_code"]


def test_snap_carries_min_so_the_monotonic_clamp_cannot_undo_it():
    """Regression: snapping Max+ROP but not Min lets new_rop = max(new_rop, new_min)
    re-raise ROP, and new_max = max(new_max, new_rop) then drag Max back up."""
    r = _run_cfg([_row(**_LOW)], {"continuity_snap": 1}).iloc[0]
    assert (r["factory_recommended_new_max"], r["factory_recommended_new_rop"],
            r["factory_recommended_new_min"]) == (1, 0, 0)


def test_snap_does_not_touch_dormant_rows():
    dormant = _row(item_id="D", max_qty=4, rop_qty=2, min_qty=1)
    default = _run_cfg([dormant], _OFF).iloc[0]
    snapped = _run_cfg([dormant], {"continuity_snap": 1}).iloc[0]
    assert default["route"] == "dormant"
    assert snapped["factory_recommended_new_max"] == default["factory_recommended_new_max"]
    assert "CONTINUITY_SNAP" not in snapped["reason_code"]


def test_prior_anchor_beats_the_current_level_for_order_to_demand():
    r = _run_cfg([_row(**_LOW, **_OTD)],
                 {"continuity_snap": 1, "prior_anchor_policy": "demand"}).iloc[0]
    assert r["factory_recommended_new_max"] == 2
    assert r["factory_recommended_new_rop"] == 1
    assert r["factory_recommended_new_min"] == 0
    assert "PRIOR_ANCHOR" in r["reason_code"]
    assert "CONTINUITY_SNAP" not in r["reason_code"]


def test_prior_anchor_ignores_order_to_max_parts():
    base = _row(**_LOW, **_OTD)
    base["replenishment_policy"] = "Order To Max"
    r = _run_cfg([base], {"continuity_snap": 1, "prior_anchor_policy": "demand"}).iloc[0]
    assert r["factory_recommended_new_max"] == 1
    assert "CONTINUITY_SNAP" in r["reason_code"]
    assert "PRIOR_ANCHOR" not in r["reason_code"]


def test_prior_anchor_falls_back_to_current_without_a_prior():
    base = _row(**_LOW, replenishment_policy="Order To Demand")
    r = _run_cfg([base], {"continuity_snap": 1, "prior_anchor_policy": "demand"}).iloc[0]
    assert r["factory_recommended_new_max"] == 1
    assert "CONTINUITY_SNAP" in r["reason_code"]


def test_prior_anchor_needs_the_snap_band_to_be_open():
    """A prior decision far from both the engine and the level in force is an old
    number, not evidence -- the anchor may only fire inside the band.

    Anchoring stays ON here: the band is closed by DISTANCE, not by config, or
    the test would only be re-proving that switching the feature off works.
    """
    base = _row(max_qty=99, rop_qty=50, min_qty=10, **_HI, **_OTD)
    r = _run([base]).iloc[0]
    assert abs(r["factory_recommended_new_max"] - 99) > 1, "band must be closed"
    assert r["factory_recommended_new_max"] != 2          # not the prior decision
    assert "PRIOR_ANCHOR" not in r["reason_code"]
    assert "CONTINUITY_SNAP" not in r["reason_code"]


def test_anchor_refuses_a_non_monotonic_level_set():
    """rop_qty > max_qty is reachable in the extract. Snapping to it would be
    undone by the Max >= ROP >= Min clamp, shipping CONTINUITY_SNAP on a row the
    engine actually moved."""
    base = _row(**{**_LOW, "max_qty": 0, "rop_qty": 2, "min_qty": 0})
    r = _run([base]).iloc[0]
    assert "CONTINUITY_SNAP" not in r["reason_code"]
    assert r["factory_recommended_new_max"] >= r["factory_recommended_new_rop"]


def test_anchor_refuses_an_incomplete_level_set():
    """A blank rop_qty must not be read as ROP 0 -- that proposes never
    reordering an active part."""
    base = _row(**_LOW)
    base["rop_qty"] = ""
    r = _run([base]).iloc[0]
    assert "CONTINUITY_SNAP" not in r["reason_code"]


def test_critical_part_is_never_anchored_below_the_quantile():
    base = _row(**{**_LOW, "sfm_criticality": "H", "max_qty": 1,
                   "rop_qty": 0, "min_qty": 0})
    off = _run_cfg([base], _OFF).iloc[0]
    on = _run([base]).iloc[0]
    assert on["factory_recommended_new_max"] >= off["factory_recommended_new_max"]
    if off["factory_recommended_new_max"] > 1:
        assert "CRITICAL_NO_ANCHOR" in on["reason_code"]
        assert "CONTINUITY_SNAP" not in on["reason_code"]


def test_anchored_row_flags_an_unplaceable_moq():
    """Max >= ROP + MOQ is a quantile guarantee; the level in force carries no
    such promise, so say so instead of silently rounding the anchor away."""
    # The quantile floors Max at ROP + MOQ, so a big MOQ only stays inside the
    # band when the level in force is already MOQ-sized. Here it is (25), but its
    # ROP of 5 leaves only 20 of headroom for a 25-unit order.
    base = _row(**{**_LOW, "max_qty": 25, "rop_qty": 5, "min_qty": 0,
                   "order_qty_multiple": 25})
    r = _run([base]).iloc[0]
    assert "CONTINUITY_SNAP" in r["reason_code"]
    assert "MOQ_UNREACHABLE" in r["reason_code"]
    assert (r["factory_recommended_new_max"], r["factory_recommended_new_rop"]) == (25, 5)


def test_explanation_does_not_credit_the_quantile_for_an_anchored_number():
    snapped = _run([_row(**_LOW)]).iloc[0]
    assert "kept it unchanged" in snapped["explanation"]
    anchored = _run([_row(**_LOW, **_OTD)]).iloc[0]
    assert "last engineer decision" in anchored["explanation"]


def test_demand_model_levers_default_off_match_baseline():
    """The demand-model levers default to the current sizing (no silent regression).

    The two anchoring levers are deliberately NOT in this list: they are ON by
    default since 2026-09-10, and their own default is pinned by
    test_anchoring_levers_are_on_by_default below.
    """
    rows = [_row(**_CONST)]
    base = _run(rows).iloc[0]
    same = _run_cfg(rows, {"demand_estimator": "single", "trend_adjust": False,
                           "lead_time_sigma": False,
                           "service_level_mode": "criticality"}).iloc[0]
    for c in ("factory_recommended_new_max", "factory_recommended_new_rop",
              "factory_recommended_new_min"):
        assert base[c] == same[c]


def test_anchoring_levers_are_on_by_default():
    """Pinned on the population they target, so a silent default flip fails here."""
    base = _run([_row(**_LOW, **_OTD)]).iloc[0]
    assert base["factory_recommended_new_max"] == 2      # the prior decision
    assert base["factory_recommended_new_rop"] == 1
    assert "PRIOR_ANCHOR" in base["reason_code"]
    plain = _run([_row(**_LOW)]).iloc[0]
    assert plain["factory_recommended_new_max"] == 1     # the level in force
    assert "CONTINUITY_SNAP" in plain["reason_code"]


def test_anchoring_can_be_switched_back_off():
    """The escape hatch is the rollback path for a stocking decision -- keep it real."""
    rows = [_row(**_LOW, **_OTD)]
    off = _run_cfg(rows, {"continuity_snap": 0, "prior_anchor_policy": ""}).iloc[0]
    assert "CONTINUITY_SNAP" not in off["reason_code"]
    assert "PRIOR_ANCHOR" not in off["reason_code"]
    assert off["factory_recommended_new_max"] == 1       # the raw quantile answer


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


def _month(client, label, item_ids, **kw):
    rows = [make_row(item_id=i, module="TCB", frequencymonthswithusage=8,
                     sfm_criticality="M", contractual_lead_time=30,
                     max_qty=2, rop_qty=1, min_qty=0, **_CONST, **kw)
            for i in item_ids]
    up = upload(client, rows_to_csv(rows), label=label)
    assert up.status_code == 200, up.text
    bid = up.json()["batch_id"]
    r = client.post(f"/run-recommendation?batch_id={bid}", headers=ENG)
    assert r.status_code == 200, r.text
    return bid


def _scored(client, batch_id):
    return client.get(f"/recommendations?batch_id={batch_id}&limit=50",
                      headers=ENG).json()["items"]


def test_prior_review_benchmark_round_trips_through_score_batch(client, monkeypatch):
    """Second month of the same parts is graded against last month's decisions.

    Also the only guard on engine_adapter's hand-built positional INSERT: a
    column added to `cols` but not to the value tuple or the placeholder count
    shifts every field one slot, silently.
    """
    monkeypatch.setenv("BOM_ENGINE", "statistical")
    first = _month(client, "jan", ["P1", "P2"])
    for item in _scored(client, first):
        r = client.post(f"/review/{item['item_id']}?batch_id={first}"
                        f"&stockroom_id={item['stockroom_id']}",
                        json={"decision": "accept", "comment": "steady consumer",
                              "justification": "Matches observed demand"},
                        headers={**SENIOR, "X-User": "alice"})
        assert r.status_code == 200, r.text

    # Next month: same parts, engineer column blanked -- a real new upload.
    second = _month(client, "feb", ["P1", "P2"],
                    factory_recommended_new_max="",
                    factory_recommended_new_rop="",
                    factory_recommended_new_min="")
    got = _scored(client, second)
    assert got, "second batch scored nothing"
    assert {g["agreement_source"] for g in got} == {"prior_review"}
    assert {g["agreement"] for g in got} == {"match"}
    # Column-shift canary: these would hold values from a neighbouring slot.
    assert all(isinstance(g["new_max"], int)
               and g["risk_level"] in ("Low", "Medium", "High") for g in got)


def test_first_ever_month_has_no_benchmark(client, monkeypatch):
    """Nothing to fall back on. The honest answer is 'none', not a guess."""
    monkeypatch.setenv("BOM_ENGINE", "statistical")
    bid = _month(client, "cold", ["P9"], factory_recommended_new_max="",
                 factory_recommended_new_rop="", factory_recommended_new_min="")
    got = _scored(client, bid)
    assert got and all((g["agreement"], g["agreement_source"]) == ("none", "")
                       for g in got)


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
