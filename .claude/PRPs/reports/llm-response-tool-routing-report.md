# Implementation Report: Tune NYRA Response Quality — Tool-Selection Accuracy

## Summary
Fixed NYRA picking the wrong tool (or no tool) for legitimate engineer
questions. Three of ten registered tools — `get_current_values`,
`list_review_queue`, `recall_context` — were unreachable from
`EchoProvider._route`, so every CI run reported "no routing problem" across a
third of the tool surface. Added a golden intent→tool test (RED first),
disambiguated the overlapping `ToolSpec` descriptions, gave `SYSTEM` an explicit
intent→tool table and answer-shape contract, rebuilt the echo route ladder with
fixed precedence, and extended `DONT_KNOW`'s capability list.

Golden routing pass rate: **10/16 → 16/16**. Full suite **99 → 116 passed**,
zero regressions.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium — as scoped |
| Confidence | 8/10 | Justified; no task needed rework |
| Files Changed | 4 changed + 1 created | 3 changed + 1 created (see Deviations) |
| Baseline failures | "roughly 5-7" | 6, exactly the predicted set |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Golden routing test (RED first) | Complete | 6 failed / 10 passed at baseline — matched the prediction exactly |
| 2 | Disambiguate `ToolSpec` descriptions | Complete | 6 descriptions rewritten with NOT-clauses; longest is 39 words (cap was ~40) |
| 3 | `SYSTEM` routing table + answer shape | Complete | Existing hard rules 1-5 untouched and unrenumbered |
| 4 | Reach unreachable tools in `_route` | Complete | All 10 tools now routable; 3 new `_render` branches |
| 5 | Extend `DONT_KNOW` capability list | Complete | First sentence preserved verbatim — no test edit needed |
| 6 | Measure and decide on few-shot | Complete | 16/16 after Tasks 2-5 → **few-shot not added**, as the plan gated it |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `python3 -m compileall -q app/agent app/llm` — clean. No ruff/mypy in requirements, none added. |
| Unit Tests | Pass | 17 new tests (16 parametrized cases + 1 filter/precedence guard) |
| Build | Pass | N/A as a separate step for this Python service; compile + import via TestClient covers it |
| Integration | Pass | End-to-end through the real ASGI app: all 5 After-diagram questions produce the intended answers |
| Edge Cases | Pass | 10 probes run against the ladder; see below |

### Full suite
```
99 passed   (baseline, before any change)
116 passed  (final)
```
`116 = 99 + 16 parametrized cases + 1 guard test`. No pre-existing failures were
inherited — the baseline was fully green, including `test_real_data.py`.

### Edge-case probe results
| Case | Result |
|---|---|
| Empty question | Cannot reach the router — `ChatRequest.question` has `min_length=1` (`schemas.py:33`), so it is a 422 |
| Two intents (`"why 100005 and set its max to 3"`) | `propose_change` deterministically; the `3` is in the engineer's message so `tools.py`'s number guard permits it |
| Filter with no item id (`"list the high risk items"`) | `list_review_queue {"risk_level": "High"}` |
| Lowercase enum (`"...set to decrease"`) | `list_review_queue {"action": "Decrease"}` — title-cased |
| Write verb in a past-tense question (`"change the history for 100005"`) | `get_item_history`, not `propose_change` |
| `"know"` contains `"now"` | `None` — the word-boundary regex holds |
| `"how many high risk items are left"` | `list_review_queue`, no longer swallowed by `batch_summary` |
| Write phrasing as a read-only role | `None` — the `in available` gate holds |
| Recall phrasing | `recall_context {"query": ...}` |
| Off-domain | `None` → `DONT_KNOW` |

### Integration output (real ASGI stack, synth batch)
```
Q: what is item 100005 max right now?
A: Item 100005 current: max 1/rop 0/min 0 (stockroom 24)          sources=1
Q: how many high risk items are left?
A: Review queue for batch 1 (1 items): 100005 pending_review Increase High ($15,000)
Q: show me the review queue
A: Review queue for batch 1 (7 items): 100005 pending_review Increase High ($15,000); ...
Q: what is the capital of France?
A: I don't know — no data source matches that question. ...       sources=0
Q: set item 100005 max to 3
A: Staged a proposed change for item 100005: max -> 3. This is not applied yet — ...
```

## Files Changed

| File | Action | Lines |
|---|---|---|
| `backend/tests/test_chat_routing.py` | CREATED | +115 |
| `backend/app/llm/echo.py` | UPDATED | +78 / -6 |
| `backend/app/agent/tools.py` | UPDATED | +26 / -9 |
| `backend/app/agent/prompts.py` | UPDATED | +21 / -2 |

> `backend/app/routers/export.py` and `backend/requirements.txt` also appear in
> `git diff` — those are **pre-existing uncommitted xlsx-exporter changes** that
> predate this session and are unrelated to this plan. Not touched here.

## Deviations from Plan

1. **`tests/test_agent_boundary.py` was not modified.** The plan listed it as a
   conditional UPDATE ("only if the `DONT_KNOW` substring assertion breaks").
   Preserving the verbatim first sentence of `DONT_KNOW` kept both assertions
   (`test_agent_boundary.py:175`, `test_api_flow.py:136`) green, so the
   condition never triggered. 4 files changed instead of 5 — the better outcome.

2. **Added one non-parametrized guard test beyond the plan's Testing Strategy.**
   `test_route_extracts_filters_and_respects_precedence` calls `_route` directly.
   WHY: the parametrized `CASES` assert only *which tool* ran, so the extracted
   `risk_level` / `action` filter arguments and the two new precedence rules were
   unguarded — a regression that dropped the filters would have left all 16 cases
   green. The filter args are not visible in the rendered answer, so this cannot
   be asserted through `/chat`.

3. **Added a `PAST_WORDS` guard not spelled out in the plan's regex list.** The
   plan's Task 4 step 1 required "no history/notes keyword" on the
   `propose_change` branch; implemented as a shared `PAST_WORDS` tuple so the
   same word list drives the guard and stays next to the branches that consume
   it.

4. **`exposure_usd or 0` in the `list_review_queue` renderer.** Not in the plan's
   snippet. `recommendation_result.exposure_usd` is nullable, and
   `f"${None:,.0f}"` raises rather than degrading — a crash in a render path, not
   a formatting nit.

5. **Integration validated through `TestClient` rather than a live `uvicorn`.**
   The plan's Browser Validation section spins a server and curls it. The
   TestClient path exercises the identical ASGI app, router, agent loop, and
   SQLite DB, and needed no port or scored-batch setup outside the fixture. The
   live-server and `LLM_BASE_URL` manual checks in the plan remain **not run** —
   see Next Steps.

## Issues Encountered

1. **`"know"` contains `"now"`.** The plan's `CURRENT_RE` word list includes
   `now`; a naive `"now" in ql` substring test fires on every question
   containing "know" — including NYRA's own refusal text — and would have routed
   them to `get_current_values`. Resolved with a word-bounded regex
   (`\bnow\b` fails on "know" because the preceding `k` is a word char) and a
   comment naming the trap, plus a permanent assertion.

2. **Precedence change is real but unobserved by existing tests.** Moving
   `batch_summary` last changes behaviour for `how many` / `status` phrasings.
   Verified safe by grepping every `"question":` literal in the suite first — no
   existing test used those words — rather than by assuming.

3. **The `GateGuard` fact-forcing hook** required a facts preamble before each
   Bash/Write/Edit. Satisfied per file; no functional impact.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_chat_routing.py` | 16 parametrized + 1 guard | Intent→tool for all 10 registry tools, 2 negative cases (vague quantity, off-domain), filter-argument extraction, `batch_summary` vs `list_review_queue` precedence, past-tense vs write-verb precedence, word-boundary regression, role gating |

## Known Limitation (by design)

`EchoProvider` never reads `SYSTEM` (`echo.py:34-51` inspects only the last user
message and the tool results). So Tasks 2-3 — the tool descriptions and the
routing table, i.e. everything that steers the *real* provider — are structurally
unmeasurable in CI, and Task 4 is the part that is measurable. This is documented
in the test module's docstring. Do not "fix" it by teaching `EchoProvider` to
parse `SYSTEM`; that makes the stub a model and the test a tautology. Scoring the
prompt work requires the manual `LLM_BASE_URL` checks below.

## Post-Review Fixes (`/code-review medium`)

The review found 8 findings; 5 were routing defects in this change and are
**fixed**. All were reproduced before fixing and re-verified after, and each has
a permanent assertion in `test_route_extracts_filters_and_respects_precedence`.

| # | Severity | Defect | Fix |
|---|---|---|---|
| 1 | **Major** | `"set" in "setting"` — the substring `WRITE_VERBS` test turned a read into a write, and `QTY_RE`'s `max <digits>` alternative then captured the *item id* as the quantity. `"what is the current max setting for item 100005"` staged `pending_change(100005, proposed_max=100005)`. | `WRITE_VERBS` tuple → word-bounded `WRITE_RE`; `propose_change` additionally refuses a quantity equal to the matched item id |
| 3 | Medium | `LIST_RE` owns `show`/`list`, so `"show me the thresholds"` and `"list the active rules"` routed to `list_review_queue`; `"show the batch summary"` did too | `RULES_RE` branch hoisted above the list branch; list branch now excludes `SUMMARY_RE` |
| 4 | Medium | The `PAST_WORDS` guard dropped legitimate write intent — `"set item 100005 max to 3 (note: bench spare)"` routed to `get_item_notes` and staged nothing, though a rationale routinely contains "note" | Guard **deleted**. An explicit quantity is the real discriminator: `"change the history for 100005"` has none, so it reaches `get_item_history` without the guard |
| 5 | Low | `recall_context`'s `not item` guard made it unreachable for any question naming a part | Guard removed — recall takes free text and is item-agnostic |
| 6 | Low | `SYSTEM` promises `"what should I look at first" -> top_exposure`, but `RANK_RE` had no `first`, so it routed to `list_review_queue` | `first` added to `RANK_RE`; the documented contract is now asserted |

Finding 1 was **pre-existing** — both the substring test and `QTY_RE` predate this
plan — but it defeated the new `get_current_values` branch and is the exact
failure mode the work was commissioned to fix, so it was in scope. DB-level
confirmation after the fix:

```
Q: what is the current max setting for item 100005
A: Item 100005 current: max 1/rop 0/min 0 (stockroom 24)
pending_change rows: []          # was: [('100005', 100005, 'pending')]
```

Net effect on the ladder: one guard added (`WRITE_RE`, item-id-as-quantity), two
guards deleted (`PAST_WORDS`, `recall_context`'s `not item`) — both were doing
harm, not work.

Suite after fixes: **116 passed**, unchanged count (the new assertions live
inside the existing guard test).

### Findings in the adjacent xlsx-exporter work — also fixed

Findings 2, 7 and 8 landed in the pre-existing uncommitted xlsx-exporter work
rather than in this plan's scope. Fixed on request ("fix all").

| # | Severity | Defect | Fix |
|---|---|---|---|
| 2 | Medium | `frontend/.../api/backend/[...path]/route.ts` forwarded a literal allowlist of response headers, never extended for `X-Rows-Updated` / `X-Rows-Acknowledged`, so `page.tsx:98` rendered "null rows carry new values, null reviewed with no change" on **every** workbook download | Forward by prefix — `content-type`, `content-disposition`, and any `x-*`. Kills the bug class instead of the instance: a new backend count header no longer needs this file edited to survive the hop |
| 7 | Low | `export_xlsx.py` blanked `justification`/`comments` on *every* row before the reviewed branch, discarding WINGS-supplied text on pending, auto-cleared and quarantined rows — contradicting the docstring's "values exactly as they were read" | `FILLED_COLUMNS` split: `STALE_COLUMNS` (the six approval signals) still cleared unconditionally; `REVIEW_TEXT_COLUMNS` written only where a review produced text |
| 8 | Low, security-adjacent | Reviewer text written to cells without formula-prefix neutralisation — a value starting with `=`, `+`, `-`, `@`, tab or CR becomes a live formula in a workbook that is emailed and re-imported | `_safe_text()` prefixes Excel's own `'` text marker. Applied **only** to cells this module writes (`justification`, `comments`, `modified_user`, and the status sheet's `reviewer` / `senior_approved_by` / `note`) — payload passthrough is left as read, since prefixing WINGS' own data would corrupt the round trip and `"-5"` is legitimate there |

Both backend fixes have tests: `test_input_commentary_survives_on_rows_nobody_reviewed`
(asserts the input text survives *and* that the stale-approval control was not
relaxed) and `test_reviewer_text_cannot_become_a_live_formula`.

Frontend verification: `npx tsc --noEmit` clean; `npm run lint` shows 7
pre-existing `react-hooks/set-state-in-effect` errors, none in `route.ts` and all
in unchanged regions. The frontend has no test runner (`scripts` is
dev/build/start/lint only), so rather than introduce one for a four-line header
filter, the predicate was executed directly against the header set `export.py`
emits — both previously-dropped counts now survive, the three original ones still
do, and `server`/`date` are still dropped.

Backend suite after all fixes: **118 passed** (116 + 2 new xlsx tests).

## Next Steps
- [ ] **Manual validation against the real endpoint** (the only check that
      exercises Tasks 2-3): with `LLM_BASE_URL` set, ask the six "Choosing a
      tool" phrasings and confirm each reaches the named tool via
      `conversation_turn.tool_calls`
- [ ] Confirm the frontend chat view renders the new multi-item
      `list_review_queue` answers without layout breakage
- [ ] Code review via `/code-review`
- [ ] Commit — note the working tree also carries unrelated pre-existing
      xlsx-exporter changes; stage selectively
