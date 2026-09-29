# Implementation Report: Clear-Threshold Calibration (S17)

## Summary

Built `analysis/s17_clear_threshold_calibration.py`, a DB-backed harness that grades
the `prior_review` and `ANALOGUE_CONCUR` rungs on a factory-stripped held-out month,
selects a clear threshold against the configured precision bar, and emits a
confidence-decile ranking for uncleared rows. Ran it. Results in
`docs/log/2026-08-21_Clear_Threshold_Calibration.md`.

Verdict from the run: **no rung clears the 98% bar** — the harness recommends rank
only, do not auto-clear. No production auto-clear behaviour was changed, as planned.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium — as estimated |
| Confidence | 8/10 single-pass | Achieved, with 3 in-flight deviations |
| Files Changed | 4 | 4 (+2 generated CSVs) |
| Tasks | 12 | 12 complete |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Hoist grading rule into `common.py` | Complete | `TARGET_PRECISION`, `LEVELS`, `agree` |
| 2 | Point s15 at hoisted helpers | Complete | **Deviated** — name collision, see below |
| 3 | Scaffold s17 (docstring, constants) | Complete | |
| 4 | Factory-stripped ingest | Complete | Added `drop_duplicates` on label key |
| 5 | Load history, score, synthesise | Complete | **Deviated** — locked-file handling |
| 6 | Score held-out + similarity | Complete | Both leak assertions fire correctly |
| 7 | Assemble evaluation frame | Complete | 2,768 rows, 2,768 labelled — merge verified |
| 8 | Rung grid + evaluate | Complete | **Deviated** — added no-route-gate variants |
| 9 | Confidence ranking output | Complete | 10 deciles, `qcut` rank trick needed as predicted |
| 10 | `main()` driver | Complete | Exits 0 on "no winner", as specified |
| 11 | Self-check | Complete | 8 assertions, passes |
| 12 | Results doc | Complete | **Expanded** — added zero-agreement decomposition |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `py_compile` clean on all three Python files |
| Self-check | Pass | `--selftest` → `selftest ok` |
| s15 Regression | Pass | Output byte-identical (`diff` → no output) |
| Backend Suite | Pass | **260 passed** in 72.7s, zero failures |
| Full Harness | Pass | End-to-end run, both CSVs written |
| Edge Cases | Pass | Locked file, NaN precision, tied confidence, missing workbook |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `analysis/s17_clear_threshold_calibration.py` | CREATED | +421 |
| `docs/log/2026-08-21_Clear_Threshold_Calibration.md` | CREATED | +150 |
| `analysis/common.py` | UPDATED | +12 |
| `analysis/s15_autoclear_calibration.py` | UPDATED | +4 / −12 |
| `analysis/output/s17_clear_threshold.csv` | GENERATED | 18 rows |
| `analysis/output/s17_confidence_ranking.csv` | GENERATED | 11 rows |

## Deviations from Plan

**1. `agree` name collision in s15 (Task 2).**
WHAT: The plan said to swap the two `_agree(` call sites for `agree(`. Line 61 held
a local variable *named* `agree` (`agree = np.all([_agree(...)])`), which would have
shadowed the import and produced a `TypeError` on the second call.
WHY: Renamed the local to `correct`, matching the naming used in s17's `eval_frame`.
Output verified byte-identical afterwards.

**2. Locked-workbook handling in `build_pool` (Task 5).**
WHAT: `AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx` raised
`PermissionError: [Errno 13]` mid-run — held open by another process (mtime and size
had changed minutes earlier). The plan assumed all workbooks readable.
WHY: These live in a shared OneDrive folder and being open in Excel is normal, so
dying at month 2 of 6 is the wrong behaviour. `build_pool` now catches `OSError`,
prints a `SKIP` line, returns the skip list, and `main` prints a warning beside the
results — because a missing month changes the peer pool and therefore every number.
Raises `SystemExit` only if *no* history month could be read.

**3. No-route-gate variants and eligibility census (Task 8).**
WHAT: Added `prior_review_match|no_route_gate`, `analogue_concur|no_route_gate`, and
a printed route/eligibility census.
WHY: The first full run produced single-digit cleared counts for every rung. The
cause was not rung quality — only **61 of 2,768** rows pass the engine's
`route == 'active'` exclusion, because Jan'26 is 93% dormant. Without a gate-free
comparison the card could not distinguish "the rung is weak" from "the gate is
binding", which are different conclusions with different fixes. The variants show
the rungs reach 91% coverage at ~96.6% precision when ungated. These are measurement
rows, not a recommendation to remove the gate — see below.

**4. Results doc expanded with a zero-agreement decomposition (Task 12).**
WHAT: Added "Result 2", backed by an extra query against Supabase batch 13.
WHY: The 96.6% ungated precision would have been misleading published as-is. The
engine zeroes **100% of dormant rows**; of 2,482 dormant "agreements", 2,061 are
both-parties-zero and the other 421 are the engineer writing 1 against the engine's
0, forgiven by the `tol_abs = 1.0` floor. Precision by route is
**96.4% dormant → 80.8% dying → 50.0% active** — it falls as the engine actually has
to decide something. Publishing the headline without this would have argued for
removing the route gate; the decomposition argues the opposite.

## Issues Encountered

| Issue | Resolution |
|---|---|
| `PermissionError` on Aug'24 workbook | Skip-with-warning (deviation 2). File is still locked; re-run when free. Costs 9 peers of 2,947 — not material |
| Local `agree` shadowing the import | Renamed to `correct` (deviation 1) |
| Every rung clearing single digits | Root-caused to the route gate, not the rungs (deviation 3) |
| Ungated precision looked publishable but wasn't | Decomposed by route; caveat now leads the section (deviation 4) |

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `s17_clear_threshold_calibration.py::selftest` | 8 assertions | `agree` (relative, absolute, reject), `_has` substring safety, `evaluate` (unlabelled exclusion, precision, escaped USD, empty-mask NaN), `_eligible` |

No pytest file added: `analysis/` has no test infrastructure and adding one would be
new scaffolding for a single script. Follows the repo's existing analysis-script
convention. The backend suite (260 tests) covers the production code this harness
drives, including the `prior_review` rung's unit behaviour
(`test_statistical_engine.py:150-237`).

## Key Findings (detail in the results log)

1. **Route gate is the binding constraint.** 61 of 2,768 rows eligible (2.2%).
2. **`prior_review` fired on 2,570 rows** — the rung, one day old at plan time, works
   against real data. First confirmation.
3. **Current `baseline_engine` is the worst option on the card**: 29 rows cleared at
   79.3% precision leaking **$70,419**, versus `prior_review_match|no_route_gate` at
   2,532 rows, 96.6%, $2,943.
4. **The ungated 96.6% is mostly "both said zero"** and is not evidence of judgment.
5. **Confidence ranks**: bottom decile 77.3% vs ~95% for the rest, but saturates at
   0.976/1.000 for 80% of rows — usable to flag the worst decile, not to order the
   full queue.

## Post-Review Fixes (`/code-review`)

The review returned 15 findings. 11 were on pre-existing branch code (`af9b750` and
earlier), not on this change. Four applied here and were fixed:

| # | Finding | Fix | Severity |
|---|---|---|---|
| 1 | `main()` cleared `DATABASE_URL` but not `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_SECRET_KEY`. `config.use_rest()` is a second route to the real project and switches on those alone — `is_postgres()` would be True, `BOM_DB_PATH` ignored, and the harness would write six months of synthetic batches into **production Supabase**. Copied from s16, which has the same hole; `conftest.py:26` closes it deliberately. | All three now cleared, with the reason in a comment | **High** |
| 4 | `--include-large` appended Dec_24 and March_2025 *after* May'2025, but both precede it. Since `_attach_prior_benchmark` and `_load_pool` resolve "latest decision" by `review_id` (insertion order), the flag silently changed which precedent the rung was graded against. | `HISTORY` is now `(name, is_large)` in true chronological order, filtered by `history_files()` | **High** |
| 14 | `ANALOGUE_CONCUR` restated as a local literal with no drift guard — a rename in `similarity.py` would make every analogue rung report `cleared_n=0` / `NaN` and exit 0 as a valid negative result | Assert against `app.similarity.ANALOGUE_CONCUR` once the deferred import is available | Medium |
| 13 | `common.agree` duplicates `engine_statistical._agreement.close()`; s17 mixes the two (engine decides `match`, harness computes precision) | Comment naming the coupling. Not merged: importing `backend/app` into `common.py` loads `engine_statistical` a second time under a different module name, which every analysis script's `sys.path` layout makes likely | Low |

Re-validated after the fixes: `--selftest` passes, s15 output still byte-identical,
**260 backend tests pass**, full harness run produces the same card.

Aug'24 became readable on the re-run and loaded cleanly (9 reviews). Results were
**byte-identical**, confirming empirically that its parts were already represented in
later months — the peer pool is 2,947 unique parts from 3,511 reviews either way.

### One finding that corrects this report's own framing

Finding #9 (pre-existing code, `agent/specialists.py:151`): `safe_clear` already
accepts `agreement_source in ("factory", "prior_review")`. So **`prior_review` is
already wired into the agent's auto-clear path** — it gates `clear_candidate` and
`recommend.bulk_acceptable` today, shipped in `af9b750`.

The claim "no rung is wired into production" is true of *this change* but false of
the *system*. The uncalibrated rung is already clearing rows, and this harness is its
first calibration — which it does not pass. Recorded in the results log's Next
section as the top action item.

## Next Steps

- [ ] Re-run once Aug'24 is unlocked; optionally `--include-large` for Dec_24/March_2025
- [ ] Do **not** wire either rung into auto-clear — neither clears the bar
- [ ] Consider the dormant lane as the real target: the engine zeroes all 2,575
      dormant rows without discriminating between them
- [ ] Ship confidence as a review-queue ranking input (no precision bar required)
- [ ] Code review via `/code-review`
