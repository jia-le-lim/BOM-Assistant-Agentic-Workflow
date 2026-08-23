# Plan: Clear-Threshold Calibration (prior_review + analogue rungs)

## Summary

Build a DB-backed calibration harness that measures precision-vs-coverage for two
evidence rungs the engine already produces but has never been graded on: the
`prior_review` benchmark (same part's own last engineer decision) and
`ANALOGUE_CONCUR` (KNN peer concurrence). It runs on a **factory-stripped**
held-out month so the grading label is genuinely withheld, picks the
highest-coverage config that clears the configured precision bar, and emits a
per-row confidence ranking for everything below that bar.

**This plan measures. It does not change production auto-clear.** No rung is
wired into `engine_statistical.run()` until the numbers exist.

## User Story

As an inventory engineer reviewing a fresh monthly BOM,
I want the system to prove which evidence rungs are precise enough to skip human review,
So that review volume drops from ~98.5% of rows to a defensible number without
un-reviewed wrong values escaping into WINGS.

## Problem → Solution

**Current:** 6184 of 6279 rows carry `review_required='Y'` (98.5%). The only
calibrated auto-clear rungs are `noop` / `immaterial` / `reliable`, and
`analysis/output/s15_autoclear_calibration.csv` shows every widening of those
*degrades* precision (70.8% → 53.9%) while escaped value rises ($29.0k → $41.6k).
The `prior_review` rung (commit `af9b750`, 2026-08-20) and `ANALOGUE_CONCUR`
(`similarity.py:81`) are strictly better-informed signals and have never been
measured on real data.

**Desired:** A reproducible harness that reports coverage, precision, and escaped
USD per rung on a held-out month, names the winning threshold, and ranks the
remainder by confidence.

## Metadata

- **Complexity**: Medium
- **Source PRD**: N/A (free-form, derived from session analysis 2026-08-20/21)
- **PRD Phase**: N/A — standalone calibration
- **Estimated Files**: 4 (1 create, 3 update)

---

## Context Discovered (read this before implementing)

### Correction to an earlier belief

`recommendation_result.agreement_source` is blank on every Supabase row. This is
**not a bug** — do not "fix" `engine_adapter.py`. The column mapping at
`engine_adapter.py:117-131` is correct. Commit `af9b750` (2026-08-20 16:54) added
`agreement_source`; batches 8–13 were scored 2026-08-19, one day earlier. The
blanks are stale data, cured by re-scoring. The harness ingests and scores fresh,
so it is unaffected.

Corollary that *does* matter: the `prior_review` rung is one day old and has
never run against a real month. Unit coverage exists
(`test_statistical_engine.py:150-237`) but this harness is its first real
exercise. Treat surprising output as a possible rung bug, not only as a data
finding.

### Why a new script instead of extending s15

`s15_autoclear_calibration.py` calls `engine_statistical.run(items, cfg)`
directly on a pandas frame (`s15:47`). That path cannot produce either new rung:

| Rung | Needs | Available in s15's pure-pandas path? |
|---|---|---|
| `prior_review` | `prior_final_max/rop/min` + `prior_c365` columns, joined from `review_history` by `engine_adapter._attach_prior_benchmark:51` | No — columns absent, `_num` returns all-NaN (`engine_statistical.py:79-82`), rung silently never fires |
| `ANALOGUE_CONCUR` | `similarity.run_similarity(conn, batch_id)` reading `review_history` ⋈ `bom_rows` ⋈ `recommendation_result` | No — requires a DB |

Adding a temp SQLite DB + ingestion + backfill + similarity to s15 turns it into
`s16_triage_backtest.py`'s shape and drags its still-valid pure-pandas Jan'26 grid
along. So: keep s15, add s17 mirroring s16's harness. Shared grading logic moves
to `common.py` so both import one definition.

### Data facts established (Supabase `qiwdjbsrtxeorzpcqmxy`)

| Fact | Value | Why it matters |
|---|---|---|
| Backfilled peer rows | 6279 | Pool size for KNN |
| Rows with `review_required='Y'` | 6184 (98.5%) | The prize |
| Jan'26 rows | 2768 | Held-out month |
| Jan'26 rows with a prior decision on the same part | **2711 (97.9%)** | `prior_review` rung addressable set |
| Jan'26 rows with no prior decision | **57 (2.1%)** | `ANALOGUE_CONCUR`-only set — wide error bars, say so in the report |
| Peers with `last_365_day_cnsmptn_qty` = 0 | 5876 (93.6%) | Do **not** add consumption as a KNN feature; it is constant across 94% of the pool |
| `similarity_result` coverage today | batches 7, 9 only | Jan'26 has no similarity output yet |
| Months backfilled | Jul'24, Aug'24, Sept'24, Oct'24, May'25, Jan'26 | Dec_24 + March_2025 (~350MB each) never loaded |
| Factory-label coverage | Sept'24/Oct'24/May'25/Jan'26 = 100%; Jul'24 = 55.1%; Aug'24 = 6.1% | Only 100%-coverage months are usable as held-out |

### Circularity hazard (the reason for factory-stripping)

`_agreement` (`engine_statistical.py:205`) grades the engine against
`factory_recommended_new_*`. `backfill_history.synthesise_reviews:115` copies that
same column into `review_history.final_max`. So on a backfilled month,
`agreement=='match'` means "engine within 10% of ground truth" **by construction**.

Measured example that must NOT be used as evidence: the
`agreement='match'` + Low-risk lane is 5730 rows with 4750 exact Max matches
(82.9%). That number is circular. The stripped-ingest protocol below is what makes
the measurement honest.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `analysis/s16_triage_backtest.py` | 1-100 | The exact temp-DB harness shape to mirror: env pinning, ingest→score→measure, CSV out, gate raise |
| P0 | `analysis/s15_autoclear_calibration.py` | 38-113 | Grading rule (`_agree`), result dict schema, grid loop, winner-selection print |
| P0 | `backend/app/engine_statistical.py` | 205-246 | `_agreement` / `_drift_ok` / `_benchmark` — the ladder being measured |
| P0 | `backend/app/engine_statistical.py` | 396-441 | Benchmark callsite + existing auto-clear policy block (exclusions to preserve) |
| P0 | `backend/scripts/backfill_history.py` | 115-176 | `synthesise_reviews` + `process` — reuse verbatim for history months |
| P1 | `backend/app/engine_adapter.py` | 51-84 | `_attach_prior_benchmark` — how `prior_*` reaches the engine |
| P1 | `backend/app/similarity.py` | 74-82, 575-581 | `ANALOGUE_CONCUR` / `ANALOGUE_DIVERGENCE` / `NO_RELIABLE_ANALOGUE` emission |
| P1 | `backend/app/similarity.py` | 381-411 | `run_similarity` signature, config keys, `status=='scored'` precondition |
| P1 | `backend/app/ingestion.py` | 26, 100-115 | `REQUIRED_COLS` (factory cols absent → stripping is safe), `ingest()` signature |
| P2 | `analysis/common.py` | 1-107 | Shared-helper home; `OUT`, `to_num`, `pct` |
| P2 | `backend/tests/test_statistical_engine.py` | 128-237 | Existing prior_review unit coverage — do not duplicate |
| P2 | `analysis/output/s15_autoclear_calibration.csv` | all | Output schema to stay compatible with |

## External Documentation

No external research needed — feature uses established internal patterns
(pandas, numpy, sqlite3 via `app.db`). No new dependencies.

---

## Patterns to Mirror

### TEMP_DB_HARNESS
```python
# SOURCE: analysis/s16_triage_backtest.py:20-37
def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update({
            "BOM_ALLOW_SQLITE": "1",
            "BOM_DB_PATH": str(Path(tmp) / "triage.db"),
            "DATABASE_URL": "",
            "LLM_BASE_URL": "",
            "BOM_ENGINE": "statistical",
        })
        sys.path.insert(0, str(ROOT / "backend"))
        from app.db import active_config, get_conn, init_db
        from app.engine_adapter import score_batch
        from app.ingestion import ingest

        init_db()
        conn = get_conn()
```
Env MUST be set before any `app.*` import — `app.config._load_dotenv()` runs at
import time and would otherwise point the harness at the real Supabase project.

### GRADING_RULE
```python
# SOURCE: analysis/s15_autoclear_calibration.py:38-43
TARGET_PRECISION = 98.0
LEVELS = ("max", "rop", "min")


def _agree(eng: np.ndarray, fac: np.ndarray, tol_abs=1.0, tol_rel=0.10) -> np.ndarray:
    return np.abs(eng - fac) <= np.maximum(tol_abs, tol_rel * np.abs(fac))
```

### RESULT_CARD_SCHEMA
```python
# SOURCE: analysis/s15_autoclear_calibration.py:67-73
    return {
        "cleared_%": round(100.0 * cleared.mean(), 1),
        "cleared_n": int(cleared.sum()),
        "precision_%": round(precision, 1) if n_cl_lab else np.nan,
        "n_labelled_cleared": n_cl_lab,
        "escaped_usd": round(escaped, 0),
    }
```

### WINNER_SELECTION
```python
# SOURCE: analysis/s15_autoclear_calibration.py:102-111
    ok = card[(card["precision_%"] >= TARGET_PRECISION) & (card["config"] != "baseline")]
    print(f"\ntarget precision >= {TARGET_PRECISION}%")
    if len(ok):
        best = ok.sort_values("cleared_n", ascending=False).iloc[0]
        print(f"recommended: {best['config']}  -> "
              f"{best['cleared_%']}% auto-cleared "
              f"({int(best['review_saved_vs_base']):+,} vs baseline), "
              f"precision {best['precision_%']}%, ${int(best['escaped_usd']):,} at risk")
    else:
        print("no config clears the target; loosen the target or tighten thresholds.")
```

### CONFIG_BAR_LOOKUP
```python
# SOURCE: analysis/s16_triage_backtest.py:39-40
            precision_bar = float(active_config(conn).get(
                "triage_clear_precision_bar", 0.98))
```

### HISTORY_SYNTHESIS
```python
# SOURCE: backend/scripts/backfill_history.py:161-176
def process(conn, path: Path, module: str, match: str, apply: bool) -> dict:
    label = label_for(path)
    ...
    content = path.read_bytes()
    summary = ingest(conn, content, label, path.name, module, "backfill",
                     match_mode=match)
    batch_id = summary["batch_id"]
    score = score_batch(conn, batch_id)
    reviews = synthesise_reviews(conn, batch_id, score["rule_version"])
```

### CSV_OUTPUT
```python
# SOURCE: analysis/s16_triage_backtest.py:91-94
    card = pd.DataFrame(cards)
    card.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(card.to_string(index=False))
    print(f"\nwrote: {OUT.relative_to(ROOT)}")
```
`encoding="utf-8-sig"` is not optional — every analysis CSV in this repo uses it.

### MODULE_DOCSTRING
```python
# SOURCE: analysis/s15_autoclear_calibration.py:1-20
"""S15 -- Auto-clear calibration: measure precision vs coverage on Jan'26.

...definitions of coverage / precision / escaped_$ ...

Run: python s15_autoclear_calibration.py   (add --all to size every item)
"""
```
Every `analysis/s*.py` opens with `"""SNN -- <one-line purpose>.` and ends the
docstring with a `Run:` line. Follow exactly.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `analysis/s17_clear_threshold_calibration.py` | CREATE | The harness: history load, stripped held-out ingest, rung grid, ranking output |
| `analysis/common.py` | UPDATE | Hoist `_agree` / `LEVELS` / `TARGET_PRECISION` so s15 and s17 share one grading definition |
| `analysis/s15_autoclear_calibration.py` | UPDATE | Import the hoisted helpers instead of defining them locally (behaviour unchanged) |
| `docs/log/2026-08-21_Clear_Threshold_Calibration.md` | CREATE | Result log — this repo records every backtest under `docs/` (see `Engine_Backtest_TCB_Jan26.md`, `Triage_Backtest_Jan26.md`) |

## NOT Building

- **No change to `engine_statistical.run()` auto-clear rungs.** Wiring a rung into
  production is a separate, later change gated on this harness's output.
- **No new keys in `analysis/engine/rule_config.json`.** The winning threshold is
  reported, not persisted.
- **No consumption feature added to `similarity.FEATURE_WEIGHTS`.** 93.6% of the
  pool has `last_365_day_cnsmptn_qty` = 0; the feature would be near-constant and
  would take weight from the nine that discriminate. Scale control, if needed
  later, is an IQR-width gate on the existing `analogue_max_p25/p75`.
- **No `agreement_source` "fix".** Confirmed stale data, not a defect.
- **No Dec_24 / March_2025 backfill.** ~350MB each, minutes per file. Behind an
  opt-in `--include-large` flag, off by default.
- **No frontend, no API endpoint, no `similarity_result` schema change.**

---

## Step-by-Step Tasks

### Task 1: Hoist the grading rule into `common.py`

- **ACTION**: Move `TARGET_PRECISION`, `LEVELS`, and `_agree` from
  `s15_autoclear_calibration.py:38-43` into `analysis/common.py`; rename `_agree`
  to `agree` (public, since two modules import it).
- **IMPLEMENT**: Append to `common.py` after `pct()` (line 105):
  ```python
  # --- shared grading rule (s15, s17) -----------------------------------------
  # "Review would have added nothing": engine value within 1 unit or 10% of the
  # engineer's, on all three levels. One definition, so two harnesses cannot drift.
  TARGET_PRECISION = 98.0
  LEVELS = ("max", "rop", "min")


  def agree(eng: np.ndarray, fac: np.ndarray, tol_abs=1.0, tol_rel=0.10) -> np.ndarray:
      return np.abs(eng - fac) <= np.maximum(tol_abs, tol_rel * np.abs(fac))
  ```
- **MIRROR**: GRADING_RULE.
- **IMPORTS**: none new — `common.py` already imports `numpy as np` (line 9).
- **GOTCHA**: Do NOT import `engine_statistical` into `common.py`. `common.py` is
  imported by every `s*.py`; adding a backend import would create two copies of
  `engine_statistical` in `sys.modules` (one top-level via `s13`'s
  `sys.path.insert`, one as `app.engine_statistical`).
- **VALIDATE**: `cd analysis && python -c "from common import agree, LEVELS, TARGET_PRECISION; print(TARGET_PRECISION)"` → `98.0`

### Task 2: Point s15 at the hoisted helpers

- **ACTION**: Replace s15's local definitions with an import. Behaviour must not change.
- **IMPLEMENT**: In `s15_autoclear_calibration.py`, change line 30
  `from common import OUT` → `from common import LEVELS, OUT, TARGET_PRECISION, agree`;
  delete lines 38-43; replace the two `_agree(` call sites at line 61 with `agree(`.
- **MIRROR**: GRADING_RULE.
- **IMPORTS**: as above.
- **GOTCHA**: `np` is still used elsewhere in s15 (`np.isnan`, `np.all`, `np.nan`) —
  do not remove the numpy import.
- **VALIDATE**: `cd analysis && python s15_autoclear_calibration.py` — output CSV
  must be byte-identical to the committed `analysis/output/s15_autoclear_calibration.csv`
  (baseline row still `3.2, 144, 70.8, 144, 29000.0, 0`).

### Task 3: Scaffold s17 — module docstring, constants, env-pinned harness

- **ACTION**: Create `analysis/s17_clear_threshold_calibration.py` with the temp-DB
  harness skeleton.
- **IMPLEMENT**:
  ```python
  """S17 -- Clear-threshold calibration: grade the prior_review and analogue rungs.

  s15 grades the engine's own auto-clear rungs (noop / immaterial / reliable) on a
  month whose factory_recommended_new_* columns are PRESENT. Those rungs never read
  that column, so the measurement is honest. The two rungs measured here do read it,
  directly or through review_history, so the same protocol would be circular:

      agreement       is computed against factory_recommended_new_*   (engine_statistical:205)
      review_history  final_* IS factory_recommended_new_*            (backfill_history:115)

  So the held-out month is ingested with those three columns STRIPPED. The engine
  then falls through _benchmark's ladder to the prior_review rung, similarity
  retrieves peers that never saw this month, and the withheld numbers are the label.

      coverage   = share of held-out rows a rung would clear
      precision  = of cleared LABELLED rows, share where engine matches engineer
                   (|delta| <= 1 or <= 10% on all three levels)
      escaped_$  = exposure of cleared rows where engine != engineer

  Rungs are MEASURED here, not wired: nothing in this script changes
  review_required in production.

  Outputs (analysis/output/):
      s17_clear_threshold.csv        one row per config: coverage/precision/escaped
      s17_confidence_ranking.csv     per-row confidence + was-changed, for ranking

  Run: python s17_clear_threshold_calibration.py   (--include-large for Dec_24/March_2025)
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

  # Ordered oldest -> newest. The held-out month must be LAST: similarity peers and
  # the prior_review benchmark may only come from months that precede it.
  HISTORY = [
      "AE_JULY'24 - BOM REVIEW - Factory Cost Rep Review_.xlsx",
      "AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx",
      "Sept_24 BOM REVIEW.xlsx",
      "Oct_24 BOM REVIEW.xlsx",
      "May'2025_BOM REVIEW.xlsx",
  ]
  HELD_OUT = "BOM REVIEW_Jan'26 .csv"
  LARGE = ["Dec_24 BOM REVIEW.xlsx", "March_2025_BOM REVIEW_UN94X4_8ae0dbe9591.xlsx"]

  MODULE = "TCB"
  MATCH_MODE = "exact"
  # PRD 5.1 engine-OUTPUT columns. Stripped from the held-out upload so the label
  # cannot reach the engine, similarity, or review_history.
  STRIP_COLS = ("factory_recommended_new_max", "factory_recommended_new_rop",
                "factory_recommended_new_min")
  ```
- **MIRROR**: MODULE_DOCSTRING, TEMP_DB_HARNESS.
- **IMPORTS**: as shown.
- **GOTCHA**: filenames must match `BOM table/` byte-for-byte, including the
  apostrophes and the trailing space in `"BOM REVIEW_Jan'26 .csv"` and
  `"AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx"`. Assert existence
  before the run rather than failing halfway through a 6-month load.
- **VALIDATE**: `cd analysis && python -c "import s17_clear_threshold_calibration as s; print([p.name for p in [s.WORKBOOKS/f for f in s.HISTORY+[s.HELD_OUT]] if not (s.WORKBOOKS/p.name).exists()])"` → `[]`

### Task 4: Factory-stripped ingest for the held-out month

- **ACTION**: Add `strip_labels(path)` returning `(stripped_bytes, labels_df)`.
- **IMPLEMENT**:
  ```python
  def strip_labels(path: Path) -> tuple[bytes, pd.DataFrame]:
      """Held-out upload with the engineer's answer removed, plus that answer.

      ingestion.REQUIRED_COLS does not include the factory columns, and
      engine_statistical._num returns an all-NaN Series for a missing column, so
      _benchmark falls straight to the prior_review rung. Nothing raises.
      """
      from app.ingestion import _read_table, normalize
      df = normalize(_read_table(path.read_bytes(), path.name))
      missing = [c for c in STRIP_COLS if c not in df.columns]
      if missing:
          raise SystemExit(f"{path.name}: no labels to withhold ({missing})")
      key = ["item_id", "stockroom_id"] if "stockroom_id" in df.columns else ["item_id"]
      labels = df[key + list(STRIP_COLS)].copy()
      return df.drop(columns=list(STRIP_COLS)).to_csv(index=False).encode("utf-8-sig"), labels
  ```
- **MIRROR**: HISTORY_SYNTHESIS (uses the same `ingest()` entry point).
- **IMPORTS**: `from app.ingestion import _read_table, normalize` — inside the
  function, after env pinning.
- **GOTCHA**: Re-serialise as **CSV** regardless of source format. `_read_table`
  returns all-string columns and `ingest()` re-parses; a round-trip through
  `to_csv` keeps the string fidelity the payload snapshot depends on. Keep
  `utf-8-sig` so `_read_table`'s CSV branch decodes the BOM.
- **GOTCHA**: `stockroom_id` may be absent; the primary key is
  `(batch_id, item_id, stockroom_id)` with `stockroom_id` defaulting to `''`
  (`db.py:141`). Match the join key to what ingestion actually stored, and strip
  whitespace on both sides — `engine_adapter` does (`stk = ...str.strip()`).
- **VALIDATE**: assert `all(c not in stripped_df.columns for c in STRIP_COLS)` and
  `len(labels) == len(df)`.

### Task 5: Load history, score, synthesise reviews

- **ACTION**: For each `HISTORY` workbook in order: ingest → `score_batch` →
  `synthesise_reviews`. Then ingest the stripped held-out month, score it, and run
  similarity.
- **IMPLEMENT**:
  ```python
  def build_pool(conn, include_large: bool) -> dict:
      from app.engine_adapter import score_batch
      from app.ingestion import ingest
      sys.path.insert(0, str(ROOT / "backend" / "scripts"))
      from backfill_history import synthesise_reviews

      files = HISTORY + (LARGE if include_large else [])
      loaded = []
      for name in files:
          path = WORKBOOKS / name
          summary = ingest(conn, path.read_bytes(), f"s17-{path.stem[:40]}",
                           path.name, MODULE, "s17-calibration",
                           match_mode=MATCH_MODE)
          score = score_batch(conn, summary["batch_id"])
          reviews = synthesise_reviews(conn, summary["batch_id"], score["rule_version"])
          loaded.append({"file": path.name, "rows": summary["rows_loaded"],
                         "scored": score["rows_scored"], **reviews})
          print(f"  history {path.name[:44]:46s} rows={summary['rows_loaded']:5,} "
                f"reviews={reviews['reviews']:5,}")
      return {"history": loaded}
  ```
- **MIRROR**: HISTORY_SYNTHESIS, TEMP_DB_HARNESS.
- **IMPORTS**: `sys.path` must already contain `ROOT/"backend"` (set in `main`)
  before importing `backfill_history`, which does `from app.db import ...`.
- **GOTCHA**: `synthesise_reviews` stamps `model_version='backfill-v1'`, which
  `similarity._summarise:527` deliberately excludes from override / high-risk
  **rates** while still counting toward the analogue **medians**. That is correct
  and must not be worked around — every peer here is backfilled, so
  `historical_override_rate` will be `None` throughout. Do not treat that as a bug
  or gate any rung on it.
- **GOTCHA**: Aug'24 contributes only 9 peers (6.1% label coverage) and Jul'24 135
  (55.1%). Load them anyway — they are legitimate peers — but do not expect them to
  move the pool.
- **VALIDATE**: after the loop, `SELECT COUNT(*) FROM review_history` ≈ 6279 for the
  default file set (exact count may shift with `match_mode`; assert > 6000).

### Task 6: Score the held-out month and run similarity

- **ACTION**: Ingest stripped bytes, score, then `run_similarity`.
- **IMPLEMENT**:
  ```python
      content, labels = strip_labels(WORKBOOKS / HELD_OUT)
      held = ingest(conn, content, "s17-heldout", HELD_OUT, MODULE,
                    "s17-calibration", match_mode=MATCH_MODE)
      bid = held["batch_id"]
      score_batch(conn, bid)
      from app.similarity import run_similarity
      sim = run_similarity(conn, bid)
      conn.commit()
      print(f"  held-out {HELD_OUT} batch={bid} rows={held['rows_loaded']:,} "
            f"no_analogue={sim.get('no_analogue', '?')}")
  ```
- **MIRROR**: TEMP_DB_HARNESS.
- **IMPORTS**: `from app.similarity import run_similarity`.
- **GOTCHA**: `run_similarity` raises `ValueError` unless `batches.status == 'scored'`
  (`similarity.py:387-388`). `score_batch` sets it (`engine_adapter.py:137-140`), so
  ordering is mandatory: ingest → score → similarity.
- **GOTCHA**: The held-out batch must never enter the peer pool. It does not —
  `_load_pool` reads `review_history`, and no reviews are synthesised for it. Assert
  this explicitly rather than trusting it:
  `SELECT COUNT(*) FROM review_history WHERE batch_id=?` → `0`.
- **VALIDATE**: `SELECT COUNT(*) FROM similarity_result WHERE batch_id=?` ≈ 2768;
  `SELECT COUNT(*) FROM recommendation_result WHERE batch_id=? AND agreement_source='prior_review'` > 0.
  If that second count is 0, the prior_review rung is not firing — stop and debug
  `_attach_prior_benchmark` before reading any precision number.

### Task 7: Assemble the evaluation frame

- **ACTION**: One row per held-out item joining engine output, similarity output,
  and the withheld labels.
- **IMPLEMENT**:
  ```python
  _EVAL_SQL = (
      "SELECT r.item_id, r.stockroom_id, r.new_max, r.new_rop, r.new_min, "
      "r.review_required, r.risk_level, r.route, r.agreement, r.agreement_source, "
      "r.exposure_usd, r.confidence AS engine_confidence, "
      "s.advisory_codes, s.confidence AS similarity_confidence, "
      "s.neighbour_count, s.analogue_max_median, s.analogue_max_p25, s.analogue_max_p75 "
      "FROM recommendation_result r "
      "LEFT JOIN similarity_result s ON s.batch_id=r.batch_id "
      "AND s.item_id=r.item_id AND s.stockroom_id=r.stockroom_id "
      "WHERE r.batch_id=?")


  def eval_frame(conn, bid: int, labels: pd.DataFrame) -> pd.DataFrame:
      df = pd.DataFrame([dict(r) for r in conn.execute(_EVAL_SQL, (bid,))])
      key = [c for c in ("item_id", "stockroom_id") if c in labels.columns]
      for c in key:
          df[c] = df[c].astype(str).str.strip()
          labels[c] = labels[c].astype(str).str.strip()
      df = df.merge(labels, on=key, how="left")
      for lvl in LEVELS:
          df[f"fac_{lvl}"] = pd.to_numeric(
              df[f"factory_recommended_new_{lvl}"], errors="coerce")
      df["labelled"] = np.all([df[f"fac_{l}"].notna() for l in LEVELS], axis=0)
      df["correct"] = np.all(
          [agree(df[f"new_{l}"].to_numpy(float), df[f"fac_{l}"].to_numpy(float))
           for l in LEVELS], axis=0)
      df["codes"] = df["advisory_codes"].fillna("")
      return df
  ```
- **MIRROR**: GRADING_RULE, and s16's `[dict(r) for r in conn.execute(...)]` row idiom.
- **IMPORTS**: already present.
- **GOTCHA**: `advisory_codes` is a comma-joined TEXT (`similarity.py:596`). Test
  membership with `df["codes"].str.split(",").apply(set)`, never a bare
  `str.contains` — `ANALOGUE_DIVERGENCE` contains no substring collision today, but
  a future code could.
- **GOTCHA**: `LEFT JOIN` — rows quarantined out of similarity, or targets skipped
  because `similarity_result` already existed, come back NULL. `neighbour_count`
  NULL must count as "no analogue", not as a truthy value.
- **VALIDATE**: `df["labelled"].sum()` ≈ 2768 (Jan'26 is 100% labelled). A materially
  lower number means the merge key is wrong — fix before proceeding.

### Task 8: Define the rung grid and evaluate

- **ACTION**: Express each rung as a boolean predicate over the eval frame; grade
  all of them through one `evaluate()`.
- **IMPLEMENT**:
  ```python
  # Exclusions carried over verbatim from the engine's own auto-clear policy
  # (engine_statistical.py:426): critical parts, non-active routes and High risk are
  # never cleared, whatever the evidence says.
  def _eligible(df: pd.DataFrame) -> np.ndarray:
      return (df["risk_level"] != "High").to_numpy() & (df["route"] == "active").to_numpy()


  def _has(df: pd.DataFrame, code: str) -> np.ndarray:
      return df["codes"].str.split(",").apply(lambda cs: code in cs).to_numpy()


  def rungs(df: pd.DataFrame) -> dict:
      prior = ((df["agreement"] == "match") &
               (df["agreement_source"] == "prior_review")).to_numpy()
      concur = _has(df, "ANALOGUE_CONCUR")
      conf = pd.to_numeric(df["similarity_confidence"], errors="coerce").fillna(0).to_numpy()
      grid = {
          "baseline_engine": (df["review_required"] == "N").to_numpy(),
          "prior_review_match": prior,
          "analogue_concur": concur,
          "prior_or_analogue": prior | concur,
          "prior_and_analogue": prior & concur,
      }
      for t in (0.5, 0.6, 0.7, 0.8, 0.9):
          grid[f"prior+conf>={t}"] = prior & (conf >= t)
          grid[f"analogue+conf>={t}"] = concur & (conf >= t)
      # baseline is the engine's own decision -- it already applies its exclusions.
      return {k: (v if k == "baseline_engine" else v & _eligible(df))
              for k, v in grid.items()}


  def evaluate(df: pd.DataFrame, cleared: np.ndarray) -> dict:
      labelled = df["labelled"].to_numpy()
      correct = df["correct"].to_numpy()
      exposure = pd.to_numeric(df["exposure_usd"], errors="coerce").fillna(0).to_numpy()
      cl_lab = cleared & labelled
      n = int(cl_lab.sum())
      return {
          "cleared_%": round(100.0 * cleared.mean(), 1),
          "cleared_n": int(cleared.sum()),
          "precision_%": round(100.0 * float((cl_lab & correct).sum()) / n, 1) if n else np.nan,
          "n_labelled_cleared": n,
          "escaped_usd": round(float(exposure[cl_lab & ~correct].sum()), 0),
      }
  ```
- **MIRROR**: RESULT_CARD_SCHEMA — identical keys so `s15_*.csv` and `s17_*.csv` are
  directly comparable.
- **IMPORTS**: already present.
- **GOTCHA**: `_eligible` deliberately omits the exposure and criticality tests that
  `engine_statistical:426` applies, because `sfm_criticality` is not on
  `recommendation_result`. `risk_level != 'High'` is the available proxy — critical
  parts score High (`test_statistical_engine.py:274`). State this limitation in the
  results log; do not silently present it as an exact reproduction of the engine's
  exclusions.
- **GOTCHA**: `prior_and_analogue` will have a small `n`. Report `n_labelled_cleared`
  alongside every precision, and refuse to name a winner whose `n` < 30.
- **VALIDATE**: `evaluate(df, np.zeros(len(df), bool))` → `cleared_n == 0`,
  `precision_% is NaN`, no exception.

### Task 9: Confidence ranking output (step 4 of the brief)

- **ACTION**: Emit per-row confidence and a decile table, so rows below the clear
  threshold are still ordered by how likely review is to change something.
- **IMPLEMENT**:
  ```python
  def ranking(df: pd.DataFrame, cleared: np.ndarray) -> pd.DataFrame:
      """Precision by confidence decile over the rows the winner did NOT clear.

      A rung that clears nothing can still be worth shipping: if precision rises
      monotonically with confidence, the queue can be ORDERED even where it cannot
      be cut.
      """
      rest = df.loc[~cleared & df["labelled"]].copy()
      conf = pd.to_numeric(rest["similarity_confidence"], errors="coerce").fillna(0)
      rest["decile"] = pd.qcut(conf.rank(method="first"), 10,
                               labels=False, duplicates="drop")
      out = rest.groupby("decile").agg(
          n=("correct", "size"),
          precision_pct=("correct", lambda s: round(100.0 * s.mean(), 1)),
          min_confidence=("similarity_confidence", "min"),
          max_confidence=("similarity_confidence", "max"),
      ).reset_index()
      return out
  ```
- **MIRROR**: CSV_OUTPUT.
- **IMPORTS**: already present.
- **GOTCHA**: `pd.qcut` raises on duplicate bin edges when confidence is heavily
  tied — likely here, since `confidence = 0.5*coverage + 0.5*closeness` takes few
  distinct values. `rank(method="first")` plus `duplicates="drop"` handles it; do
  not drop the rank.
- **VALIDATE**: decile table has ≥ 2 rows and `n` sums to `(~cleared & labelled).sum()`.

### Task 10: `main()` — wire it together, write CSVs, name the winner

- **ACTION**: Assemble; write `s17_clear_threshold.csv` and
  `s17_confidence_ranking.csv`; print the winner using the configured bar.
- **IMPLEMENT**:
  ```python
  def main() -> None:
      include_large = "--include-large" in sys.argv
      with tempfile.TemporaryDirectory() as tmp:
          os.environ.update({
              "BOM_ALLOW_SQLITE": "1",
              "BOM_DB_PATH": str(Path(tmp) / "s17.db"),
              "DATABASE_URL": "",
              "LLM_BASE_URL": "",
              "BOM_ENGINE": "statistical",
          })
          sys.path.insert(0, str(ROOT / "backend"))
          from app.db import active_config, get_conn, init_db
          init_db()
          conn = get_conn()
          try:
              bar = 100.0 * float(active_config(conn).get(
                  "triage_clear_precision_bar", TARGET_PRECISION / 100.0))
              build_pool(conn, include_large)
              df, bid = held_out(conn)          # tasks 4 + 6, returns eval frame
              grid = rungs(df)
              card = pd.DataFrame([{"config": k, **evaluate(df, v)}
                                   for k, v in grid.items()])
              base = card.loc[card["config"] == "baseline_engine", "cleared_n"].iloc[0]
              card["review_saved_vs_base"] = card["cleared_n"] - base
              card.to_csv(OUT / "s17_clear_threshold.csv", index=False,
                          encoding="utf-8-sig")
              print(card.to_string(index=False))

              ok = card[(card["precision_%"] >= bar) &
                        (card["n_labelled_cleared"] >= 30) &
                        (card["config"] != "baseline_engine")]
              print(f"\ntarget precision >= {bar}%  (min 30 labelled cleared rows)")
              if len(ok):
                  best = ok.sort_values("cleared_n", ascending=False).iloc[0]
                  print(f"recommended: {best['config']}  -> {best['cleared_%']}% cleared "
                        f"({int(best['review_saved_vs_base']):+,} vs baseline), "
                        f"precision {best['precision_%']}%, "
                        f"${int(best['escaped_usd']):,} at risk")
                  winner = grid[best["config"]]
              else:
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
  ```
- **MIRROR**: WINNER_SELECTION, CONFIG_BAR_LOOKUP, CSV_OUTPUT, TEMP_DB_HARNESS.
- **GOTCHA**: Unlike s16, do **not** `raise SystemExit` when no rung clears the bar.
  s16 is a regression gate on a shipped feature; s17 is an experiment, and "nothing
  clears 98%" is a valid, informative result. Exit 0.
- **GOTCHA**: `active_config` returns the bar as a fraction (0.98); s15's constant is
  a percentage (98.0). Convert once, at the boundary, as shown.
- **VALIDATE**: `cd analysis && python s17_clear_threshold_calibration.py` runs end to
  end and writes both CSVs.

### Task 11: Self-check

- **ACTION**: Add `--selftest` exercising `agree`, `_has`, `evaluate`, and `ranking`
  on a synthetic frame with known answers.
- **IMPLEMENT**:
  ```python
  def selftest() -> None:
      """Smallest thing that fails if the grading logic breaks. No DB, no fixtures."""
      assert agree(np.array([100.0]), np.array([105.0]))[0]        # within 10%
      assert not agree(np.array([100.0]), np.array([200.0]))[0]
      assert agree(np.array([1.0]), np.array([2.0]))[0]            # abs tolerance wins

      df = pd.DataFrame({
          "codes": ["ANALOGUE_CONCUR", "ANALOGUE_DIVERGENCE", ""],
          "labelled": [True, True, False],
          "correct": [True, False, True],
          "exposure_usd": [10.0, 250.0, 999.0],
      })
      assert list(_has(df, "ANALOGUE_CONCUR")) == [True, False, False]
      r = evaluate(df, np.array([True, True, True]))
      assert r["cleared_n"] == 3 and r["n_labelled_cleared"] == 2
      assert r["precision_%"] == 50.0                # 1 of 2 labelled cleared
      assert r["escaped_usd"] == 250.0               # unlabelled row excluded
      assert np.isnan(evaluate(df, np.zeros(3, bool))["precision_%"])
      print("selftest ok")
  ```
  Call it first in `main()`: `if "--selftest" in sys.argv: return selftest()`.
- **MIRROR**: assert-style checks, consistent with the repo's no-extra-scaffolding
  approach for `analysis/` (no pytest lives there).
- **GOTCHA**: `evaluate` reads `df["labelled"]`/`df["correct"]` as numpy bools —
  build the synthetic frame with real bools, not 0/1 ints, or `& ~correct` behaves
  as bitwise arithmetic on integers.
- **VALIDATE**: `cd analysis && python s17_clear_threshold_calibration.py --selftest`
  → `selftest ok`, exit 0, no DB touched.

### Task 12: Record the result

- **ACTION**: Write `docs/log/2026-08-21_Clear_Threshold_Calibration.md` with the two
  tables, the protocol, and the caveats.
- **IMPLEMENT**: Mirror `docs/Triage_Backtest_Jan26.md`'s shape — a table first, then
  the interpretation. Must state explicitly: (a) the factory-stripping protocol and
  why; (b) that `analogue_concur`'s addressable set on Jan'26 is 57 rows with no
  prior review, so its standalone precision has wide error bars; (c) that
  `risk_level != 'High'` is a proxy for the engine's criticality exclusion; (d) the
  circular 82.9% figure and why it is not used.
- **MIRROR**: existing backtest docs.
- **VALIDATE**: numbers in the doc match the committed CSVs exactly.

---

## Testing Strategy

### Unit / self-check

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `agree` relative tolerance | eng=100, fac=105 | True | No |
| `agree` beyond tolerance | eng=100, fac=200 | False | No |
| `agree` absolute floor | eng=1, fac=2 | True (abs 1 wins over 10%) | Yes |
| `_has` exact code match | codes=`ANALOGUE_DIVERGENCE`, query `ANALOGUE_CONCUR` | False | Yes — substring trap |
| `evaluate` unlabelled excluded | 3 rows, 1 unlabelled | `n_labelled_cleared==2` | Yes |
| `evaluate` nothing cleared | all-False mask | `precision_%` NaN, no ZeroDivision | Yes |
| `evaluate` escaped sums only wrong+labelled | as in selftest | 250.0 | Yes |

### Integration assertions (inside the run, fail loudly)

- Held-out batch contributes **0** rows to `review_history`.
- `agreement_source='prior_review'` count > 0 on the held-out batch — otherwise the
  rung never fired and every precision number is meaningless.
- `df["labelled"].sum()` ≈ row count (Jan'26 is 100% labelled).
- `similarity_result` row count ≈ held-out scored row count.

### Edge Cases Checklist

- [ ] Held-out workbook missing a factory column → `SystemExit` with a clear message
- [ ] `stockroom_id` absent from source → merge falls back to `item_id` only
- [ ] All-tied confidence → `qcut` does not raise (rank + `duplicates="drop"`)
- [ ] No rung clears the bar → prints "rank only", exits 0
- [ ] Rung with `n_labelled_cleared < 30` → excluded from winner selection
- [ ] `similarity_result` LEFT JOIN NULLs → treated as no-analogue, not truthy
- [ ] `--include-large` off by default (Dec_24 / March_2025 are ~350MB each)

---

## Validation Commands

### Self-check (no DB, fast)
```bash
cd analysis && python s17_clear_threshold_calibration.py --selftest
```
EXPECT: `selftest ok`, exit 0

### s15 regression (Task 2 must not change behaviour)
```bash
cd analysis && python s15_autoclear_calibration.py
git diff --stat analysis/output/s15_autoclear_calibration.csv
```
EXPECT: no diff — baseline row still `3.2, 144, 70.8, 144, 29000.0, 0`

### Full harness
```bash
cd analysis && python s17_clear_threshold_calibration.py
```
EXPECT: both CSVs written; a printed grid; either a named winner or "rank only"

### Backend test suite (no regressions from the `common.py` change)
```bash
make test
```
EXPECT: all pass. `common.py` is analysis-only, so this should be untouched —
run it to confirm nothing imported it transitively.

### Manual validation
- [ ] `analysis/output/s17_clear_threshold.csv` has the same column names as
      `s15_autoclear_calibration.csv` plus `config`
- [ ] `prior_review_match` row has `n_labelled_cleared` in the low thousands
      (addressable set is 2711) — a tiny number means the rung is not firing
- [ ] `analogue_concur` row's `n_labelled_cleared` is small (~tens) — expected,
      and the reason its precision is reported with an explicit `n`
- [ ] `escaped_usd` for any recommended config is a number you would defend to the
      inventory owner

---

## Acceptance Criteria

- [ ] `--selftest` passes
- [ ] s15 output byte-identical after the `common.py` hoist
- [ ] Harness runs end to end on the default 5 history months + Jan'26
- [ ] Both CSVs written to `analysis/output/`
- [ ] Integration assertions all hold (esp. `prior_review` fired, held-out not in pool)
- [ ] Results doc written with the four mandated caveats
- [ ] No change to `engine_statistical.py`, `similarity.py`, or `rule_config.json`

## Completion Checklist

- [ ] Docstring follows `"""SNN -- purpose.` + `Run:` convention
- [ ] `encoding="utf-8-sig"` on every `to_csv`
- [ ] Env pinned before any `app.*` import
- [ ] Result dict keys identical to s15's
- [ ] No hardcoded precision bar — read from `active_config`
- [ ] No new dependencies
- [ ] Scope held: measurement only, no production rung wired

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `prior_review` rung never fires (1-day-old code, never run on real data) | Medium | High — all numbers meaningless | Hard assert on `agreement_source='prior_review'` count > 0 before evaluating; stop the run if 0 |
| `analogue_concur` set too small for a credible precision | **High** | Medium | Only 57 Jan'26 rows lack a prior review; enforce `n >= 30` for winner selection and print `n` beside every precision |
| Merge key mismatch silently drops labels | Medium | High | Assert `labelled.sum()` ≈ row count; Jan'26 is 100% labelled so any shortfall is a join bug |
| Stripping changes engine behaviour beyond the benchmark | Low | High | `REQUIRED_COLS` excludes the factory columns and `_num` returns NaN for missing ones; sizing reads none of them (`test_benchmark_never_changes_sizing`) |
| Held-out month leaks into the peer pool | Low | Critical | No reviews synthesised for it; assert `review_history` count = 0 for that batch |
| Only one usable held-out month | High | Medium | Jan'26 is the newest 100%-labelled month. Sept'24/Oct'24 could serve as secondary held-outs with less history behind them — note as follow-up, do not build now |
| `risk_level != 'High'` ≠ the engine's criticality exclusion | Medium | Low | Documented limitation in the results log, not silently presented as equivalent |

## Notes

- The two rungs answer different populations and must never be averaged into one
  headline: `prior_review` addresses **2711 of 2768** Jan'26 rows (97.9%),
  `ANALOGUE_CONCUR` alone addresses the **57** with no prior decision (2.1%).
  A combined "auto-clear rate" hides which signal earned it.
- Consumption stays out of `FEATURE_WEIGHTS` deliberately (93.6% of the pool is
  zero). If demand scale needs controlling later, the lever is an IQR-width gate on
  the existing `analogue_max_p25/p75`, not a tenth feature.
- Once a rung clears the bar, wiring it is a **separate** change: `prior_review`
  belongs in the auto-clear block at `engine_statistical.py:426` (the signal is
  already local at line 404); `ANALOGUE_CONCUR` cannot go there — similarity runs
  after scoring, so it needs a second pass updating `review_required`. That split is
  the main reason this plan stops at measurement.
