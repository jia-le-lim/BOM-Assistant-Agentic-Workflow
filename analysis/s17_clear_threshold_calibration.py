"""S17 -- Clear-threshold calibration: grade the prior_review and analogue rungs.

s15 grades the engine's own auto-clear rungs (noop / immaterial / reliable) on a
month whose factory_recommended_new_* columns are PRESENT. Those rungs never read
that column, so the measurement is honest. The two rungs measured here do read it,
directly or through review_history, so the same protocol would be circular:

    agreement       is computed against factory_recommended_new_*  (engine_statistical:205)
    review_history  final_* IS factory_recommended_new_*           (backfill_history:115)

So the held-out month is ingested with those three columns STRIPPED. The engine then
falls through _benchmark's ladder to the prior_review rung, similarity retrieves
peers that never saw this month, and the withheld numbers are the label.

    coverage   = share of held-out rows a rung would clear
    precision  = of cleared LABELLED rows, share where the engine value matches the
                 engineer's (|delta| <= 1 or <= 10% on all three levels)
    escaped_$  = exposure of cleared rows where engine != engineer

Rungs are MEASURED here, not wired: nothing in this script changes review_required
in production, and no threshold is written back to rule_config.

Outputs (analysis/output/):
    s17_clear_threshold.csv      one row per config: coverage / precision / escaped
    s17_confidence_ranking.csv   confidence-decile precision over the UNCLEARED rows

Run: python s17_clear_threshold_calibration.py   (--include-large adds Dec_24 +
March_2025, ~350MB each; --selftest checks the grading maths and exits)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from common import LEVELS, OUT, TARGET_PRECISION, agree

ROOT = Path(__file__).resolve().parent.parent
WORKBOOKS = ROOT / "BOM table"

# Ordered oldest -> newest, and it MUST stay that way. Both
# engine_adapter._attach_prior_benchmark and similarity._load_pool resolve "the
# latest decision" by review_id, i.e. by insertion order -- so loading a month out
# of sequence silently changes which precedent the prior_review rung is graded
# against. The two ~350MB workbooks are opt-in (--include-large) but sit in their
# real chronological slots, not appended at the end.
HISTORY = [
    ("AE_JULY'24 - BOM REVIEW - Factory Cost Rep Review_.xlsx", False),
    ("AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx", False),
    ("Sept_24 BOM REVIEW.xlsx", False),
    ("Oct_24 BOM REVIEW.xlsx", False),
    ("Dec_24 BOM REVIEW.xlsx", True),
    ("March_2025_BOM REVIEW_UN94X4_8ae0dbe9591.xlsx", True),
    ("May'2025_BOM REVIEW.xlsx", False),
]
HELD_OUT = "BOM REVIEW_Jan'26 .csv"       # newest -> held out, loaded last


def history_files(include_large: bool) -> list[str]:
    return [name for name, large in HISTORY if include_large or not large]

MODULE = "TCB"
MATCH_MODE = "exact"

# PRD 5.1 engine-OUTPUT columns. Stripped from the held-out upload so the label
# cannot reach the engine, similarity, or review_history by any route.
STRIP_COLS = ("factory_recommended_new_max", "factory_recommended_new_rop",
              "factory_recommended_new_min")

ANALOGUE_CONCUR = "ANALOGUE_CONCUR"
MIN_CLEARED_FOR_WINNER = 30     # a precision over fewer rows than this means nothing
CONF_STEPS = (0.5, 0.6, 0.7, 0.8, 0.9)


# ---------------------------------------------------------------------------
# held-out protocol
# ---------------------------------------------------------------------------

def strip_labels(path: Path) -> tuple[bytes, pd.DataFrame]:
    """Held-out upload with the engineer's answer removed, plus that answer.

    ingestion.REQUIRED_COLS does not include the factory columns, and
    engine_statistical._num returns an all-NaN Series for a missing column, so
    _benchmark falls straight through to the prior_review rung. Nothing raises.
    """
    from app.ingestion import _read_table, normalize

    df = normalize(_read_table(path.read_bytes(), path.name))
    missing = [c for c in STRIP_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"{path.name}: no labels to withhold ({missing})")

    key = ["item_id", "stockroom_id"] if "stockroom_id" in df.columns else ["item_id"]
    labels = df[key + list(STRIP_COLS)].copy()
    for c in key:
        labels[c] = labels[c].astype(str).str.strip()
    # Duplicate keys are quarantined by ingestion and so never reach
    # recommendation_result, but a duplicated LABEL row would fan the merge out.
    labels = labels.drop_duplicates(subset=key)

    stripped = df.drop(columns=list(STRIP_COLS))
    return stripped.to_csv(index=False).encode("utf-8-sig"), labels


def build_pool(conn, include_large: bool) -> tuple[list[dict], list[str]]:
    """Ingest -> score -> synthesise reviews for every history month, in order.

    A workbook open in Excel is locked on Windows, and these live in a shared
    OneDrive folder. Skip it loudly rather than losing the whole run: the caller
    prints the skips beside the results, because a missing month changes the peer
    pool and therefore every number below it.
    """
    from app.engine_adapter import score_batch
    from app.ingestion import ingest

    sys.path.insert(0, str(ROOT / "backend" / "scripts"))
    from backfill_history import synthesise_reviews

    loaded, skipped = [], []
    for name in history_files(include_large):
        path = WORKBOOKS / name
        try:
            content = path.read_bytes()
        except OSError as e:
            skipped.append(name)
            print(f"  SKIP     {path.name[:44]:46s} unreadable ({e.strerror}) "
                  "-- close it in Excel and re-run")
            continue
        summary = ingest(conn, content, f"s17-{path.stem[:40]}",
                         path.name, MODULE, "s17-calibration",
                         match_mode=MATCH_MODE)
        score = score_batch(conn, summary["batch_id"])
        reviews = synthesise_reviews(conn, summary["batch_id"], score["rule_version"])
        loaded.append({"file": path.name, "rows": summary["rows_loaded"],
                       "scored": score["rows_scored"], **reviews})
        print(f"  history  {path.name[:44]:46s} rows={summary['rows_loaded']:6,} "
              f"reviews={reviews['reviews']:6,}")
    if not loaded:
        raise SystemExit("no history workbook could be read -- the peer pool is empty")
    return loaded, skipped


def held_out(conn) -> tuple[pd.DataFrame, int]:
    """Score the stripped month, retrieve peers, return the graded eval frame."""
    from app import similarity as _similarity
    from app.engine_adapter import score_batch
    from app.ingestion import ingest
    from app.similarity import run_similarity

    # ANALOGUE_CONCUR is restated as a literal above because app.* cannot be
    # imported before main() pins the environment. Assert the two agree: a rename
    # in similarity.py would otherwise make every analogue rung report cleared_n=0
    # with precision NaN, and the run would exit 0 as a valid negative result.
    if _similarity.ANALOGUE_CONCUR != ANALOGUE_CONCUR:
        raise SystemExit(f"advisory code drift: similarity.py says "
                         f"{_similarity.ANALOGUE_CONCUR!r}, this harness expects "
                         f"{ANALOGUE_CONCUR!r}")

    content, labels = strip_labels(WORKBOOKS / HELD_OUT)
    held = ingest(conn, content, "s17-heldout", HELD_OUT, MODULE,
                  "s17-calibration", match_mode=MATCH_MODE)
    bid = held["batch_id"]
    score_batch(conn, bid)
    sim = run_similarity(conn, bid)
    conn.commit()
    print(f"  held-out {HELD_OUT[:44]:46s} rows={held['rows_loaded']:6,} "
          f"batch={bid} no_analogue={sim['no_analogue']:,} "
          f"pool={sim['neighbour_pool']:,}")

    # The held-out month must never be its own peer. It is not -- _load_pool reads
    # review_history and none was synthesised for it -- but assert rather than trust.
    leaked = conn.execute("SELECT COUNT(*) AS c FROM review_history WHERE batch_id=?",
                          (bid,)).fetchone()["c"]
    if leaked:
        raise SystemExit(f"held-out batch {bid} leaked {leaked} rows into the peer pool")

    fired = conn.execute(
        "SELECT COUNT(*) AS c FROM recommendation_result "
        "WHERE batch_id=? AND agreement_source='prior_review'", (bid,)).fetchone()["c"]
    if not fired:
        raise SystemExit(
            f"batch {bid}: the prior_review rung never fired. Every precision below "
            "would be meaningless -- debug engine_adapter._attach_prior_benchmark first.")
    print(f"  prior_review benchmark fired on {fired:,} rows")

    return eval_frame(conn, bid, labels), bid


_EVAL_SQL = (
    "SELECT r.item_id, r.stockroom_id, r.new_max, r.new_rop, r.new_min, "
    "r.review_required, r.risk_level, r.route, r.agreement, r.agreement_source, "
    "r.exposure_usd, r.confidence AS engine_confidence, "
    "s.advisory_codes, s.confidence AS similarity_confidence, s.neighbour_count "
    "FROM recommendation_result r "
    "LEFT JOIN similarity_result s ON s.batch_id=r.batch_id "
    "AND s.item_id=r.item_id AND s.stockroom_id=r.stockroom_id "
    "WHERE r.batch_id=?")


def eval_frame(conn, bid: int, labels: pd.DataFrame) -> pd.DataFrame:
    """One row per held-out item: engine output + peer evidence + withheld label."""
    df = pd.DataFrame([dict(r) for r in conn.execute(_EVAL_SQL, (bid,))])
    key = [c for c in ("item_id", "stockroom_id") if c in labels.columns]
    for c in key:
        df[c] = df[c].astype(str).str.strip()
    df = df.merge(labels, on=key, how="left")

    for lvl in LEVELS:
        df[f"fac_{lvl}"] = pd.to_numeric(df[f"factory_recommended_new_{lvl}"],
                                         errors="coerce")
    df["labelled"] = np.all([df[f"fac_{lvl}"].notna().to_numpy() for lvl in LEVELS],
                            axis=0)
    df["correct"] = np.all(
        [agree(df[f"new_{lvl}"].to_numpy(float), df[f"fac_{lvl}"].to_numpy(float))
         for lvl in LEVELS], axis=0)
    df["codes"] = df["advisory_codes"].fillna("")
    return df


# ---------------------------------------------------------------------------
# rungs and grading
# ---------------------------------------------------------------------------

def _eligible(df: pd.DataFrame) -> np.ndarray:
    """The engine's own exclusions, as far as recommendation_result can express them.

    engine_statistical:426 also excludes critical parts and high-exposure changes.
    sfm_criticality is not on recommendation_result, so risk_level != 'High' stands
    in for it -- critical parts score High. Stated as a limitation in the results
    log, never presented as an exact reproduction.
    """
    return ((df["risk_level"] != "High").to_numpy()
            & (df["route"] == "active").to_numpy())


def _has(df: pd.DataFrame, code: str) -> np.ndarray:
    """Exact membership in the comma-joined advisory_codes (similarity.py:596).

    Not str.contains: no code is a substring of another today, but one could be.
    """
    return df["codes"].str.split(",").apply(lambda cs: code in cs).to_numpy()


def rungs(df: pd.DataFrame) -> dict:
    prior = ((df["agreement"] == "match")
             & (df["agreement_source"] == "prior_review")).to_numpy()
    concur = _has(df, ANALOGUE_CONCUR)
    conf = pd.to_numeric(df["similarity_confidence"],
                         errors="coerce").fillna(0.0).to_numpy()

    grid = {
        "baseline_engine": (df["review_required"] == "N").to_numpy(),
        "prior_review_match": prior,
        "analogue_concur": concur,
        "prior_or_analogue": prior | concur,
        "prior_and_analogue": prior & concur,
    }
    for t in CONF_STEPS:
        grid[f"prior+conf>={t}"] = prior & (conf >= t)
        grid[f"analogue+conf>={t}"] = concur & (conf >= t)

    # baseline is the engine's own verdict; it already applied its own exclusions.
    out = {k: (v if k == "baseline_engine" else v & _eligible(df))
           for k, v in grid.items()}

    # Route-gate-free variants. The engine only auto-clears route=='active'
    # (engine_statistical:426), which on a mostly-dormant BOM caps EVERY rung at a
    # couple of percent. Without these two rows the card cannot distinguish "the
    # rung is weak" from "the route gate is binding" -- a different conclusion and a
    # different fix. Still exclude High risk; still measurement only.
    keep = (df["risk_level"] != "High").to_numpy()
    out["prior_review_match|no_route_gate"] = prior & keep
    out["analogue_concur|no_route_gate"] = concur & keep
    return out


def evaluate(df: pd.DataFrame, cleared: np.ndarray) -> dict:
    labelled = df["labelled"].to_numpy()
    correct = df["correct"].to_numpy()
    exposure = pd.to_numeric(df["exposure_usd"], errors="coerce").fillna(0.0).to_numpy()

    cl_lab = cleared & labelled
    n = int(cl_lab.sum())
    return {
        "cleared_%": round(100.0 * cleared.mean(), 1),
        "cleared_n": int(cleared.sum()),
        "precision_%": (round(100.0 * float((cl_lab & correct).sum()) / n, 1)
                        if n else np.nan),
        "n_labelled_cleared": n,
        "escaped_usd": round(float(exposure[cl_lab & ~correct].sum()), 0),
    }


def ranking(df: pd.DataFrame, cleared: np.ndarray) -> pd.DataFrame:
    """Precision by confidence decile over the rows the winner did NOT clear.

    A rung that clears nothing can still be worth shipping: if precision rises with
    confidence, the queue can be ORDERED even where it cannot be cut.
    """
    rest = df.loc[~cleared & df["labelled"].to_numpy()].copy()
    if rest.empty:
        return pd.DataFrame(columns=["decile", "n", "precision_pct",
                                     "min_confidence", "max_confidence"])
    conf = pd.to_numeric(rest["similarity_confidence"], errors="coerce").fillna(0.0)
    rest["similarity_confidence"] = conf
    # Confidence is 0.5*coverage + 0.5*closeness and takes few distinct values, so
    # raw qcut would raise on duplicate bin edges. Rank first, drop empty bins.
    rest["decile"] = pd.qcut(conf.rank(method="first"), 10,
                             labels=False, duplicates="drop")
    return rest.groupby("decile").agg(
        n=("correct", "size"),
        precision_pct=("correct", lambda s: round(100.0 * s.mean(), 1)),
        min_confidence=("similarity_confidence", "min"),
        max_confidence=("similarity_confidence", "max"),
    ).reset_index()


# ---------------------------------------------------------------------------
# self-check
# ---------------------------------------------------------------------------

def selftest() -> None:
    """Smallest thing that fails if the grading maths breaks. No DB, no fixtures."""
    assert agree(np.array([100.0]), np.array([105.0]))[0]        # inside 10%
    assert not agree(np.array([100.0]), np.array([200.0]))[0]
    assert agree(np.array([1.0]), np.array([2.0]))[0]            # abs floor wins

    df = pd.DataFrame({
        "codes": [ANALOGUE_CONCUR, "ANALOGUE_DIVERGENCE", ""],
        "labelled": [True, True, False],
        "correct": [True, False, True],
        "exposure_usd": [10.0, 250.0, 999.0],
    })
    assert list(_has(df, ANALOGUE_CONCUR)) == [True, False, False]

    r = evaluate(df, np.array([True, True, True]))
    assert r["cleared_n"] == 3 and r["n_labelled_cleared"] == 2
    assert r["precision_%"] == 50.0          # 1 correct of 2 labelled+cleared
    assert r["escaped_usd"] == 250.0         # unlabelled row contributes nothing
    assert np.isnan(evaluate(df, np.zeros(3, bool))["precision_%"])

    # eligibility keeps High risk and non-active routes out of every rung
    elig = pd.DataFrame({"risk_level": ["Low", "High", "Low"],
                         "route": ["active", "active", "dormant"]})
    assert list(_eligible(elig)) == [True, False, False]

    print("selftest ok")


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def main() -> None:
    if "--selftest" in sys.argv:
        return selftest()

    include_large = "--include-large" in sys.argv
    wanted = history_files(include_large) + [HELD_OUT]
    absent = [n for n in wanted if not (WORKBOOKS / n).exists()]
    if absent:
        raise SystemExit(f"missing workbooks in {WORKBOOKS}: {absent}")

    with tempfile.TemporaryDirectory() as tmp:
        # Before ANY app.* import: app.config._load_dotenv() runs at import time and
        # would otherwise point this harness at the real Supabase project.
        #
        # Clearing DATABASE_URL is NOT enough. config.use_rest() is a second route
        # to the same project and switches on SUPABASE_URL + a key alone, with
        # is_postgres() then True and BOM_DB_PATH ignored -- this harness would
        # write six months of synthetic batches and review_history straight into
        # production. backend/tests/conftest.py clears exactly these three for the
        # same reason; that guard has to be carried here too.
        os.environ.update({
            "BOM_ALLOW_SQLITE": "1",
            "BOM_DB_PATH": str(Path(tmp) / "s17.db"),
            "DATABASE_URL": "",
            "LLM_BASE_URL": "",
            "BOM_ENGINE": "statistical",
            "SUPABASE_URL": "",
            "SUPABASE_SERVICE_ROLE_KEY": "",
            "SUPABASE_SECRET_KEY": "",
        })
        sys.path.insert(0, str(ROOT / "backend"))
        from app.db import active_config, get_conn, init_db

        init_db()
        conn = get_conn()
        try:
            bar = 100.0 * float(active_config(conn, "s17-calibration").get("triage_clear_precision_bar",
                                                        TARGET_PRECISION / 100.0))
            print(f"building peer pool (precision bar {bar}%)\n")
            _, skipped = build_pool(conn, include_large)
            df, _ = held_out(conn)

            grid = rungs(df)
            card = pd.DataFrame([{"config": k, **evaluate(df, v)}
                                 for k, v in grid.items()])
            base = card.loc[card["config"] == "baseline_engine", "cleared_n"].iloc[0]
            card["review_saved_vs_base"] = card["cleared_n"] - base
            card.to_csv(OUT / "s17_clear_threshold.csv", index=False,
                        encoding="utf-8-sig")
            print(f"\n{len(df):,} held-out rows, "
                  f"{int(df['labelled'].sum()):,} labelled")
            # The eligibility census explains any low coverage below: a rung cannot
            # clear more rows than the engine's own exclusions leave available.
            print(f"  eligible (active route, not High): {int(_eligible(df).sum()):,}"
                  f"   route: {df['route'].value_counts().to_dict()}\n")
            print(card.to_string(index=False))
            if skipped:
                print(f"\nWARNING: {len(skipped)} history workbook(s) skipped -- the "
                      f"peer pool is smaller than intended: {skipped}")

            ok = card[(card["precision_%"] >= bar)
                      & (card["n_labelled_cleared"] >= MIN_CLEARED_FOR_WINNER)
                      & (card["config"] != "baseline_engine")]
            print(f"\ntarget precision >= {bar}%  "
                  f"(min {MIN_CLEARED_FOR_WINNER} labelled cleared rows)")
            if len(ok):
                best = ok.sort_values("cleared_n", ascending=False).iloc[0]
                print(f"recommended: {best['config']}  -> {best['cleared_%']}% cleared "
                      f"({int(best['review_saved_vs_base']):+,} vs baseline), "
                      f"precision {best['precision_%']}%, "
                      f"${int(best['escaped_usd']):,} at risk")
                winner = grid[best["config"]]
            else:
                # Not a failure: "nothing clears the bar" is a valid result, and
                # unlike s16 this is an experiment, not a regression gate. Exit 0.
                print("no rung clears the bar -- rank only, do not auto-clear.")
                winner = np.zeros(len(df), bool)

            rank = ranking(df, winner)
            rank.to_csv(OUT / "s17_confidence_ranking.csv", index=False,
                        encoding="utf-8-sig")
            print("\nconfidence decile precision (rows NOT cleared):")
            print(rank.to_string(index=False))
        finally:
            conn.close()

    print(f"\nwrote: {(OUT / 's17_clear_threshold.csv').relative_to(ROOT)}")
    print(f"wrote: {(OUT / 's17_confidence_ranking.csv').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
