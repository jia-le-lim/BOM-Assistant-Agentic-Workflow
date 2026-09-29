"""Combine every eligible TCB cycle and compare methods on pooled review rows.

Includes the previously opened August holdout. These are descriptive historical
results, not a replacement independent test. S27 evidence remains unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from s27_chronological_calibration import (
    BASELINE, HOLDOUT, OUT as SOURCE, ROOT, candidates, load_inputs, predictions,
    benchmark_predictions, score_cycles, save_json, sha, matched,
)

OUT = ROOT / "analysis/output/s28_pooled_months"
COUNT_METRICS = ["matches", "severe_under", "zero_max_errors", "zero_rop_errors",
                 "critical_reductions_vs_baseline", "critical_n", "priced_n"]


def aggregate_scores(monthly):
    if monthly.duplicated(["candidate", "cycle", "segment"]).any():
        raise ValueError("Duplicate candidate/cycle/segment score rows")
    records = []
    for (name, segment), group in monthly.groupby(["candidate", "segment"]):
        n = int(group.n.sum())
        row = {"candidate": name, "segment": segment, "cycles": group.cycle.nunique(), "n": n}
        row.update({c: int(group[c].sum()) for c in COUNT_METRICS})
        row["match_pct"] = row["matches"] / n * 100
        row["equal_cycle_match_pct"] = float(group.match_pct.mean())
        for c in ("max_mae", "rop_mae"):
            row[c] = float((group[c] * group.n).sum() / n)
        records.append(row)
    return pd.DataFrame(records)


def cluster_gain_interval(frame, candidate, baseline):
    """Resample item-stockroom pairs, retaining all their monthly observations."""
    truth = frame[["_final_max", "_final_rop"]].to_numpy(float)
    difference = matched(candidate[:, :2], truth).astype(int) - matched(baseline[:, :2], truth).astype(int)
    clusters = frame[["item_id", "stockroom_id"]].copy()
    clusters["difference"], clusters["n"] = difference, 1
    totals = clusters.groupby(["item_id", "stockroom_id"])[["difference", "n"]].sum().to_numpy()
    rng = np.random.default_rng(280911)
    indices = rng.integers(0, len(totals), size=(4000, len(totals)))
    sampled = totals[indices].sum(axis=1)
    gains = 100 * sampled[:, 0] / sampled[:, 1]
    return {"unique_item_stockroom_pairs": len(totals),
            "gain_pp": float(difference.mean() * 100),
            "paired_cluster_bootstrap_95pct_interval_pp": np.percentile(gains, [2.5, 97.5]).tolist()}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    previous_hashes = {name: sha(SOURCE / name) for name in (
        "selection.json", "protocol.json", "holdout_summary.json", "development_scores.csv")}
    panel, config = load_inputs(SOURCE)
    assert not panel.duplicated(["item_id", "stockroom_id", "_cycle"]).any()
    panel.to_csv(OUT / "combined_tcb_reviews.csv", index=False)
    specs = {c["name"]: c for c in candidates()}
    old = pd.read_csv(SOURCE / "development_scores.csv")
    assert set(old.candidate) == set(specs) | {"hold_current", "last_approved"}
    assert old.cycle.max() < HOLDOUT
    august = panel[panel._cycle.eq(HOLDOUT)].reset_index(drop=True)
    baseline, routes = predictions(august, config, specs[BASELINE])
    live = np.isin(routes, ("active", "dying"))
    rows = score_cycles(august, baseline, baseline, routes, BASELINE)
    for name, candidate in specs.items():
        if name == BASELINE:
            continue
        values = baseline.copy()
        values[live], candidate_routes = predictions(august[live].reset_index(drop=True), config, candidate)
        assert np.array_equal(candidate_routes, routes[live])
        rows.extend(score_cycles(august, values, baseline, routes, name))
    for name, values in benchmark_predictions(august, baseline).items():
        rows.extend(score_cycles(august, values, baseline, routes, name))
    monthly = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
    combined = aggregate_scores(monthly)
    all_rows = combined[combined.segment.eq("all")]
    assert all_rows.n.eq(len(panel)).all()
    assert all_rows.cycles.eq(panel._cycle.nunique()).all()
    live_scores = combined[combined.segment.eq("live")].set_index("candidate")
    base_score = live_scores.loc[BASELINE]
    live_scores["gain_vs_current_pp"] = live_scores.match_pct - base_score.match_pct
    live_scores["passes_pooled_error_checks"] = (
        live_scores.severe_under.le(base_score.severe_under)
        & live_scores.zero_max_errors.le(base_score.zero_max_errors)
        & live_scores.zero_rop_errors.le(base_score.zero_rop_errors)
        & live_scores.critical_reductions_vs_baseline.eq(0))
    live_scores["is_engine_variant"] = live_scores.index.isin(specs)
    ranked = live_scores.sort_values(["match_pct", "max_mae"], ascending=[False, True])
    raw_best = ranked[ranked.is_engine_variant].index[0]
    screened_best = ranked[ranked.is_engine_variant & ranked.passes_pooled_error_checks].index[0]
    monthly.to_csv(OUT / "all_months_by_cycle.csv", index=False)
    combined.to_csv(OUT / "all_months_combined.csv", index=False)
    ranked.reset_index().to_csv(OUT / "pooled_live_ranking.csv", index=False)

    # Independently rescore the full panel for the baseline, selected method,
    # and pooled leaders; verify the pooled denominator/matches from row values.
    earlier_choice = json.loads((SOURCE / "selection.json").read_text())["selected"]["name"]
    full_base, full_routes = predictions(panel, config, specs[BASELINE])
    full_live = np.isin(full_routes, ("active", "dying"))
    targets = panel[["_final_max", "_final_rop"]].to_numpy(float)
    intervals = {}
    inspect = list(dict.fromkeys([BASELINE, earlier_choice, raw_best, screened_best]))
    for name in inspect:
        values, _ = predictions(panel, config, specs[name])
        score = live_scores.loc[name]
        hits = int(matched(values[full_live, :2], targets[full_live]).sum())
        assert hits == int(score.matches)
        assert int(full_live.sum()) == int(score.n)
        all_score = all_rows[all_rows.candidate.eq(name)].iloc[0]
        assert int(matched(values[:, :2], targets).sum()) == int(all_score.matches)
        if name != BASELINE:
            intervals[name] = cluster_gain_interval(panel[full_live], values[full_live], full_base[full_live])
    # Comparators are checked on full row predictions as well.
    for name, values in benchmark_predictions(panel, full_base).items():
        assert int(matched(values[full_live, :2], targets[full_live]).sum()) == int(live_scores.loc[name, "matches"])
    comparison_names = list(dict.fromkeys([BASELINE, earlier_choice, raw_best, screened_best,
                                         "hold_current", "last_approved"]))
    comparison = combined[combined.candidate.isin(comparison_names)].copy()
    comparison.to_csv(OUT / "main_comparison.csv", index=False)
    assert previous_hashes == {name: sha(SOURCE / name) for name in previous_hashes}
    summary = {
        "scope": "All available eligible historical review cycles combined, including previously opened August 2026",
        "evaluation_type": "Pooled descriptive comparison; not independent out-of-sample validation",
        "weighting": "Every item-stockroom-cycle review has equal weight; percentages use total matches / total rows",
        "period_start": panel._cycle.min(), "period_end": panel._cycle.max(),
        "review_cycles": panel._cycle.nunique(), "eligible_reviews": len(panel),
        "unique_item_stockroom_pairs": len(panel[["item_id", "stockroom_id"]].drop_duplicates()),
        "live_reviews": int(full_live.sum()), "dormant_reviews": int((full_routes == "dormant").sum()),
        "engine_configurations": len(specs), "comparators": 2,
        "best_engine_by_pooled_agreement": raw_best,
        "best_engine_passing_pooled_error_checks": screened_best,
        "previously_selected_candidate": earlier_choice,
        "source_panel_sha256": sha(SOURCE / "panel.pkl"), "original_evidence_hashes": previous_hashes,
        "intervals": intervals, "production_changed": False,
        "independent_full_panel_reconciliation_passed": True,
    }
    save_json(OUT / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    print(ranked.head(12)[["n", "matches", "match_pct", "gain_vs_current_pp", "severe_under",
                          "zero_max_errors", "zero_rop_errors", "passes_pooled_error_checks"]].round(3).to_string())


if __name__ == "__main__":
    main()
