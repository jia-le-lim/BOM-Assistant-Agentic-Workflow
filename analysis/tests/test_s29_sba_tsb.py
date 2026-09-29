import numpy as np
import pandas as pd
import pytest

from s27_chronological_calibration import INPUTS, predictions
from s29_sba_tsb_backtest import (
    BASELINE, BASE_SPEC, E, choose_from_past, guarded_values,
    injected_predictions, intermittent_rate, observed_histories,
)


def test_sba_matches_hand_calculated_size_interval_and_bias():
    # Positive sizes 4,2 => 3.8; occurrence intervals 3,2 => 2.9.
    assert intermittent_rate([0, 0, 4, 0, 2], "sba") == pytest.approx(.95 * 3.8 / 2.9 / 30)
    assert intermittent_rate([0, 0, 4, 0, 2, 0, 0], "sba") == pytest.approx(.95 * 3.8 / 2.9 / 30)


def test_tsb_updates_probability_on_zero_and_initializes_without_future():
    assert intermittent_rate([10, 0, 0], "tsb") == pytest.approx(10 * .9 ** 2 / 30)
    # Probabilities 0,0,.1,.09,.181; positive size estimate 3.8.
    assert intermittent_rate([0, 0, 4, 0, 2], "tsb") == pytest.approx(.181 * 3.8 / 30)
    assert intermittent_rate([0, 0], "tsb") == 0


@pytest.mark.parametrize("method", ["sba", "tsb"])
def test_intermitttent_models_handle_all_zero_constant_and_bad_input(method):
    assert intermittent_rate([0, 0, 0], method) == 0
    assert intermittent_rate([6, 6, 6], method) == pytest.approx((.95 if method == "sba" else 1) * 6 / 30)
    for y in ([np.nan], [-1], []):
        with pytest.raises(ValueError):
            intermittent_rate(y, method)


def test_history_skips_missing_months_and_unavailable_or_other_stockroom():
    def row(cycle, y, room="24", available=None):
        return dict(item_id="part", stockroom_id=room, _cycle=cycle,
                    _available_date=available or cycle + "-15",
                    last_30_day_cnsmptn_qty=y, _final_max=900)
    frame = pd.DataFrame([
        row("2025-01", 3), row("2025-03", 999, available="2025-06-15"),
        row("2025-04", 99, room="25"), row("2025-05", 0),
        row("2025-08", 1),
    ])
    series, audit = observed_histories(frame)
    np.testing.assert_array_equal(series[3], [3, 0])
    assert audit.loc[3, "unobserved_months"] == 3
    assert audit.loc[3, "observations"] == 2  # NOT five synthetic periods.
    assert audit.loc[3, "latest_prior_cycle"] == "2025-01"
    changed = frame.copy()
    changed.loc[4, "last_30_day_cnsmptn_qty"] = 777
    changed["_final_max"] = 0
    after, _ = observed_histories(changed)
    for i in range(4):
        np.testing.assert_array_equal(series[i], after[i])


def test_history_rejects_duplicate_cycle():
    row = dict(item_id="p", stockroom_id="24", _cycle="2025-01", _available_date="2025-01-20", last_30_day_cnsmptn_qty=1)
    with pytest.raises(ValueError, match="Duplicate"):
        observed_histories(pd.DataFrame([row, row]))


def test_rate_substitution_preserves_row_order_labels_and_engine_function():
    rows = []
    for i, rate in enumerate([.01, .5]):
        row = {c: "" for c in INPUTS}
        row.update(item_id=str(i), stockroom_id="24", module="TCB", max_qty="0", rop_qty="0", min_qty="0",
                   last_365_day_cnsmptn_qty=rate * 365, last_90_day_cnsmptn_qty=rate * 90,
                   contractual_lead_time=30, order_qty_multiple=1, sfm_criticality="l",
                   _final_max=900, factory_recommended_new_max=900)
        rows.append(row)
    frame = pd.DataFrame(rows)
    original = E._estimate_mu
    baseline, routes = predictions(frame, {}, BASE_SPEC)
    actual, injected_routes = injected_predictions(frame, {}, [.01, .5])
    np.testing.assert_array_equal(actual, baseline)
    np.testing.assert_array_equal(injected_routes, routes)
    assert E._estimate_mu is original
    frame["_final_max"] = frame["factory_recommended_new_max"] = 0
    again, _ = injected_predictions(frame, {}, [.01, .5])
    np.testing.assert_array_equal(actual, again)
    reverse, _ = injected_predictions(frame.iloc[::-1], {}, [.5, .01])
    np.testing.assert_array_equal(reverse, actual[::-1])


def test_guard_restores_all_three_levels_for_new_zero_or_critical_reduction():
    baseline = np.array([[3, 2, 1], [4, 3, 2], [4, 2, 1]], dtype=float)
    values = np.array([[2, 0, 0], [3, 2, 1], [3, 1, 0]], dtype=float)
    result, veto = guarded_values(values, baseline, [False, True, False])
    np.testing.assert_array_equal(veto, [True, True, False])
    np.testing.assert_array_equal(result[:2], baseline[:2])
    np.testing.assert_array_equal(result[2], values[2])


def test_walk_forward_cannot_select_from_future_scores_and_obeys_error_gate():
    rows = []
    for cycle in ["2025-01", "2025-03", "2025-05"]:
        for name in [BASELINE, "sba_a0.10"]:
            hits = 60 if name == BASELINE else (50 if cycle < "2025-05" else 100)
            rows.append(dict(candidate=name, segment="live", cycle=cycle, n=100, matches=hits,
                             match_pct=hits, severe_under=0, zero_max_errors=0, zero_rop_errors=0,
                             critical_reductions_vs_baseline=0, critical_n=0, priced_n=0, max_mae=1, rop_mae=1))
    scores = pd.DataFrame(rows)
    assert choose_from_past(scores, ["2025-01", "2025-03"], "sba") == BASELINE
    mask = scores.candidate.eq("sba_a0.10")
    scores.loc[mask, ["matches", "match_pct"]] = 90
    assert choose_from_past(scores, ["2025-01", "2025-03"], "sba") == "sba_a0.10"
    scores.loc[mask, "zero_rop_errors"] = 1
    assert choose_from_past(scores, ["2025-01", "2025-03"], "sba") == BASELINE
