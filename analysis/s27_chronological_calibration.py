"""S27: chronological TCB sizing experiment; never writes to the live database.

Run fetch, tune, then holdout in that order. August 2026 labels are used only
after selection.json is frozen. All sensitive outputs remain in analysis/output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/scripts"))
from app import engine_statistical as E, dormant_rules, part_category  # noqa: E402
from app.db import active_config, get_conn  # noqa: E402
from app.ingestion import normalize, quarantine_mask  # noqa: E402
from import_tcb_archive import cycle_for, fingerprints  # noqa: E402
from scorecard import matched  # noqa: E402

OUT = ROOT / "analysis/output/s27_chronological"
ARCHIVE = ROOT / "BOM table/BOM Review Archive 1.csv"
HOLDOUT = "2026-08"
DEV_START = "2025-06"
LEVELS = ("max", "rop", "min")
PRIOR_COLS = ["prior_final_max", "prior_final_rop", "prior_final_min", "prior_c365"]
INPUTS = ["item_id", "stockroom_id", "module", "part_category", "contractual_lead_time",
          "order_qty_multiple", "unitprice", "max_qty", "rop_qty", "min_qty",
          "frequencymonthswithusage", "sfm_criticality", "sfm_mean_lt_cd",
          "qry_eoh_excess_qty", "ownership", "replenishment_policy"] + list(E.CONS.values())
SQL = """SELECT h.batch_id,h.review_id,h.item_id,h.stockroom_id,h.reviewed_at,
 h.final_max,h.final_rop,h.final_min,b.payload
 FROM review_history h JOIN bom_rows b USING(batch_id,item_id,stockroom_id)
 WHERE h.decision='historical' AND b.module='TCB' AND b.quarantined=0
 ORDER BY h.batch_id,h.item_id,h.stockroom_id,h.review_id"""


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding="utf-8")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def candidates():
    result = []
    anchors = [("off", 0, "", None), ("current1", 1, "", None),
               ("prior1", 1, "demand", None), ("prior2", 2, "demand", None),
               ("guard25_1", 1, "demand", .25), ("guard50_1", 1, "demand", .50),
               ("guard25_2", 2, "demand", .25)]
    for estimator in ("single", "blended"):
        for trend in (False, True):
            for name, band, policy, drift in anchors:
                identifier = f"{estimator}_{'trend' if trend else 'flat'}_{name}"
                result.append({"name": identifier, "cfg": {
                    "demand_estimator": estimator, "trend_adjust": trend,
                    "continuity_snap": band, "prior_anchor_policy": policy},
                    "prior_max_age_months": 6 if drift is not None else None,
                    "prior_max_drift": drift})
    # Development-only follow-up: the leading wider-anchor candidates lost
    # eligibility by zeroing levels that the baseline kept positive. This
    # label-free veto retains all baseline levels when that happens, or when
    # any critical-part Max/ROP would be reduced. August remains unopened.
    for parent in ("single_flat_prior2", "single_flat_guard25_2", "single_trend_prior2"):
        original = next(c for c in result if c["name"] == parent)
        result.append({**original, "name": parent + "_positive",
                       "preserve_positive_baseline": True})
    return result


BASELINE = "single_flat_prior1"


def protocol():
    return {
        "holdout": HOLDOUT, "development_start": DEV_START,
        "baseline": BASELINE, "candidate_grid": candidates(),
        "primary": "Equal-weight mean strict Max+ROP agreement across development cycles, active/dying only",
        "tolerance": "10% relative on both Max and ROP; zero requires zero; no absolute floor",
        "eligibility": "No increase versus baseline in pooled severe undersizing, zero-Max errors, or zero-ROP errors; no reduction of critical-part levels versus baseline; win at least half the cycles",
        "severe_undersizing": "Either Max or ROP below the engineer by more than max(1 unit, 25% of that approved level)",
        "tie_break": "Higher macro agreement, lower Max MAE, baseline preference, lexical candidate name",
        "walk_forward": "At each cycle choose using earlier cycles only; at least two earlier cycles required",
        "holdout_gate": "Selected method must improve live agreement, pass the same risk proxies, and have a positive paired 95% bootstrap lower bound",
        "scope": "Retrospective decision agreement under today's frozen engine/config; not achieved service levels, demand forecasts, or causal savings",
        "timestamp_rule": "Strictly earlier review cycle AND known decision availability before the prediction cycle; conflicting January inputs reconciled to archive, unresolved rows excluded",
        "bootstrap": "4000 paired resamples of unique item-stockroom pairs; seed 270826",
        "development_only_amendment": "After 28 development variants, add three positive-level/criticality veto wrappers to the leading wider-anchor methods. Earlier selection saved as selection_round1.json. No August scores inspected before this amendment.",
    }


def numeric(series):
    return pd.to_numeric(series, errors="coerce")


def resolve_source(row, payload, cycle, archive):
    """Do not manufacture an exact historical review date from a cycle name."""
    date = pd.to_datetime(row["reviewed_at"], errors="coerce")
    timestamp_conflict = pd.isna(date) or date.strftime("%Y-%m") < cycle
    if not timestamp_conflict:
        return payload, str(row["reviewed_at"])[:10], "stored"
    key = (row["item_id"], row["stockroom_id"], cycle)
    replacement = archive.get(key)
    if replacement is None:
        return None, None, "unresolved_timestamp"
    for level in LEVELS:
        value = pd.to_numeric(replacement.get("factory_recommended_new_" + level), errors="coerce")
        if pd.isna(value) or value != row["final_" + level]:
            return None, None, "conflicting_archive_decision"
    actual_date = pd.to_datetime(replacement.get("source_modified_date"), errors="coerce")
    if pd.isna(actual_date) or actual_date.strftime("%Y-%m") < cycle:
        return None, None, "unresolved_archive_timestamp"
    return replacement, actual_date.strftime("%Y-%m-%d"), "archive_reconciled"


def attach_priors(frame):
    """Publish outcomes only after the whole cycle; isolate stockrooms."""
    df = frame.sort_values(["_cycle", "item_id", "stockroom_id"]).reset_index(drop=True).copy()
    for col in PRIOR_COLS:
        df[col] = np.nan
    df["_prior_cycle"] = ""
    df["_prior_available_date"] = ""
    df["_prior_age_months"] = np.nan
    history = {}
    for cycle, group in df.groupby("_cycle", sort=True):
        cutoff = f"{cycle}-01"
        for i, row in group.iterrows():
            key = (str(row.item_id), str(row.stockroom_id))
            available = [r for r in history.get(key, []) if r["_available_date"] < cutoff]
            if not available:
                continue
            previous = available[-1]
            assert previous["_cycle"] < cycle
            for level in LEVELS:
                df.at[i, "prior_final_" + level] = previous["_final_" + level]
            df.at[i, "prior_c365"] = pd.to_numeric(previous.get(E.CONS[365]), errors="coerce")
            df.at[i, "_prior_cycle"] = previous["_cycle"]
            df.at[i, "_prior_available_date"] = previous["_available_date"]
            df.at[i, "_prior_age_months"] = pd.Period(cycle, freq="M").ordinal - pd.Period(previous["_cycle"], freq="M").ordinal
        for _, row in group.iterrows():
            history.setdefault((str(row.item_id), str(row.stockroom_id)), []).append(row.to_dict())
    return df


def fetch(output, settings_owner):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "selection.json").exists():
        raise ValueError("Selection is frozen; use a new output directory for new data")
    save_json(output / "protocol.json", protocol())
    conn = get_conn()
    try:
        if not conn.execute("SELECT 1 FROM user_settings WHERE owner_user=?", (settings_owner,)).fetchone():
            raise ValueError("Open Settings for the requested account before fetching a read-only snapshot")
        before = fingerprints(conn)
        rows = conn.execute(SQL).fetchall()
        batches = {r["batch_id"]: dict(r) for r in conn.execute("SELECT * FROM batches")}
        config = active_config(conn, settings_owner)
        config["_settings_owner"] = settings_owner
        config["dormant_rules"] = dormant_rules.load_rules(conn, settings_owner)
        rules, broken = part_category.load_rules(conn, settings_owner)
        if broken:
            raise ValueError("Invalid confirmed category patterns")
        after = fingerprints(conn)
        if before != after:
            raise ValueError("Database changed during read; retry the snapshot")
    finally:
        conn.close()
    archive = {}
    for chunk in pd.read_csv(ARCHIVE, dtype=str, keep_default_na=False, encoding="utf-8-sig", chunksize=20000):
        tcb = normalize(chunk[chunk.module.str.strip().eq("TCB")].copy())
        for p in tcb.to_dict("records"):
            key = (p["item_id"], p["stockroom_id"], p["bom_review_cylce"])
            if key in archive:
                raise ValueError("Archive identity is not unique")
            archive[key] = p
    data, quality = [], []
    for row in rows:
        p = json.loads(row["payload"])
        cycle = cycle_for(p, batches[row["batch_id"]])
        source, date, status = resolve_source(row, p, cycle, archive)
        q = {k: row[k] for k in ("batch_id", "item_id", "stockroom_id", "review_id")}
        q.update(cycle=cycle, resolution=status, original_reviewed_at=row["reviewed_at"])
        quality.append(q)
        if source is None:
            continue
        record = {c: source.get(c, "") for c in INPUTS}
        record["part_category"] = part_category.categorise(source.get("item_desc"), rules)
        record.update({"_cycle": cycle, "_available_date": date, "_batch_id": row["batch_id"],
                       "_review_id": row["review_id"], "_resolution": status})
        record.update({"_final_" + level: row["final_" + level] for level in LEVELS})
        data.append(record)
    df = pd.DataFrame(data)
    if df.duplicated(["item_id", "stockroom_id", "_cycle"]).any():
        raise ValueError("Duplicate item/stockroom/cycle outcomes")
    excluded = []
    for cycle, group in df.groupby("_cycle"):
        reasons = quarantine_mask(group)
        for i in reasons[reasons.ne("")].index:
            excluded.append({"review_id": int(df.loc[i, "_review_id"]), "cycle": cycle, "reason": reasons.loc[i]})
        df.loc[reasons[reasons.ne("")].index, "_exclude"] = True
    df = df[~df.get("_exclude", pd.Series(False, index=df.index)).fillna(False).astype(bool)].copy()
    targets = df[["_final_" + level for level in LEVELS]].apply(pd.to_numeric, errors="coerce")
    valid = targets.notna().all(axis=1) & targets.ge(0).all(axis=1)
    valid &= targets["_final_max"].ge(targets["_final_rop"]) & targets["_final_rop"].ge(targets["_final_min"])
    invalid_labels = int((~valid).sum())
    df = attach_priors(df[valid].copy())
    # Today's factory answers, comments, dates and adoption columns cannot reach E.run.
    assert not any(c.startswith("factory_recommended") for c in df.columns)
    df.to_pickle(output / "panel.pkl")
    pd.DataFrame(quality).to_csv(output / "timestamp_resolution.csv", index=False)
    pd.DataFrame(excluded).to_csv(output / "quarantine_exclusions.csv", index=False)
    save_json(output / "config.json", config)
    (output / "source_query.sql").write_text(SQL, encoding="utf-8")
    meta = {"database_before": before, "source_rows": len(rows), "eligible_rows": len(df),
            "timestamp_resolution": dict(Counter(q["resolution"] for q in quality)),
            "quarantine_after_reconciliation": len(excluded), "invalid_labels": invalid_labels,
            "cycles": df.groupby("_cycle").size().to_dict(), "archive_sha256": sha(ARCHIVE),
            "panel_sha256": sha(output / "panel.pkl"), "engine_sha256": sha(ROOT / "backend/app/engine_statistical.py"),
            "engine_version": E.MODEL_VERSION, "source": str(ARCHIVE.relative_to(ROOT)),
            "created_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "dependency_versions": {"numpy": np.__version__, "pandas": pd.__version__}}
    save_json(output / "source_manifest.json", meta)
    print(json.dumps({k: meta[k] for k in ("source_rows", "eligible_rows", "timestamp_resolution", "quarantine_after_reconciliation", "invalid_labels", "cycles")}), flush=True)


def engine_input(frame, candidate):
    df = frame.reindex(columns=INPUTS + PRIOR_COLS).copy()
    drift = candidate.get("prior_max_drift")
    if drift is not None:
        now, then = numeric(df[E.CONS[365]]), numeric(df.prior_c365)
        allowed = now.notna() & then.notna() & (now - then).abs().le(drift * then.abs().clip(lower=1))
        allowed &= numeric(frame._prior_age_months).le(candidate["prior_max_age_months"])
        df.loc[~allowed, PRIOR_COLS] = np.nan
    return df


def predictions(frame, config, candidate):
    result = E.run(engine_input(frame, candidate), {**config, **candidate["cfg"]})
    values = result[["factory_recommended_new_" + l for l in LEVELS]].to_numpy(float)
    if candidate.get("preserve_positive_baseline"):
        base_spec = next(c for c in candidates() if c["name"] == BASELINE)
        base, _ = predictions(frame, config, base_spec)
        critical = frame.sfm_criticality.fillna("").astype(str).str.strip().str.lower().str.startswith("h").to_numpy()
        veto = ((values[:, :2] == 0) & (base[:, :2] > 0)).any(axis=1)
        veto |= critical & (values[:, :2] < base[:, :2]).any(axis=1)
        values = values.copy()
        values[veto] = base[veto]
    assert np.isfinite(values).all() and (values >= 0).all()
    assert (values[:, 0] >= values[:, 1]).all() and (values[:, 1] >= values[:, 2]).all()
    return values, result.route.to_numpy()


def benchmark_predictions(frame, fallback):
    cur = frame[["max_qty", "rop_qty", "min_qty"]].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    prior = frame[["prior_final_max", "prior_final_rop", "prior_final_min"]].to_numpy(float)
    def valid(x):
        return np.isfinite(x).all(axis=1) & (x >= 0).all(axis=1) & (x[:, 0] >= x[:, 1]) & (x[:, 1] >= x[:, 2])
    current = np.where(valid(cur)[:, None], cur, fallback)
    previous = np.where(valid(prior)[:, None], prior, current)
    return {"hold_current": current, "last_approved": previous}


def metric(frame, values, base_values):
    target = frame[["_final_" + l for l in LEVELS]].to_numpy(float)
    hit = matched(values[:, :2], target[:, :2])
    delta = target[:, :2] - values[:, :2]
    severe = (delta > np.maximum(1, .25 * target[:, :2])).any(axis=1)
    price = numeric(frame.unitprice).to_numpy(float)
    price_ok = np.isfinite(price) & (price > 0)
    critical = frame.sfm_criticality.fillna("").astype(str).str.strip().str.lower().str.startswith("h").to_numpy()
    return {"n": len(frame), "matches": int(hit.sum()), "match_pct": float(hit.mean() * 100),
            "max_mae": float(np.abs(values[:, 0] - target[:, 0]).mean()),
            "rop_mae": float(np.abs(values[:, 1] - target[:, 1]).mean()),
            "severe_under": int(severe.sum()),
            "zero_max_errors": int(((values[:, 0] == 0) & (target[:, 0] > 0)).sum()),
            "zero_rop_errors": int(((values[:, 1] == 0) & (target[:, 1] > 0)).sum()),
            "critical_reductions_vs_baseline": int((critical & (values[:, :2] < base_values[:, :2]).any(axis=1)).sum()),
            "critical_n": int(critical.sum()), "priced_n": int(price_ok.sum()),
            "proposed_max_value_usd": float((price[price_ok] * values[price_ok, 0]).sum()),
            "approved_max_value_usd": float((price[price_ok] * target[price_ok, 0]).sum())}


def score_cycles(frame, values, baseline, routes, name):
    records = []
    for cycle in sorted(frame._cycle.unique()):
        for segment in ("all", "live", "active", "dying", "dormant"):
            mask = frame._cycle.eq(cycle).to_numpy(copy=True)
            if segment == "live":
                mask &= np.isin(routes, ("active", "dying"))
            elif segment != "all":
                mask &= routes == segment
            if mask.any():
                records.append({"candidate": name, "cycle": cycle, "segment": segment,
                                **metric(frame[mask], values[mask], baseline[mask])})
    return records


def ranking(scores, cycles):
    live = scores[scores.segment.eq("live") & scores.cycle.isin(cycles)]
    base = live[live.candidate.eq(BASELINE)].set_index("cycle")
    rows = []
    eligible_names = {c["name"] for c in candidates()}
    for name, group in live.groupby("candidate"):
        aligned = group.set_index("cycle").reindex(base.index)
        if aligned.n.isna().any():
            continue
        wins = int(aligned.match_pct.gt(base.match_pct).sum())
        safe = all(aligned[c].sum() <= base[c].sum() for c in ("severe_under", "zero_max_errors", "zero_rop_errors"))
        safe &= aligned.critical_reductions_vs_baseline.sum() == 0
        ready = safe and wins >= (len(base) + 1) // 2
        if name == BASELINE:
            ready = True
        rows.append({"candidate": name, "macro_match_pct": float(aligned.match_pct.mean()),
                     "pooled_match_pct": float(aligned.matches.sum() / aligned.n.sum() * 100),
                     "max_mae": float((aligned.max_mae * aligned.n).sum() / aligned.n.sum()),
                     "wins": wins, "cycles": len(base), "severe_under": int(aligned.severe_under.sum()),
                     "zero_max_errors": int(aligned.zero_max_errors.sum()), "zero_rop_errors": int(aligned.zero_rop_errors.sum()),
                     "eligible": bool(ready and name in eligible_names), "baseline_priority": 0 if name == BASELINE else 1})
    return pd.DataFrame(rows).sort_values(["macro_match_pct", "max_mae", "baseline_priority", "candidate"], ascending=[False, True, True, True])


def choose(scores, cycles):
    rank = ranking(scores, cycles)
    return rank[rank.eligible].iloc[0].candidate


def load_inputs(output):
    manifest = json.loads((output / "source_manifest.json").read_text())
    assert sha(output / "panel.pkl") == manifest["panel_sha256"]
    assert sha(ROOT / "backend/app/engine_statistical.py") == manifest["engine_sha256"]
    return pd.read_pickle(output / "panel.pkl"), json.loads((output / "config.json").read_text())


def tune(output):
    if (output / "holdout_summary.json").exists():
        raise ValueError("Holdout already opened; this experiment cannot be retuned")
    df, config = load_inputs(output)
    df = df[df._cycle.lt(HOLDOUT)].reset_index(drop=True)
    specs = {c["name"]: c for c in candidates()}
    baseline, routes = predictions(df, config, specs[BASELINE])
    live = np.isin(routes, ("active", "dying"))
    records = score_cycles(df, baseline, baseline, routes, BASELINE)
    for name, cand in specs.items():
        if name == BASELINE:
            continue
        values = baseline.copy()
        # Candidate levers only affect active/dying. Dormant/no-data remain the
        # exact baseline, without rescoring those identical rows 28 times.
        values[live], candidate_routes = predictions(df[live].reset_index(drop=True), config, cand)
        assert np.array_equal(candidate_routes, routes[live])
        records.extend(score_cycles(df, values, baseline, routes, name))
        print(f"Scored development: {name}", flush=True)
    for name, values in benchmark_predictions(df, baseline).items():
        records.extend(score_cycles(df, values, baseline, routes, name))
    scores = pd.DataFrame(records)
    scores.to_csv(output / "development_scores.csv", index=False)
    dev = sorted(c for c in df._cycle.unique() if c >= DEV_START)
    ranks = ranking(scores, dev)
    ranks.to_csv(output / "development_ranking.csv", index=False)
    selected = choose(scores, dev)
    walk = []
    all_cycles = sorted(df._cycle.unique())
    for cycle in dev:
        earlier = [c for c in all_cycles if c < cycle]
        winner = choose(scores, earlier) if len(earlier) >= 2 else BASELINE
        current = scores[scores.cycle.eq(cycle) & scores.segment.eq("live")]
        win = current[current.candidate.eq(winner)].iloc[0]
        base = current[current.candidate.eq(BASELINE)].iloc[0]
        walk.append({"cycle": cycle, "chosen_using_earlier_cycles": winner, "n": int(win.n),
                     "match_pct": float(win.match_pct), "baseline_match_pct": float(base.match_pct),
                     "gain_pp": float(win.match_pct - base.match_pct)})
    pd.DataFrame(walk).to_csv(output / "walk_forward.csv", index=False)
    selection = {"selected": specs[selected], "baseline": specs[BASELINE], "development_cycles": dev,
                 "selected_results": ranks[ranks.candidate.eq(selected)].iloc[0].to_dict(),
                 "protocol_sha256": sha(output / "protocol.json"), "panel_sha256": sha(output / "panel.pkl"),
                 "config_sha256": sha(output / "config.json"), "code_sha256": sha(Path(__file__)),
                 "frozen_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                 "holdout_used_for_selection": False}
    save_json(output / "selection.json", selection)
    print(json.dumps(selection, indent=2), flush=True)


def paired_interval(frame, a, b):
    assert not frame.duplicated(["item_id", "stockroom_id"]).any()
    target = frame[["_final_max", "_final_rop"]].to_numpy(float)
    differences = matched(a[:, :2], target).astype(float) - matched(b[:, :2], target).astype(float)
    rng = np.random.default_rng(270826)
    draws = rng.choice(differences, size=(4000, len(differences)), replace=True).mean(axis=1) * 100
    return [float(x) for x in np.percentile(draws, [2.5, 97.5])]


def holdout(output):
    selection = json.loads((output / "selection.json").read_text())
    assert sha(Path(__file__)) == selection["code_sha256"], "Experiment code changed after selection"
    assert sha(output / "protocol.json") == selection["protocol_sha256"]
    assert sha(output / "config.json") == selection["config_sha256"]
    df, config = load_inputs(output)
    df = df[df._cycle.eq(HOLDOUT)].reset_index(drop=True)
    baseline, routes = predictions(df, config, selection["baseline"])
    selected, selected_routes = predictions(df, config, selection["selected"])
    assert np.array_equal(routes, selected_routes)
    comparisons = {BASELINE: baseline, "selected": selected, **benchmark_predictions(df, baseline)}
    rows, details = [], []
    for name, values in comparisons.items():
        rows.extend(score_cycles(df, values, baseline, routes, name))
        part = df[["item_id", "stockroom_id", "_cycle", "_final_max", "_final_rop", "_final_min", "_resolution", "_prior_cycle"]].copy()
        part["candidate"], part["route"] = name, routes
        for pos, level in enumerate(LEVELS):
            part["predicted_" + level] = values[:, pos]
        details.append(part)
    scores = pd.DataFrame(rows)
    scores.to_csv(output / "holdout_scores.csv", index=False)
    pd.concat(details, ignore_index=True).to_csv(output / "holdout_predictions.csv", index=False)
    live = np.isin(routes, ("active", "dying"))
    ci = paired_interval(df[live], selected[live], baseline[live])
    pair = scores[scores.segment.eq("live")].set_index("candidate")
    a, b = pair.loc["selected"], pair.loc[BASELINE]
    safe = all(a[c] <= b[c] for c in ("severe_under", "zero_max_errors", "zero_rop_errors")) and a.critical_reductions_vs_baseline == 0
    summary = {"holdout": HOLDOUT, "selected_candidate": selection["selected"]["name"],
               "n_all": len(df), "n_live": int(live.sum()), "baseline_live_match_pct": float(b.match_pct),
               "selected_live_match_pct": float(a.match_pct), "gain_pp": float(a.match_pct - b.match_pct),
               "paired_gain_95pct_interval_pp": ci, "risk_proxy_gate_passed": bool(safe),
               "promotion_supported": bool(a.match_pct > b.match_pct and ci[0] > 0 and safe),
               "production_changed": False, "selection_sha256": sha(output / "selection.json"),
               "holdout_labels_opened_at_utc": pd.Timestamp.now(tz="UTC").isoformat()}
    save_json(output / "holdout_summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("fetch", "tune", "holdout"))
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--settings-owner", help="Account whose settings to snapshot (required for fetch)")
    args = parser.parse_args()
    if args.stage == "fetch":
        if not args.settings_owner:
            parser.error("fetch requires --settings-owner")
        fetch(args.output, args.settings_owner)
    else:
        {"tune": tune, "holdout": holdout}[args.stage](args.output)


if __name__ == "__main__":
    main()
