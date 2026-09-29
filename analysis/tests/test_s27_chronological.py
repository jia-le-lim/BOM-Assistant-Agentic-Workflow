import numpy as np
import pandas as pd

from s27_chronological_calibration import (
    BASELINE, INPUTS, attach_priors, candidates, engine_input, predictions,
    resolve_source, ranking,
)


def record(cycle, stockroom="24", final=3, available=None):
    return {"item_id": "part", "stockroom_id": stockroom, "_cycle": cycle,
            "_available_date": available or cycle + "-20", "_final_max": final,
            "_final_rop": 1, "_final_min": 0, "last_365_day_cnsmptn_qty": 5}


def test_prior_requires_earlier_cycle_and_does_not_cross_stockrooms():
    result = attach_priors(pd.DataFrame([
        record("2026-01", "24", 4), record("2026-01", "33", 9),
        record("2026-03", "24", 6), record("2026-03", "33", 11),
    ]))
    first = result[result._cycle.eq("2026-01")]
    assert first.prior_final_max.isna().all()
    later = result[result._cycle.eq("2026-03")].set_index("stockroom_id")
    assert later.loc["24", "prior_final_max"] == 4
    assert later.loc["33", "prior_final_max"] == 9


def test_late_review_does_not_enter_an_earlier_prediction():
    result = attach_priors(pd.DataFrame([
        record("2025-10", final=2),
        record("2026-01", final=7, available="2026-04-02"),
        record("2026-03", final=8),
    ]))
    assert result.iloc[-1].prior_final_max == 2


def test_future_outcomes_do_not_change_earlier_priors():
    data = pd.DataFrame([record("2026-01"), record("2026-03"), record("2026-08")])
    before = attach_priors(data)
    data.loc[2, "_final_max"] = 9999
    after = attach_priors(data)
    pd.testing.assert_frame_equal(before.iloc[:2], after.iloc[:2])


def test_january_uses_archive_only_when_decisions_agree():
    row = {"item_id": "part", "stockroom_id": "24", "reviewed_at": "2025-08-19",
           "final_max": 3, "final_rop": 1, "final_min": 0}
    original = {"max_qty": "1"}
    archive = {("part", "24", "2026-01"): {
        "max_qty": "4", "factory_recommended_new_max": "3",
        "factory_recommended_new_rop": "1", "factory_recommended_new_min": "0",
        "source_modified_date": "01/19/2026"}}
    payload, available, status = resolve_source(row, original, "2026-01", archive)
    assert payload["max_qty"] == "4" and available == "2026-01-19"
    assert status == "archive_reconciled"
    row["final_max"] = 12
    assert resolve_source(row, original, "2026-01", archive)[0] is None
    assert resolve_source(row, original, "2026-01", {})[2] == "unresolved_timestamp"


def test_model_input_cannot_include_same_cycle_answers():
    source = {c: "" for c in INPUTS}
    source.update(item_id="part", stockroom_id="24", module="TCB", max_qty="2",
                  rop_qty="1", min_qty="0", unitprice="10", contractual_lead_time="30",
                  last_365_day_cnsmptn_qty="4", last_90_day_cnsmptn_qty="2",
                  sfm_criticality="l", _final_max=900, factory_recommended_new_max=900,
                  comments="Keep 900", _prior_age_months=2,
                  prior_final_max=3, prior_final_rop=1, prior_final_min=0, prior_c365=4)
    frame = pd.DataFrame([source])
    candidate = next(c for c in candidates() if c["name"] == BASELINE)
    cleaned = engine_input(frame, candidate)
    assert "_final_max" not in cleaned and "factory_recommended_new_max" not in cleaned
    before, _ = predictions(frame, {}, candidate)
    frame["factory_recommended_new_max"] = 0
    frame["_final_max"] = 0
    after, _ = predictions(frame, {}, candidate)
    np.testing.assert_array_equal(before, after)


def test_guard_rejects_changed_demand_and_old_prior():
    candidate = next(c for c in candidates() if c["name"] == "single_flat_guard25_1")
    frame = pd.DataFrame([
        {"last_365_day_cnsmptn_qty": 10, "prior_c365": 10, "_prior_age_months": 2, "prior_final_max": 3},
        {"last_365_day_cnsmptn_qty": 20, "prior_c365": 10, "_prior_age_months": 2, "prior_final_max": 3},
        {"last_365_day_cnsmptn_qty": 10, "prior_c365": 10, "_prior_age_months": 7, "prior_final_max": 3},
    ])
    result = engine_input(frame, candidate)
    assert result.loc[0, "prior_final_max"] == 3
    assert result.loc[1:, "prior_final_max"].isna().all()


def test_ranking_excludes_high_match_candidate_that_increases_zero_errors():
    rows = []
    for name, rate, zero in [(BASELINE, 50, 0), ("blended_flat_prior1", 80, 2)]:
        for cycle in ("2026-03", "2026-05"):
            rows.append(dict(candidate=name, cycle=cycle, segment="live", n=100,
                             matches=rate, match_pct=rate, max_mae=1,
                             severe_under=0, zero_max_errors=zero, zero_rop_errors=0,
                             critical_reductions_vs_baseline=0))
    result = ranking(pd.DataFrame(rows), ["2026-03", "2026-05"]).set_index("candidate")
    assert result.loc[BASELINE, "eligible"]
    assert not result.loc["blended_flat_prior1", "eligible"]


def test_positive_level_veto_prevents_new_zero_levels_without_using_labels():
    data = []
    for demand in range(1, 31):
        row = {c: "" for c in INPUTS}
        row.update(item_id=str(demand), stockroom_id="24", module="TCB",
                   max_qty="0", rop_qty="0", min_qty="0", unitprice="10",
                   contractual_lead_time="30", last_365_day_cnsmptn_qty=str(demand),
                   last_90_day_cnsmptn_qty="1", sfm_criticality="l",
                   _prior_age_months=np.nan)
        data.append(row)
    frame = pd.DataFrame(data)
    specs = {c["name"]: c for c in candidates()}
    baseline, _ = predictions(frame, {}, specs[BASELINE])
    wider, _ = predictions(frame, {}, specs["single_flat_prior2"])
    guarded, _ = predictions(frame, {}, specs["single_flat_prior2_positive"])
    newly_zero = ((wider[:, :2] == 0) & (baseline[:, :2] > 0)).any(axis=1)
    assert newly_zero.any()
    np.testing.assert_array_equal(guarded[newly_zero], baseline[newly_zero])
    assert not ((guarded[:, :2] == 0) & (baseline[:, :2] > 0)).any()
