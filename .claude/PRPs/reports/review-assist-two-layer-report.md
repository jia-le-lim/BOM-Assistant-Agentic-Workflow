# Implementation Report: Two-Layer Review Assist

## Summary

Both layers built and green.

**Layer 1** — dormant rows are sized by an engineer-owned rule table
(`dormant_rule_config`) that overrides the statistical engine. Propose → confirm →
engine-reads, mirroring `part_category_config`. Rules resolve in
`engine_adapter` and travel into the engine through `cfg`, so
`engine_statistical.run()` still holds no database handle. A settings sub-page at
`/config/dormant` maintains them, with a coverage + stock-value-impact panel.

**Layer 2** — the active/dying rows run through a fixed four-step
`langchain-core` chain (`resolve → evidence fan-out → evaluate → narrate →
persist`). The verdict is produced by `assist/rules.evaluate()`, a pure function;
the LLM is handed the decided verdict and writes only the sentence explaining
it. Verdicts persist to `assist_result`, filter `/review/bulk`, and pre-tick the
review queue behind the existing `triage_guarded_assist_enabled` gate.

## ⚠️ The Q2 gate fired — read before enabling any dormant rule

Task 8's backtest (`analysis/s22_dormant_rule_backtest.py`, 8 cycles / 8,343
dormant rows) **contradicts the plan's premise for a blanket rule**:

| Arm | Agreement | Δ agreement | Book value | Δ book |
|---|---|---|---|---|
| `engine_only` (today) | **83.9%** | — | $0 | — |
| `hold_current` (the plan's seeded default) | 79.0% | **−4.9pp** | $1.64M | **+$1.64M** |
| `fixed_1` | 0.1% | −83.8pp | $15.31M | +$15.31M |
| `hold_current_non_critical` | 79.0% | −4.9pp | $1.64M | +$1.64M |

The engine's zero already matches the engineer on 83.9% of dormant rows — the
1,344 overrides are the 16.1% minority, not the norm. A blanket default rule
therefore *loses* agreement and adds $1.64M of inventory. **Targeted item- and
category-scoped rules are the only viable form of this layer.**

Consequences already baked in:
- `DEFAULT_RULES` seeds exactly one row, **unconfirmed**, so nothing moves stock
  until a human confirms it.
- No category rules were invented — the plan explicitly forbade inventing
  unmeasured ones, and the backtest says a blanket one would be wrong anyway.
- The coverage endpoint and the settings page both report the dollar delta beside
  the match rate.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | XL | XL |
| Dependencies added | none | none (`langchain-core` 1.5.6 already present) |
| Files created | 12 | 9 |
| Files updated | 10 | 12 |
| Tests | new suites for both layers | 60 new tests; suite 271 → 333 |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | `dormant_rule_config` + `assist_result` DDL, both dialects | Complete | Deviated — see D1, D2 |
| 2 | `dormant_rules.py` — resolution and seeding | Complete | Deviated — see D3 |
| 3 | Engine reads rules from `cfg` | Complete | Deviated — see D4 |
| 4 | Dormant branch applies the rule | Complete | |
| 5 | CRUD + confirm endpoints | Complete | |
| 6 | `engine_adapter` passes rules into `cfg` | Complete | |
| 7 | Dormant settings sub-page | Complete | Sidebar active-link fix included |
| 8 | `s22_dormant_rule_backtest.py` (Q2 gate) | Complete | **Result contradicts a blanket rule — see above** |
| 9 | `get_agreement_history` tool | Complete | Deviated — see D5 |
| 10 | `assist/rules.py` — deterministic verdict | Complete | |
| 11 | `assist_result` table | Complete | Folded into Task 1 |
| 12 | The chain | Complete | Deviated — see D6 |
| 13 | Determinism and boundary tests | Complete | |
| 14 | Router, redaction, bulk filter | Complete | Deviated — see D7 |
| 15 | Review queue grouping and pre-tick | Complete | |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Dependency check | Pass | `langchain_core 1.5.6`; no umbrella import anywhere |
| Schema parity + FKs | Pass | 29 tests; both DDL blocks, both new tables |
| Engine unchanged by default | Pass | 33 tests; bare `run(df)` byte-identical, asserted in s22 `_selfcheck` |
| New backend tests | Pass | 60 tests across 3 files |
| Full backend suite | Pass | **333 passed** (was 271) |
| Frontend build | Pass | zero type errors |
| Frontend lint | Pass | `eslint --max-warnings=0` clean |
| Dormant backtest | Pass | per-cycle agreement **and** stock-value delta printed |

## Files Changed

| File | Action |
|---|---|
| `backend/app/dormant_rules.py` | CREATED |
| `backend/app/assist/__init__.py` | CREATED |
| `backend/app/assist/rules.py` | CREATED |
| `backend/app/assist/chain.py` | CREATED |
| `backend/app/assist/prompts.py` | CREATED |
| `backend/app/routers/assist.py` | CREATED |
| `frontend/src/app/config/dormant/page.tsx` | CREATED |
| `analysis/s22_dormant_rule_backtest.py` | CREATED |
| `backend/tests/test_dormant_rules.py` | CREATED (25 tests) |
| `backend/tests/test_assist_rules.py` | CREATED (22 tests) |
| `backend/tests/test_assist_chain.py` | CREATED (13 tests) |
| `backend/app/db.py` | UPDATED — 2 tables × 2 dialects, `FOREIGN_KEYS`, `IDENTITY_PK` |
| `backend/app/engine_statistical.py` | UPDATED — `DORMANT_RULES` lever, dormant branch |
| `backend/app/engine_adapter.py` | UPDATED — `part_category` column, rules into `cfg` |
| `backend/app/schemas.py` | UPDATED — `DormantRuleRequest`, assist bulk filters |
| `backend/app/routers/rules_config.py` | UPDATED — 5 dormant-rule endpoints |
| `backend/app/routers/review.py` | UPDATED — `assist_result` join + gate |
| `backend/app/agent/tools.py` | UPDATED — `get_agreement_history` + registration |
| `backend/app/redact.py` | UPDATED — Q4 exception recorded |
| `backend/app/main.py` | UPDATED — assist router |
| `backend/tests/test_schema_parity.py` | UPDATED — table count 15 → 17 |
| `backend/tests/test_foreign_keys.py` | UPDATED — `assist_result` PK-covered |
| `frontend/src/lib/types.ts` | UPDATED — dormant + assist types |
| `frontend/src/app/batches/[id]/page.tsx` | UPDATED — verdict column, narrative, pre-tick |
| `frontend/src/components/Sidebar.tsx` | UPDATED — nav entry + active-link fix |

## Deviations from Plan

**D1 — `assist_result` FK is `ON DELETE CASCADE`, not `RESTRICT`.**
The plan said RESTRICT "matches `triage_result`"; `triage_result` is actually
CASCADE (`db.py` FOREIGN_KEYS), as are both `similarity_*` tables. CASCADE is
correct for derived, reproducible data and it removes the need for the manual
`assist_result` delete inside `score_batch` the plan asked for — a re-score
sheds them automatically. Risk row "`assist_result` FK blocks re-score" is
closed by construction.

**D2 — `test_schema_parity.py` / `test_foreign_keys.py` updated.**
The plan listed neither. `test_expected_table_count` asserts a literal (15 → 17)
and `test_fk_supporting_indexes_are_declared` carries a hardcoded `pk_covered`
set; both had to learn about the new tables or the parity gate fails.

**D3 — `apply()` takes no `moq`.**
The plan's signature was `apply(rule, current_max, moq)` but nothing used `moq`.
Rounding an engineer's stated quantity up to an order multiple would return a
number they did not ask for, so there is no MOQ rounding and the parameter is
gone. Asserted in `test_apply_does_not_round_to_an_order_multiple`.

**D4 — the engine reads `part_category` off the DataFrame, not by categorising in-loop.**
The plan said "read `item_desc` and resolve the part category in the row loop".
Resolving needs the compiled lexicon, which needs a connection, and compiled
regexes are not JSON-serialisable so they cannot ride in `cfg` (which is hashed
into `cfg_hash`). `engine_adapter` attaches a `part_category` column the same way
it attaches `prior_final_*`, and the engine reads it like `crit`/`own`. Both
GOTCHAs the plan cared about are preserved: engine stays DB-free, `cfg` stays
JSON-serialisable. `cfg_hash` now uses `default=str` since rule dicts carry
`updated_at`.

**D5 — `gap_vs_prior_accepted` is computed in `assist/rules`, not in the tool.**
The tool cannot know this month's engine number, so it returns `last_final_max`
and `rules.gap_vs_prior_accepted()` does the division. Same value, and the tool
stays a pure history reader. `n_cycles`, `n_diverge`, `diverge_streak` are on the
tool as specified.

**D6 — `peer_override_rate` is derived from the retrieved neighbours.**
The plan referenced `historical_override_rate` from `get_similar_parts`; that
field does not exist. `similarity_result` stores the peers, not a rate, so the
chain computes the override fraction over the neighbours actually shown — which
is the rate that matches the evidence the reviewer sees.

**D7 — `assist_verdict` and `assist_preselect` are separate filters.**
`assist_verdict` is a plain filter (no gate, selects nothing when unassisted);
`assist_preselect` is the pre-ticking path and goes through
`triage_guarded_assist_enabled`, returning 409 when off. Splitting them keeps
the gate on the only filter that can bulk-accept.

**D8 — `s22` reports four arms, not one candidate.**
An ablation table is the only honest form of a Q2 gate: "here is what each rule
shape would have cost". See the finding above.

## Issues Encountered

- **f-string brace collision.** `POSTGRES_DDL` is an f-string, so a literal
  `DEFAULT '{}'` for `evidence_json` was a `SyntaxError`. Escaped to `'{{}}'` in
  the Postgres block only; the SQLite block is a plain string and keeps `'{}'`.
- **`BOM_ENGINE=rules` is pinned in conftest.** The rule engine emits no `route`,
  so the chain would find zero live rows. `test_assist_chain` sets
  `BOM_ENGINE=statistical` per test, matching `test_bulk_review.py`.
- **Sidebar active-link collision.** `/config/dormant` also lit up `/config` under
  `path.startsWith`. Now the longest matching href wins.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_dormant_rules.py` | 25 | resolution order, criticality filter, decline cases, engine override, confirm gate, coverage endpoint |
| `backend/tests/test_assist_rules.py` | 22 | full verdict truth table both directions, determinism ×100, `AGREE_TOL` imported, garbage-input tolerance |
| `backend/tests/test_assist_chain.py` | 13 | five evidence sources, live-row selection, idempotence, RBAC, LLM-cannot-change-verdict, dead provider, markdown stripping, pre-tick gate |

## Edge Cases Checklist

- [x] Dormant row with no `max_qty` — `hold_current` declines, engine keeps its answer
- [x] Rule table empty — engine byte-identical (asserted in s22 `_selfcheck`)
- [x] Unconfirmed rule ignored — `load_rules` reads `confirmed=1` only
- [x] Batch with zero active/dying rows — chain no-ops cleanly
- [x] LLM provider unreachable — verdict persists, narrative empty
- [x] Re-score a batch with `assist_result` rows — CASCADE, no IntegrityError
- [x] Two stockrooms for one item — `stockroom_id` is in the agreement join
- [x] Cold start (`n_cycles == 0`) — `needs_context`
- [x] `justification` prompt injection — narration prompt names it as data,
      `_plain()` caps it, narrative never feeds a decision
- [ ] 193-row cycle timing — not measured; no production-sized batch available offline

## Acceptance Criteria

- [x] All tasks completed
- [x] All validation commands pass
- [x] `backend/tests` 333 passed (271 baseline + new)
- [x] Bare `engine_statistical.run(df)` byte-identical to pre-change output
- [x] No `langchain` umbrella import — `langchain_core` only
- [x] Verdict deterministic over 100 identical runs
- [x] The LLM cannot change a verdict
- [x] `AGREE_TOL` imported; literal `0.10` absent from `assist/rules.py`
- [x] Dormant backtest reports both agreement and stock-value impact
- [x] Pre-tick gated behind `triage_guarded_assist_enabled`, default off

## Next Steps

- [ ] **Owner decision on Layer 1**: the backtest says a blanket rule is wrong.
      Decide whether to pursue targeted item/category rules or leave dormant
      sizing to the engine. Nothing is confirmed, so today's behaviour is unchanged.
- [ ] Time `POST /assist/run` on a 193-row cycle before enabling in production
- [ ] Consider persisting `gap_vs_prior_accepted` on `recommendation_result` at
      score time — `_benchmark()` already computes it and discards it
- [ ] Code review via `/code-review`
- [ ] Create PR via `/prp-pr`
