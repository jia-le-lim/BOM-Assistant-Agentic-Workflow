"""Offline SBA/TSB sensitivity test on observed TCB snapshots, not monthly issues.

Irregular review snapshots cannot validate a calendar-period demand model. This
experiment compresses missing periods, explicitly as a proxy, without imputing
zero demand. It changes only the rate in the frozen current sizing engine.
"""
from __future__ import annotations

import json
from collections import defaultdict
from unittest.mock import patch

import numpy as np
import pandas as pd

from s27_chronological_calibration import (
    BASELINE, E, LEVELS, OUT as SOURCE, ROOT, candidates, engine_input,
    load_inputs, matched, predictions, save_json, score_cycles, sha,
)
from s28_pooled_months import aggregate_scores, cluster_gain_interval

OUT = ROOT / "analysis/output/s29_sba_tsb"
MIN_OBSERVATIONS = 3
ALPHAS = (.05, .10, .20, .30)
BASE_SPEC = next(c for c in candidates() if c["name"] == BASELINE)
REFERENCE = "https://github.com/Nixtla/statsforecast/blob/main/python/statsforecast/models.py"


def ses(values, alpha):
    state = float(values[0])
    for value in values[1:]:
        state += alpha * (float(value) - state)
    return state


def intermittent_rate(observed, method, alpha=.1, beta=.1):
    """Forecast per observed 30-day window, converted to units/day.

    SBA initializes size/interval from the first positive observation; TSB
    initializes probability from the first observed indicator. Both use only
    the supplied historical prefix. TSB updates probability on observed zeros.
    No observations representing missing calendar months are constructed here.
    """
    y = np.asarray(observed, dtype=float)
    if method not in ("sba", "tsb"):
        raise ValueError("Unknown intermittent method")
    if not (0 < alpha <= 1 and 0 < beta <= 1):
        raise ValueError("Smoothing parameters must be in (0, 1]")
    if not y.size or not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("Expected finite, nonnegative observed demand")
    positions = np.flatnonzero(y > 0)
    if not positions.size:
        return 0.
    size = ses(y[positions], alpha)
    if method == "sba":
        intervals = np.diff(positions + 1, prepend=0)
        forecast = (1 - alpha / 2) * size / ses(intervals, alpha)
    else:
        forecast = size * ses((y > 0).astype(float), beta)
    return float(forecast / 30)


def observed_histories(frame):
    """Use earlier available snapshots of the same item AND stockroom.

    Current demand features are available for the current sizing decision,
    exactly as for the baseline. Current approved decisions are never read.
    """
    columns = ["item_id", "stockroom_id", "_cycle", "_available_date", E.CONS[30]]
    df = frame.reindex(columns=columns).reset_index(drop=True)
    if df.duplicated(["item_id", "stockroom_id", "_cycle"]).any():
        raise ValueError("Duplicate item-stockroom-cycle")
    history = defaultdict(list)
    series, audits = [None] * len(df), [None] * len(df)
    for cycle, group in df.groupby("_cycle", sort=True):
        cutoff = cycle + "-01"
        for i, row in group.iterrows():
            key = (str(row.item_id), str(row.stockroom_id))
            prior = [r for r in history[key] if r[1] < cutoff]
            current = pd.to_numeric(row[E.CONS[30]], errors="coerce")
            seen = prior + [(cycle, str(row._available_date), current)]
            seen = [r for r in seen if np.isfinite(r[2]) and r[2] >= 0]
            assert all(r[0] < cycle and r[1] < cutoff for r in prior)
            values = np.array([r[2] for r in seen], dtype=float)
            ordinals = [pd.Period(r[0], freq="M").ordinal for r in seen]
            gaps = np.diff(ordinals)
            suffix = 1 if seen else 0
            for gap in gaps[::-1]:
                if gap != 1:
                    break
                suffix += 1
            series[i] = values
            audits[i] = {"observations": len(values), "positive_observations": int((values > 0).sum()),
                         "span_months": ordinals[-1] - ordinals[0] + 1 if seen else 0,
                         "unobserved_months": int(sum(gaps - 1)),
                         "max_gap_months": int(max(gaps)) if len(gaps) else 0,
                         "consecutive_review_months": suffix,
                         "latest_prior_cycle": prior[-1][0] if prior else "",
                         "latest_prior_available_date": prior[-1][1] if prior else ""}
        for _, row in group.iterrows():
            y = pd.to_numeric(row[E.CONS[30]], errors="coerce")
            if np.isfinite(y) and y >= 0:
                history[(str(row.item_id), str(row.stockroom_id))].append(
                    (cycle, str(row._available_date), float(y)))
    return series, pd.DataFrame(audits)


def specs():
    result = []
    for alpha in ALPHAS:
        result.append(dict(name=f"sba_a{alpha:.2f}", method="sba", alpha=alpha, beta=.1))
        for beta in ALPHAS:
            result.append(dict(name=f"tsb_a{alpha:.2f}_b{beta:.2f}", method="tsb", alpha=alpha, beta=beta))
    result.append(dict(name="sba_active_tsb_dying", method="hybrid", alpha=.1, beta=.1))
    for name in ("sba_a0.10", "tsb_a0.10_b0.10", "sba_active_tsb_dying"):
        original = next(s for s in result if s["name"] == name)
        result.append({**original, "name": name + "_blend50", "blend": .5})
        result.append({**original, "name": name + "_guarded", "guarded": True})
    return result


def injected_predictions(frame, config, rates):
    """Temporary, process-local substitution; never edits the engine or DB."""
    rates = np.asarray(rates, dtype=float)
    if len(rates) != len(frame) or not np.isfinite(rates).all() or (rates < 0).any():
        raise ValueError("One finite nonnegative daily rate per input row required")
    cursor = iter(rates)
    with patch.object(E, "_estimate_mu", side_effect=lambda *_: next(cursor)) as estimator:
        result = E.run(engine_input(frame, BASE_SPEC), {**config, **BASE_SPEC["cfg"]})
        assert estimator.call_count == len(frame)
    values = result[["factory_recommended_new_" + l for l in LEVELS]].to_numpy(float)
    assert np.isfinite(values).all() and (values >= 0).all()
    assert (values[:, 0] >= values[:, 1]).all() and (values[:, 1] >= values[:, 2]).all()
    return values, result.route.to_numpy()


def guarded_values(values, baseline, critical):
    veto = ((values[:, :2] == 0) & (baseline[:, :2] > 0)).any(axis=1)
    veto |= np.asarray(critical) & (values[:, :2] < baseline[:, :2]).any(axis=1)
    result = values.copy()
    result[veto] = baseline[veto]
    return result, veto


def choose_from_past(monthly, eligible_cycles, family):
    """Choose a smoothing variant from earlier cycles; never use current labels."""
    prefix = "sba_" if family == "sba" else "tsb_"
    names = {s["name"] for s in specs() if s["method"] == family and not s.get("blend") and not s.get("guarded")}
    names.add(BASELINE)
    past = monthly[monthly.cycle.isin(eligible_cycles) & monthly.segment.eq("live") & monthly.candidate.isin(names)]
    if len(eligible_cycles) < 2 or past.empty:
        return BASELINE
    pooled = aggregate_scores(past).set_index("candidate")
    baseline = pooled.loc[BASELINE]
    allowed = pooled.match_pct.gt(baseline.match_pct)
    for col in ("severe_under", "zero_max_errors", "zero_rop_errors"):
        allowed &= pooled[col].le(baseline[col])
    allowed &= pooled.critical_reductions_vs_baseline.eq(0)
    if not allowed.any():
        return BASELINE
    return pooled[allowed].sort_values(["match_pct", "max_mae", "candidate"], ascending=[False, True, True]).index[0]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    panel, config = load_inputs(SOURCE)
    protected = [SOURCE / f for f in ("panel.pkl", "config.json", "selection.json", "holdout_summary.json")]
    protected += [ROOT / "backend/app/engine_statistical.py"]
    before = {str(p.relative_to(ROOT)): sha(p) for p in protected}
    protocol = {
        "experiment": "Exploratory observed-snapshot SBA/TSB rate substitution",
        "calendar_monthly_validation": False,
        "primary_scope": "All eligible active/dying item-stockroom-cycle rows; dormant policy unchanged",
        "observations": "Observed last_30_day consumption, ordered by review cycle; missing months omitted, never zero-filled. Each observed window is treated as one model step, so gaps compress time and snapshot windows may overlap. This is a proxy, not a reconstructed monthly demand series.",
        "history_rule": "Earlier cycle and decision availability before current cycle start; current demand is allowed, current approvals are not",
        "minimum_observations": MIN_OBSERVATIONS,
        "cold_start": "Current engine below three observed snapshots; no synthetic demand seed",
        "rate_units": "Forecast per observed 30-day window divided by 30",
        "candidate_grid": specs(),
        "main_candidates": [BASELINE, "sba_a0.10", "tsb_a0.10_b0.10", "sba_active_tsb_dying"],
        "hybrid": "SBA active; TSB dying; route determined by unchanged original consumption windows",
        "blend50": "Equal mixture of candidate daily rate and current cumulative-window daily rate",
        "guarded": "Keep baseline full triple if candidate newly zeros positive Max/ROP or lowers a critical Max/ROP",
        "metric": "Strict simultaneous Max and ROP agreement within 10% relative of engineer; zero requires exact zero",
        "risk_proxies": "Severe undersizing (>max(1 unit,25% of approved level)), zero-Max/ROP errors, critical reductions",
        "walk_forward": "Before each cycle choose using only whole earlier cycles whose labels are all available, minimum two cycles, strict improvement and no worse pooled risk proxies. August has been inspected in prior experiments; no new independent holdout claim.",
        "reference": REFERENCE,
        "inference_limit": "Review agreement is not forecast accuracy, inventory service, or savings; observed-snapshot performance is not calendar-period SBA/TSB performance",
        "production_changed": False,
    }
    save_json(OUT / "protocol.json", protocol)  # Written before scoring any candidate.
    series, audit = observed_histories(panel)
    baseline, routes = predictions(panel, config, BASE_SPEC)
    live = np.isin(routes, ("active", "dying"))
    eligible = live & audit.observations.ge(MIN_OBSERVATIONS).to_numpy()
    eligible_positions = np.flatnonzero(eligible)
    part = panel[eligible].reset_index(drop=True)
    critical = panel.sfm_criticality.fillna("").astype(str).str.strip().str.lower().str.startswith("h").to_numpy()
    base_rates = np.array([E._estimate_mu({w: pd.to_numeric(r.get(c), errors="coerce") for w, c in E.CONS.items()}, "single", False)
                           for r in part.to_dict("records")])
    parity, parity_routes = injected_predictions(part, config, base_rates)
    np.testing.assert_array_equal(parity, baseline[eligible])
    np.testing.assert_array_equal(parity_routes, routes[eligible])
    audit = pd.concat([panel[["item_id", "stockroom_id", "_cycle", "_available_date"]], audit], axis=1)
    audit["route"], audit["candidate_eligible"] = routes, eligible
    audit.to_csv(OUT / "observation_coverage.csv", index=False)
    values_by_name = {BASELINE: baseline}
    rows = score_cycles(panel, baseline, baseline, routes, BASELINE)
    changes = []
    for spec in specs():
        rate = []
        for i in eligible_positions:
            method = spec["method"]
            if method == "hybrid":
                method = "sba" if routes[i] == "active" else "tsb"
            rate.append(intermittent_rate(series[i], method, spec["alpha"], spec["beta"]))
        rate = np.array(rate)
        if spec.get("blend"):
            rate = spec["blend"] * rate + (1 - spec["blend"]) * base_rates
        values = baseline.copy()
        values[eligible], candidate_routes = injected_predictions(part, config, rate)
        np.testing.assert_array_equal(candidate_routes, routes[eligible])
        veto = np.zeros(len(panel), dtype=bool)
        if spec.get("guarded"):
            values, veto = guarded_values(values, baseline, critical)
        np.testing.assert_array_equal(values[~eligible], baseline[~eligible])
        values_by_name[spec["name"]] = values
        rows.extend(score_cycles(panel, values, baseline, routes, spec["name"]))
        changes.append(dict(candidate=spec["name"], attempted_rows=int(eligible.sum()), vetoed_rows=int(veto.sum()),
                            changed_rows=int((values != baseline).any(axis=1).sum())))
        print(f"Scored {spec['name']}", flush=True)
    monthly = pd.DataFrame(rows)
    # Retrospective parameter selection restricted to previously available labels.
    choices = []
    for family in ("sba", "tsb"):
        values = baseline.copy()
        for cycle in sorted(panel._cycle.unique()):
            earlier = panel[panel._cycle.lt(cycle)].groupby("_cycle")._available_date.max()
            available = earlier[earlier.lt(cycle + "-01")].index.tolist()
            selected = choose_from_past(monthly, available, family)
            mask = panel._cycle.eq(cycle).to_numpy()
            values[mask] = values_by_name[selected][mask]
            choices.append(dict(family=family, cycle=cycle, selected=selected, training_cycles=";".join(available)))
        name = family + "_walk_forward"
        values_by_name[name] = values
        rows.extend(score_cycles(panel, values, baseline, routes, name))
    monthly = pd.DataFrame(rows)
    combined = aggregate_scores(monthly)
    live_scores = combined[combined.segment.eq("live")].copy()
    base_score = live_scores[live_scores.candidate.eq(BASELINE)].iloc[0]
    live_scores["gain_pp"] = live_scores.match_pct - base_score.match_pct
    live_scores["passes_error_checks"] = live_scores.critical_reductions_vs_baseline.eq(0)
    for col in ("severe_under", "zero_max_errors", "zero_rop_errors"):
        live_scores["passes_error_checks"] &= live_scores[col].le(base_score[col])
    live_scores = live_scores.merge(pd.DataFrame(changes), on="candidate", how="left")
    monthly.to_csv(OUT / "scores_by_cycle.csv", index=False)
    combined.to_csv(OUT / "scores_all_months.csv", index=False)
    live_scores.sort_values("match_pct", ascending=False).to_csv(OUT / "live_ranking.csv", index=False)
    pd.DataFrame(choices).to_csv(OUT / "walk_forward_choices.csv", index=False)
    eligible_rows = []
    for name, values in values_by_name.items():
        eligible_rows.extend(score_cycles(panel[eligible], values[eligible], baseline[eligible], routes[eligible], name))
    aggregate_scores(pd.DataFrame(eligible_rows)).to_csv(OUT / "scores_eligible_only.csv", index=False)
    main_names = protocol["main_candidates"] + ["sba_active_tsb_dying_blend50", "sba_active_tsb_dying_guarded", "sba_walk_forward", "tsb_walk_forward"]
    main_scores = combined[combined.candidate.isin(main_names)]
    main_scores.to_csv(OUT / "main_comparison.csv", index=False)
    export = panel[["item_id", "stockroom_id", "_cycle", "_final_max", "_final_rop", "_final_min"]].copy()
    export["route"], export["candidate_eligible"] = routes, eligible
    for name in main_names:
        for j, level in enumerate(LEVELS):
            export[name + "_" + level] = values_by_name[name][:, j]
    export.to_csv(OUT / "main_predictions.csv", index=False)
    # Reconcile baseline with the already-reviewed S28 output.
    old = pd.read_csv(ROOT / "analysis/output/s28_pooled_months/all_months_combined.csv")
    for segment in ("all", "live", "active", "dying", "dormant"):
        previous = old[old.candidate.eq(BASELINE) & old.segment.eq(segment)].iloc[0]
        now = combined[combined.candidate.eq(BASELINE) & combined.segment.eq(segment)].iloc[0]
        for col in ("n", "matches", "severe_under", "zero_max_errors", "zero_rop_errors"):
            assert now[col] == previous[col], (segment, col)
    intervals = {name: cluster_gain_interval(panel[live], values_by_name[name][live], baseline[live]) for name in main_names if name != BASELINE}
    counts = panel.groupby(["item_id", "stockroom_id"]).size()
    summary = {
        "review_rows": len(panel), "cycles": int(panel._cycle.nunique()), "start": panel._cycle.min(), "end": panel._cycle.max(),
        "active_dying_rows": int(live.sum()), "dormant_rows": int((routes == "dormant").sum()),
        "attempted_sba_tsb_rows": int(eligible.sum()), "fallback_live_rows": int((live & ~eligible).sum()),
        "attempted_pct_of_live": float(eligible.sum() / live.sum() * 100),
        "max_observations_per_pair": int(counts.max()), "median_observations_per_pair": float(counts.median()),
        "max_consecutive_review_months": int(audit.consecutive_review_months.max()),
        "live_rows_with_12_consecutive_review_months": int((live & audit.consecutive_review_months.ge(12).to_numpy()).sum()),
        "eligible_rows_without_any_positive_observation": int((eligible & audit.positive_observations.eq(0).to_numpy()).sum()),
        "candidate_configurations": len(specs()), "main_names": main_names, "intervals": intervals,
        "baseline_reconciles_s28": True, "rate_injection_parity_passed": True,
        "unchanged_source_hashes": before, "script_sha256": sha(ROOT / "analysis/s29_sba_tsb_backtest.py"),
        "evaluation": "Exploratory observed-snapshot sensitivity; not independent holdout or valid monthly-period forecast validation",
        "production_changed": False,
    }
    assert before == {str(p.relative_to(ROOT)): sha(p) for p in protected}
    save_json(OUT / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("intervals", "unchanged_source_hashes")}, indent=2))
    print(live_scores[["candidate", "n", "matches", "match_pct", "gain_pp", "severe_under", "zero_max_errors", "zero_rop_errors", "passes_error_checks"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
