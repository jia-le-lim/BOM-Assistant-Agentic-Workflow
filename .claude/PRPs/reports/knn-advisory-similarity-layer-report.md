# Implementation Report: KNN Advisory Similarity Layer

## Summary

Added a deterministic k-nearest-neighbour peer-evidence layer that runs after the
statistical engine scores a batch and before the engineer reviews it. For each
scored row it retrieves the closest historical peer parts from `review_history`
joined to that peer's own frozen `bom_rows` snapshot, then persists neighbour
evidence, an outlier score, distance-weighted analogue Max/ROP/Min ranges, and
historical override/high-risk rates into two new advisory tables.

The layer writes `similarity_result` and `similarity_neighbour` and nothing else.
It never touches `recommendation_result`, never lowers a risk level or triage
tier, and no number it produces can reach a WINGS export. It can only add
evidence and raise review priority.

No new dependencies. `numpy` was already pinned.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Large | Large — accurate |
| Confidence | 8/10 single-pass | Held. Three defects found and fixed during validation, none structural |
| Files Changed | 17 (5 CREATE, 12 UPDATE) | 20 (3 CREATE, 17 UPDATE) |
| New dependencies | 0 | 0 |
| Lines | ~1,100 | 1,039 new + 541 inserted / 23 deleted |

Two files were touched that the plan did not anticipate: `frontend/src/components/ui.tsx`
(added a `warning` kind to `Banner`) and `backend/app/agent/specialists.py` (the
plan located the `synthesis` changes in `graph.py`, but `synthesis` lives in
`specialists.py`).

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Schema — two tables, both dialects | Complete | +2 `FOREIGN_KEYS`, +1 index, no `IDENTITY_PK` entries |
| 2 | `backend/app/similarity.py` | Complete | Deviated — see Deviation 1 (missing-value rule) |
| 3 | Config thresholds | Complete | 4 keys seeded + made admin-editable |
| 4 | Router + schema + registration | Complete | 3 endpoints, all `REVIEW_ROLES` |
| 5 | Chat tools | Complete | `get_similar_parts`, `search_similar_reviews` |
| 6 | Prompt + offline routing | Complete | Hard rule 6 added; `DONT_KNOW` untouched |
| 7 | Triage integration | Complete | Deviated — `synthesis` is in `specialists.py`, not `graph.py` |
| 8 | `backend/tests/test_similarity.py` | Complete | 26 tests |
| 9 | Frontend types | Complete | 3 interfaces |
| 10 | Item detail page | Complete | Deviated — `Banner` needed a `warning` kind |
| 11 | Batch page control | Complete | Empty-pool message included |
| 12 | Schema guard tests | Complete | 12→14 tables; `pk_covered` +2 |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static analysis | Pass | `tsc --noEmit` clean; backend import-check clean (no Python type checker configured in this repo) |
| Unit tests | Pass | 199 passed, 0 failed — 26 new, 173 pre-existing, no regressions |
| Build | Pass | `next build` compiled, TypeScript checked, 6 static pages generated |
| Integration | Pass | Live uvicorn on :8033 — health, upload, score, similarity (empty pool and populated pool), review, chat tool, triage |
| Edge cases | Pass | Empty pool, 1-peer pool, unscored batch, unknown batch, VIEWER 403, missing item 404, resume/refresh, both-sides-missing features |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `backend/app/similarity.py` | CREATED | +500 |
| `backend/tests/test_similarity.py` | CREATED | +465 |
| `backend/app/routers/similarity.py` | CREATED | +74 |
| `backend/app/db.py` | UPDATED | +126 |
| `backend/app/agent/tools.py` | UPDATED | +105 |
| `frontend/src/app/batches/[id]/items/[itemId]/page.tsx` | UPDATED | +105 / -5 |
| `frontend/src/lib/types.ts` | UPDATED | +57 |
| `frontend/src/app/batches/[id]/page.tsx` | UPDATED | +46 / -3 |
| `backend/app/agent/graph.py` | UPDATED | +26 / -7 |
| `backend/app/routers/rules_config.py` | UPDATED | +18 / -4 |
| `backend/app/agent/specialists.py` | UPDATED | +16 / -4 |
| `backend/app/llm/echo.py` | UPDATED | +13 |
| `frontend/src/components/ui.tsx` | UPDATED | +8 / -4 |
| `analysis/engine/rule_config.json` | UPDATED | +6 |
| `backend/app/agent/prompts.py` | UPDATED | +6 |
| `backend/app/schemas.py` | UPDATED | +5 |
| `backend/app/agent/triage.py` | UPDATED | +5 / -2 |
| `backend/tests/test_schema_parity.py` | UPDATED | +5 / -3 |
| `backend/tests/test_foreign_keys.py` | UPDATED | +4 |
| `backend/app/main.py` | UPDATED | +3 / -2 |

`Makefile`, `backend/app/config.py` and `backend/tests/conftest.py` also show as
modified — these are **pre-existing** uncommitted changes carried onto the
branch, not part of this work. `config.py` and `conftest.py` have zero content
diff (line-ending only).

## Deviations from Plan

**1. Missing-value rule in the distance function — corrected during validation.**

*What*: the plan specified "missing on EITHER side scores 1.0". Changed to: one
side missing scores 1.0, both sides missing scores 0.0.

*Why*: the original rule made two rows that are both missing a field maximally
dissimilar on it. Since `conftest.py` pins `BOM_ENGINE=rules` and the rule engine
emits no `route`/`consumable`, that added a constant 0.15 to every pair in the
test suite, plus 0.20 for the fixture's blank `sfm_criticality` — a 0.45 floor
against a 0.35 threshold, so no two parts could ever be neighbours. Caught by
`test_run_persists_results_and_neighbours` failing with zero neighbours
persisted. The corrected rule is closer to classic Gower (which drops such pairs
from the denominator entirely) while still penalising the case that actually
matters: a peer we know nothing about is not evidence.

**2. `synthesis` lives in `specialists.py`, not `graph.py`.**

*What*: the plan put the promote-only rule in `graph.py`.

*Why*: plain misattribution in the plan. `graph.py` holds `TriageState`,
`triage_features`, `intake`, `persist` and the graph wiring; the specialist nodes
including `synthesis` are in `specialists.py`. The `intake`/`triage_features`
changes did land in `graph.py` as planned.

**3. `Banner` gained a `warning` kind.**

*What*: unplanned 4-line change to `frontend/src/components/ui.tsx`.

*Why*: `Banner` accepted only `error | info | success`. `NO_RELIABLE_ANALOGUE` is
a caution, not an error, and rendering it as an error would overstate it. The
`var(--warning)` token already existed (used by `RiskChip`/`StatusChip`), so this
completes an existing component rather than introducing a concept.

## Issues Encountered

**1. Zero neighbours ever matched.** Root cause and fix: Deviation 1 above.

**2. Reasons claimed matches on unknown fields.** The live run showed
`"same criticality"` on parts where `sfm_criticality` is blank on both sides.
The distance rule is correct (both-missing is not dissimilar), but narrating it
as a match tells the engineer something false. `similarity_reasons` now skips any
feature not actually known on the target. Covered by
`test_similarity_reasons_skip_features_unknown_on_both_sides`.

**3. Keep-alive floor applied asymmetrically.** The floor clamped
`analogue_max_median` and `analogue_max_p75` but not `analogue_max_p25`, so a
critical part could display "typical range 0–4" — reading as "zero is normal
here". `p25` is now floored too.

**4. Stale server during live verification.** Uvicorn was started without
`--reload`, so the first post-fix smoke run still executed the old module and
appeared unfixed. Restarted and re-verified; the fix was real.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_similarity.py` | 26 | Distance maths (identical / one-sided-missing / both-missing / criticality dominance / ordinal proportionality), weighted percentile, reason redaction and honesty, empty pool, persistence, self-exclusion, the write boundary, sub-threshold NULL ranges, order-multiple rounding, critical keep-alive floor, resume/refresh, RBAC, 400/404 paths, both chat tools, outlier tier block, priority promotion, backward-compatible `features` dict |

Full suite: **199 passed**, up from 173, no regressions.

## Real-Data Observation (not a defect)

`sfm_criticality` is blank on **12,067 of 17,165 rows (70%)** in
`BOM table/BOM REVIEW_Jan'26 .csv` (`L`: 1,570, `M`: 333, `H`: 157, `D`: 3,038).
Criticality carries the second-heaviest weight (0.20) and is meant to stop a
cheap consumable matching a critical insurance spare. On real data it is unknown
for most rows, so those rows match each other freely on that dimension and the
safeguard is weaker than the weighting implies. The `machine_criticality_config`
table (engineer-confirmed, merged by `active_config`) is the intended remedy and
is currently empty. Worth populating before trusting the peer sets on production
data.

`DEMO_TCB_showcase.csv` has it blank on all 20 rows.

## Next Steps

- [ ] Code review via `/code-review`
- [ ] Populate `machine_criticality_config` before trusting peer sets on real data
- [ ] `analysis/s17_similarity_backtest.py` — temporally-split evaluation, the gate
      before this layer is ever allowed to influence a number rather than a priority
- [ ] Commit (nothing has been committed; all work is uncommitted on
      `feat/knn-advisory-similarity-layer`)
