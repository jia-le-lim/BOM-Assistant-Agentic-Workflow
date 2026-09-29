# Implementation Report: Part-Category Similarity Constraint

## Summary

Peer similarity now resolves a **part category** from `item_desc` via an
engineer-owned regex lexicon and uses it as a **hard eligibility filter**: a part
of a different *known* category is never retrieved as a peer. Uncategorised parts
are treated as unknown — never blocked, never narrated as a match.

Because the category is a constraint rather than a distance dimension,
`FEATURE_WEIGHTS` is untouched and still sums to 1.0. Nothing was re-tuned.

Verified on the live Supabase project: **0 cross-category peers** across 1,376
peer relationships, checked against each peer's own description.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium — accurate |
| Confidence | 9/10 | Held. One production-only bug, caught by live integration |
| Files Changed | 11 (2 create, 9 update) | 13 (2 create, 11 update) |
| New dependencies | 0 | 0 |
| Lexicon coverage | ~76% | 68.2% on batch 7 (pool 76%) |

Two files beyond the plan: `backend/app/rest_conn.py` and
`backend/tests/test_rest_transport.py`, both from Issue 1 below.

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | `backend/app/part_category.py` | Complete | 23 seeded rules, `load_rules` skips broken/overlong patterns |
| 2 | Schema — table, seed, migration | Complete | Deviated — seed SQL rewritten, see Deviation 1 |
| 3 | `similarity.py` — resolve, block, narrate, persist | Complete | Deviated — `_RESULT_INSERT` refactored, see Deviation 2 |
| 4 | Schemas + config endpoints | Complete | 4 endpoints; `{pattern:path}` for regex metacharacters |
| 5 | Tests | Complete | 22 new in `test_part_category.py`, 7 new in `test_similarity.py` |
| 6 | Frontend | Complete | Config lexicon editor, item-page restriction header, batch toast |
| 7 | Schema guard test | Complete | 14 → 15 tables; `test_foreign_keys.py` untouched as predicted |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static analysis | Pass | `tsc --noEmit` and `eslint` clean; backend import-check clean |
| Unit tests | Pass | **235 passed** (was 201) — 34 new, zero regressions |
| Build | Pass | `next build` compiled, 6 static pages |
| Integration | Pass | Live Supabase: migration + seed applied, 23 rules confirmed, block verified |
| Edge cases | Pass | Unconfirmed rule inert, broken regex skipped, uncategorised unrestricted |

## Measured Outcome

Against the live project, batch 7 (211 rows), pool 3,005:

| | Before | After |
|---|---|---|
| Distinct similarity buckets (pool) | 637 | 1,241 |
| Largest bucket | 510 | 126 |
| Categorised rows (batch 7) | — | 144 / 211 (68.2%) |
| Outliers | 12 | 18 |
| Cross-category peers | n/a | **0 of 1,376** |

Category distribution on batch 7: `sensor 21, hose_tube 14, filter 14,
assembly 10, valve 9, thermal 8, pcb 8, …`

## Files Changed

| File | Action | Lines |
|---|---|---|
| `backend/tests/test_part_category.py` | CREATED | +215 |
| `backend/app/part_category.py` | CREATED | +130 |
| `frontend/src/app/config/page.tsx` | UPDATED | +115 |
| `backend/tests/test_similarity.py` | UPDATED | +145 |
| `backend/app/routers/rules_config.py` | UPDATED | +110 |
| `backend/app/similarity.py` | UPDATED | +75 / −25 |
| `backend/app/db.py` | UPDATED | +45 |
| `frontend/src/lib/types.ts` | UPDATED | +40 |
| `backend/app/rest_conn.py` | UPDATED | +35 |
| `backend/tests/test_rest_transport.py` | UPDATED | +35 |
| `backend/app/schemas.py` | UPDATED | +22 |
| `frontend/src/app/batches/[id]/items/[itemId]/page.tsx` | UPDATED | +14 |
| `frontend/src/app/batches/[id]/page.tsx` | UPDATED | +3 / −1 |

## Deviations from Plan

**1. Seed SQL rewritten to pass every value as a placeholder.**

*What*: the plan's seed was `VALUES (?,?,?,'seed',1,'seed')` with 3-tuples.
Changed to `VALUES (?,?,?,?,?,?)` with 6-tuples.

*Why*: `RestConn.executemany` **discards the original VALUES clause** and
regenerates it from the row width, so inline literals are dropped. See Issue 1.

**2. `_RESULT_INSERT` refactored to a single column list.**

*What*: the plan warned that adding a column meant updating the column list,
placeholder count and positional tuple together. Rather than doing that
carefully, `_summarise` now returns a **dict** and the tuple is built from a
single `_RESULT_COLUMNS` constant.

*Why*: the plan rated this Medium/High risk. Removing the class of bug is cheaper
than being careful about it every time, and it also let the run-summary counters
read `row["is_outlier"]` instead of `row[8]` — which would otherwise have needed
re-indexing to `row[9]` when the new column landed.

**3. `rest_conn.py` gained a validation guard** (unplanned). See Issue 1.

## Issues Encountered

**1. Production-only startup failure — the seed insert.**

`init_db()` crashed the backend against Supabase with:

```
42601: INSERT has more target columns than expressions
INSERT INTO part_category_config (pattern, category, priority, set_by,
  confirmed, confirmed_by) VALUES ($1,$2,$3),($4,$5,$6),...
```

Root cause: `RestConn.executemany` partitions on `VALUES`, throws the tail away,
and rebuilds groups from `len(rows[0])`. Six target columns, three placeholders,
three-tuples. **SQLite runs the original string and succeeds**, so the entire
offline suite passed while production could not start.

Fixed the seed, and added a guard in `executemany` that compares declared target
columns against the row width and raises a message naming the cause. Three
regression tests, including the exact seed shape.

Worth noting: my first guard compared *placeholders* to row width and did **not**
catch it — 3 placeholders matched 3-tuples. The column count is what must be
compared.

**2. "0 cross-category peers" was initially verified with a vacuous query.**

The first check joined peers to `similarity_result`, which only contains batch 7 —
so almost no historical peer matched and the query could not have found a
violation. Re-verified by computing each peer's category from its own
`bom_rows.payload`: 1,376 rows, 141 same-category, 777 peer-uncategorised,
**0 violations**.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_part_category.py` | 22 | Lexicon compile/ordering/length, specific-beats-generic, abbreviations, empty/None, case-insensitivity, word boundaries, confirmed-only loading, broken and overlong regex skipping, seed, all four endpoints, self-confirm 403, re-propose reset, 422 on bad regex, coverage endpoint |
| `backend/tests/test_similarity.py` (added) | 7 | Cross-category block, uncategorised unrestricted, category leads reasons, no false category claim, unconfirmed rule inert then active once confirmed, summary counters, `FEATURE_WEIGHTS` untouched |
| `backend/tests/test_rest_transport.py` (added) | 3 | Inline-literal rejection, the exact seed shape, target-column counting |

Suite: **235 passed**, up from 201.

## Observation Worth Your Attention

**777 of 1,376 peer relationships (56%) involve a peer that is itself
uncategorised**, and those are never blocked — the policy you chose.

The consequence is structural: an uncategorised part is eligible for *every*
target regardless of category, while a categorised part is eligible only for
matching targets. Uncategorised parts are therefore **over-represented** in peer
sets relative to their share of the pool.

So the block removes wrong-category peers with certainty, but its practical reach
is bounded by lexicon coverage more tightly than the 68–76% figures suggest. The
lever is the lexicon, and `/config/part-categories/coverage` plus the Config page
exist to drive it. Current unmatched samples from batch 7: `ASIO3`,
`NAS 2-BAY 2-LAN DISKSTATION DS720+`, `SHIM 0.01MM`.

## Next Steps

- [ ] Grow the lexicon from the coverage endpoint's unmatched samples
- [ ] Code review via `/code-review`
- [ ] Commit — nothing has been committed; all work is on
      `feat/knn-advisory-similarity-layer`
- [ ] Still open from earlier: `consumption_band` feature; the 18.9 MB-per-run
      pool query
