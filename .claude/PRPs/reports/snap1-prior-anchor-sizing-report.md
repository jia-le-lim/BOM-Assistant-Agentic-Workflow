# Implementation Report: Snap-1 + prior anchor sizing for live routes

## Summary

Added two config-gated levers to `engine_statistical.run()` that change the proposed
Min/ROP/Max on `active` / `dying` rows only. `continuity_snap` holds the level already in
force when the lead-time quantile lands within N units of it; `prior_anchor_policy` prefers
the part's own previous engineer decision inside that band when `replenishment_policy`
says SAP does not hold the part to a Max.

**Shipped ON** (`CONTINUITY_SNAP = 1`, `PRIOR_ANCHOR_POLICY = "demand"`) — owner decision,
2026-09-10, taken after the implementation landed with both levers off. Setting them back
to `0` / `""` restores the previous sizing, and a test pins that escape hatch.

The analysis harness `s26_small_delta_tuning.py` now asserts the engine reproduces its
rule row-for-row. That assertion failed on first run and caught a real defect — in the
harness, not the engine (see Deviations).

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Small | Small — confirmed |
| Confidence | 9/10 | Justified; one planned-fixture error and one harness bug, both caught by the plan's own guards |
| Files Changed | 3 | 4 (docs update was on the checklist but not in the file table) |
| Tests | 7 new | 10 new |
| Full suite | 271 → 278 | 375 → 384 (plan quoted a stale count from the divergence doc) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Declare the two levers | Complete | landed off; flipped to `1` / `"demand"` in the second pass below |
| 2 | Read cfg keys + policy column | Complete | `df.get(...)` guard confirmed needed — bare `run()` has no such column |
| 3 | Apply the snap inside the live branch | Complete | Placed before the route reason codes, as planned |
| 4 | Continuity-snap tests | Complete | Deviated — fixture changed, see below |
| 5 | Prior-anchor tests | Complete | Added one test beyond the plan |
| 6 | Extend the defaults-off guard | Complete | Later inverted when the defaults flipped |
| 7 | Engine/harness parity guard | Complete | **Found a real defect on first run** |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `py_compile` clean on both changed Python files |
| Unit Tests | Pass | `test_statistical_engine.py` 34 → 43 tests |
| Full Suite | Pass | **384 passed**, 1 pre-existing unrelated warning (`StarletteDeprecationWarning`) |
| Build | N/A | Python backend, no build step |
| Integration | Pass | `test_end_to_end_statistical_engine` and `test_prior_review_benchmark_round_trips_through_score_batch` green |
| Edge Cases | Pass | Missing `replenishment_policy`, missing `prior_final_*`, NaN `max_qty`, dormant isolation, band-closed all covered by tests |
| Baseline regression | Pass | `s20_diverge_rootcause.py` identical after the flip too: 79.7% / 30.9% (it pins `ANCHOR_OFF`) |
| Analysis parity | Pass | `s26` asserts the SHIPPED DEFAULTS reproduce the harness rule row-for-row |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `backend/app/engine_statistical.py` | UPDATED | +43 |
| `backend/tests/test_statistical_engine.py` | UPDATED | +99 / -1 |
| `docs/CURRENT_CODEBASE_END_TO_END.md` | UPDATED | new §13.7a |
| `analysis/s26_small_delta_tuning.py` | UPDATED | parity arm, the ROP fix below, `ANCHOR_OFF` baseline |
| `analysis/s20_diverge_rootcause.py` | UPDATED | `ANCHOR_OFF` baseline (3 call sites) |
| `analysis/s23_live_route_autopsy.py` | UPDATED | `ANCHOR_OFF` baseline |
| `analysis/s25_ladder_jan26_replay.py` | UPDATED | `ANCHOR_OFF` baseline |

## Deviations from Plan

1. **Test fixture (Tasks 4–5).** The plan reused `_CONST` (12 units/year). That row sizes
   to Max 3 against a current of 1, which is *outside* the one-unit band, so the snap
   never fires and the test would have asserted nothing. Replaced with a new `_LOW`
   fixture — one issue a year, nothing in the last quarter — which is the population the
   lever exists for. **WHY it matters:** `_CONST` sitting outside the band is not a fixture
   detail, it is the feature's scope; the comment on `_LOW` records that.

2. **Two extra tests (9, not 7).** Added `test_prior_anchor_needs_the_snap_band_to_be_open`
   (a prior decision far from both the engine and the current level is an old number, not
   evidence) and `test_anchoring_levers_are_off_on_the_population_they_target` — the
   plan's defaults-off guard runs on `_CONST`, which cannot detect a default flip on the
   rows the lever actually touches.

3. **Harness ROP convention removed — the parity guard earned its place.** The assertion
   in Task 7 failed on first run: 67 live rows disagreed. All 67 were *outside* the snap
   band, where `s26`'s `snap_rule`/`snap_prior` rewrote `ROP = round(Max/2)` while the
   engine keeps its own quantile ROP. The engine was right: the plan never specified that
   rewrite, and the original parameter sweep had already scored `rop="engine"` at 55.6%
   against `rop="half"` at 55.4%. Removed the rewrite from the harness.
   **Consequence — the published figures moved, slightly up:**

   | | before the fix | after |
   |---|---|---|
   | delta ≤1 | 57.9% | **58.1%** |
   | delta ≤2 | 53.4% | **53.5%** |
   | all live | 44.9% | **45.0%** |
   | right on proposals | 19.6% | **20.1%** |
   | active / dying | 31.5 / 52.4 | 32.3 / 52.2 |

4. **Reason-code ordering.** Emits `CONTINUITY_SNAP,DYING_DEMAND`, not the plan's
   illustrative `DYING_DEMAND,CONTINUITY_SNAP`. The block runs before the route reason
   codes by design, so `BIG_CHANGE` cannot fire on a snapped row. Cosmetic; all tests use
   `in`, and `ReasonCodes` renders order-independently.

5. **Skipped the Phase 2 remote pull.** On a feature branch with this session's untracked
   analysis files; nothing to rebase onto and a mid-implementation rebase adds risk with
   no benefit.

## Issues Encountered

- **The planned gotcha was real.** Before carrying Min through the anchor, the clamp
  `new_rop = max(new_rop, new_min)` at `engine_statistical.py:449-451` re-raised ROP to
  the quantile's Min and dragged Max back up, silently undoing the snap on exactly the
  rows it targets. Handled as planned; pinned by
  `test_snap_carries_min_so_the_monotonic_clamp_cannot_undo_it`.
- **The pickle carries no `prior_final_*` columns.** The plan flagged this; without
  writing them onto the frame first, the parity arm would have "passed" with the anchor
  never firing. `s26` now writes them explicitly with a comment saying why.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_statistical_engine.py` | 10 new | snap holds current; far number untouched; Min carried through the clamp; dormant isolation; prior anchor fires on Order-To-Demand; ignored on Order-To-Max; falls back without a prior; requires the band open; both levers ON by default on the targeted population; both can be switched back off |

## Live Effect (levers ON, eight TCB cycles)

| | shipped | with both levers |
|---|---|---|
| all live rows (711) | 30.9% | **45.0%** |
| low delta ≤1 (549) | 40.1% | **58.1%** |
| proposals | 481 | 194 |
| right on proposals | 18.3% | **20.1%** |
| book value | $2.53M | $2.34M |

Wins 5 of 8 cycles. Loses 2024-10 (85.7% → 73.8% on the band), the cycle where engineers
held nearly every level.

## Turning the levers on (second pass, same session)

Flipping the defaults was not a one-line change, because "the default" is the behaviour:

- **3 tests failed**, all three the ones that encoded "off by default"; nothing else in the
  suite moved, which is the useful signal that the change is well contained. Rewritten:
  `test_demand_model_levers_default_off_match_baseline` (renamed — it now guards only the
  demand-model levers), `test_anchoring_levers_are_on_by_default`, and a new
  `test_anchoring_can_be_switched_back_off` pinning the rollback path.
- **Three earlier tests silently lost their contrast.** They used `_run()` as the
  unanchored baseline, which it no longer is. Repointed at an explicit
  `_OFF = {"continuity_snap": 0, "prior_anchor_policy": ""}`.
- **Four analysis harnesses would have silently changed meaning.** `s20`, `s23`, `s25` and
  `s26` all take `E.run(df)` as their baseline column. With the levers on that baseline
  becomes the anchored engine, and `s26` would have double-applied the rule on top of an
  already-anchored `e0`. All four now pin `ANCHOR_OFF` in their baselines, with a comment
  saying why; `s26`'s parity arm switched to plain `E.run(df)` so it now asserts the
  **shipped defaults** reproduce the harness rule.
- Verified after the flip: `s20_diverge_rootcause.py` still prints 79.7% / 30.9%
  (the autopsy still describes the engine it was written about), `s26` parity holds, and
  the full suite is **384 passed**.

## Next Steps

- [ ] Code review via `/code-review`
- [x] Owner decision on enabling `continuity_snap` / `prior_anchor_policy` — ON, 2026-09-10
- [ ] Fill-rate backtest (ML plan Phase 4) still outstanding — the stocking risk of any
      sizing change remains unbounded without it
