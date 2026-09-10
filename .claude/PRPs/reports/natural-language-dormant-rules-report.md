# Implementation Report: Dormant Rules from Natural Language

## Summary

The chatbot can now record a dormant stocking rule from plain language. "Keep
all filter parts at 2" calls `propose_dormant_rule`, which writes
`dormant_rule_config` with `confirmed=0` — invisible to
`dormant_rules.load_rules()` (`WHERE confirmed=1`) and inert until a different
person with approval rights confirms it in the console.

The tool refuses four things the console does not have to: a `default`-scope
rule, a quantity the engineer did not state, an unknown category, and — the one
that matters — re-proposing a rule that is already **confirmed**.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium — accurate |
| Confidence | 8/10 | Justified; two issues, both caught immediately |
| Files Changed | 7 updated | 7 updated (0 created) |
| Registry size | 22 | 22 |
| Backend tests | — | 372 passing (13 new) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | The tool | Complete | All eight validation steps as specified |
| 2 | Register + propose branch | Complete | Deviated — also fixed `READ_ONLY_TOOLS`/`specs()` |
| 3 | Extend the propose prompt | Complete | |
| 4 | Teach EchoProvider | Complete | Deviated — needed a new `RULE_QTY_RE` |
| 5 | Tests | Complete | 12 in `test_dormant_rules.py`, 2 routing cases |
| 6 | Documentation | Complete | Plus the now-false "exactly one write tool" line |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `compileall` clean; `tsc --noEmit` clean |
| Lint | Pass | eslint clean |
| Unit Tests | Pass | 372 passed / 0 failed, 13 new |
| Build | N/A | No frontend change in this feature |
| Integration | Pass | Exercised through `POST /chat` end to end |
| Edge Cases | Pass | All 8 from the plan's checklist |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `backend/tests/test_dormant_rules.py` | UPDATED | +253 |
| `backend/app/agent/tools.py` | UPDATED | +168 / -3 |
| `backend/app/llm/echo.py` | UPDATED | +63 |
| `docs/CURRENT_CODEBASE_END_TO_END.md` | UPDATED | +28 / -5 |
| `backend/app/agent/prompts.py` | UPDATED | +23 / -1 |
| `backend/tests/test_agent_boundary.py` | UPDATED | +15 / -4 |
| `backend/tests/test_chat_routing.py` | UPDATED | +4 |

## Deviations from Plan

1. **Fixed `READ_ONLY_TOOLS` and `specs()` — a latent hole the plan did not
   anticipate** (Task 2). `READ_ONLY_TOOLS` was `frozenset(REGISTRY) -
   {"propose_change"}` and `specs()` filtered that one literal name. Adding
   `propose_dormant_rule` would have made it *read-only by default*, offered to
   viewers and auditors. Worse, the same hole already applied to `run_assist`
   and `stage_review_action` from the previous feature — both were classified as
   read tools. Replaced with a derived `WRITE_TOOLS` frozenset of all four.
   The role gates on those tools meant the hole was not exploitable in practice,
   but it was one branch-membership mistake away from being so.

2. **`RULE_QTY_RE` added** (Task 4). The plan reused `QTY_RE`, which matches
   `max N`, `to N` and `by N` — but not `at N`. The plan's own worked example,
   "keep all filter parts at 2", routed to nothing. `QTY_RE` is what
   `propose_change` routes on and every staging case in `test_chat_routing` is
   tuned against its shape, so a separate `\b(?:at|of)\s+(\d+)\b` is tried first
   rather than widening it.

3. **`test_tool_specs_exclude_writes_when_disallowed` rewritten** (Task 5). It
   asserted the withheld set was exactly `{"propose_change"}` — true when that
   was the only writer, and the reason deviation 1 went unnoticed. Now asserts
   against `T.WRITE_TOOLS` plus that the set names real tools, so it stays
   honest as the surface grows. This is a strengthening, not a weakening: the
   old assertion could not fail when a new writer was added.

4. **One extra test beyond the plan's ten**:
   `test_chat_refuses_a_quantity_on_a_policy_that_takes_none` — the plan listed
   the behaviour in the edge-case checklist but not in the test table.

5. **Extra doc correction** (Task 6). `CURRENT_CODEBASE_END_TO_END.md` stated
   "There is exactly one agent write tool: `propose_change`", which had been
   false since the router work added `run_assist` and `stage_review_action`.
   Corrected to point at `WRITE_TOOLS` and to describe both gates.

## Issues Encountered

1. **The plan's fixture warning turned out not to apply.** It flagged that
   `part_category_config` might be empty, making every category refusal fire.
   `db.py:1012-1023` seeds the lexicon **confirmed=1** at `init_db()`, so all 23
   default categories including `filter` are live. The existing `BODY` fixture
   already used `"filter"`. No workaround needed.

2. **"keep all filter parts at 2" routed to `None`** on first run — the `QTY_RE`
   gap in deviation 2. Caught by the manual routing check before any test was
   written.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_dormant_rules.py` | 12 | Unconfirmed-and-inert; the confirmed-rule guard (refused, and `replace=True`); invented quantity; default scope; unknown category; item not in batch; `fixed_qty` without a quantity; quantity on a policy that takes none; viewer blocked at both gates; no rescore of a scored batch |
| `backend/tests/test_chat_routing.py` | +2 cases | Rule instruction vs coverage question, which share the word "dormant" |
| `backend/tests/test_agent_boundary.py` | 1 rewritten | Every `WRITE_TOOLS` member withheld from a read-only role |

### The test that matters

`test_chat_cannot_replace_a_confirmed_rule` asserts that after a refused call
the row is **still** `confirmed=1` **and** still carries its original quantity.
Without the guard, the upsert's `ON CONFLICT ... confirmed=0` would silently
un-confirm a live rule, `load_rules()` would stop returning it, and the next
`/run-recommendation` would resize every matching dormant part with nobody
approving it.

## Manual Validation Not Yet Done

The plan's browser checklist. `make dev`, then: record a rule from chat, see it
pending at `/config/dormant`, confirm it as a different user, and check that
re-stating it in chat is now refused. Everything above is from the suite.

## Code Review (`/code-review high`, tools.py + echo.py)

8 findings. Six were in this session's code and are fixed; two are pre-existing
and are reported, not silently changed. Suite after fixes: **374 passing**.

| # | Severity | Finding | Outcome |
|---|---|---|---|
| 6 | **High (mine)** | `KEEP_RE` matches "stock", and it was the dormant branch's only trigger, so "set the stock max for 100005 at 5" — an ordinary `propose_change` — emitted a standing dormant rule that would size that part on every future engine run | Fixed: the branch now requires a dormant signal (`DORMANT_RULE_RE`/`CATEGORY_AT_RE`), matching what `_classify` already required. Regression test added. |
| 3 | Medium | `get_similar_parts` had no role gate while `GET /similarity/{b}/{item}` is `REVIEW_ROLES`. The `advisory` branch is not a `WRITE_INTENT`, so a viewer/auditor was never downgraded out of it and read other engineers' decisions and justifications through /chat | Fixed: `_require_review_role`. Pre-existing tool, but the inconsistency was mine — I gated its two siblings and not it. |
| 5 | Medium (mine) | `run_assist` never committed; the only commit is at end of turn. A provider failure on the summarising pass hit `chat_stream`'s `rollback()` and discarded every `assist_result` row **after** the model calls were billed | Fixed: commits straight after `run_batch`, as `POST /assist/run` does. Test rolls back afterwards and asserts the rows survive. |
| 7 | Low (mine) | `stage_review_action` made three of `confirm_pending`'s four checks, skipping `resolve_rec` — so a card staged against a re-scored batch rendered fine and 400'd on click, which its docstring promises cannot happen | Fixed: the fourth check added. |
| 4 | Medium (mine) | The `run_assist` docstring claimed the gate stops a model that has decided to call it. `confirm` is just an argument the model fills | Docstring corrected to state what the gate actually buys and what it does not. Closing it properly needs session-scoped server state, which `ToolContext` has no session id for — logged as a follow-up rather than faked. |
| 8 | Low (mine) | The `action` branch offers no way to discover a `pending_id`, so "confirm the change I staged for 100005" is unanswerable unless the engineer quotes the id | Not fixed — a new read tool is a feature, not a defect fix. Logged as a follow-up. |
| 1 | High (**pre-existing**) | `get_agreement_history` joins `review_history` without de-duplicating to the latest review per (batch, item, stockroom). `review_history` is append-only and `confirm_pending`/bulk review insert extra rows, so `n_cycles`, `n_diverge` and `diverge_streak` inflate for any re-reviewed item | **Not touched.** From commit `af9b750`, zero hits in this session's diff. It feeds `assist/chain._facts` → `rules.evaluate`, so changing it moves verdicts — out of scope for a dormant-rule feature and needs its own plan. |
| 2 | Medium (**pre-existing**) | Same query's `ORDER BY r.batch_id DESC` has no tiebreaker, so `last_final_max` can come from a superseded review | **Not touched**, same reason. Fix is `, h.reviewed_at DESC, h.review_id DESC` alongside finding 1. |

### The bigger thing the review surfaced

Chasing finding 5 exposed that **assist had never actually assisted a row in any
test**. `conftest` pins `BOM_ENGINE=rules`, that engine leaves `route` empty, so
`live_rows()` returned 0 for the synthetic fixture and
`test_run_assist_runs_only_when_confirmed` was asserting `0 == 0`. A `make_live()`
helper now marks rows active, and both assist tests assert against a non-zero
row count. That was a hole in the previous feature's tests, not this one's.

## Next Steps
- [ ] Manual browser pass on `/chat` → `/config/dormant`
- [ ] Decide on findings 1 & 2 (`get_agreement_history` de-duplication) — real
      bugs in agreement statistics, own plan, verdict-affecting
- [ ] Consider a `list_pending_changes` read for the `action` branch (finding 8)
- [ ] Session-scoped confirmation state for `run_assist` (finding 4)
- [ ] Create PR via `/prp-pr`
