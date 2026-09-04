# Implementation Report: Chatbot LangGraph Tool Router

## Summary

`/chat` now runs a LangGraph `StateGraph` in front of the bounded tool loop. A
`classify` node picks one of six branches, each branch offers the model only its
own 3–6 tools, and the chatbot can reach four capabilities that were previously
console-button-only: assist verdicts, running the assist chain, batch-level
similarity outliers, and dormant-rule coverage. Review-queue actions arrive as a
**staged action card** — the agent builds it, the engineer presses it, and the
write still originates from a human click against the existing RBAC-guarded
endpoint.

The write boundary is unchanged: `review_history` is still unreachable from the
agent, and no existing test was weakened to make room for the new capability.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Large | Large — accurate |
| Confidence | 8/10 | Justified; 3 issues, all caught by the suite, all fixed |
| Files Changed | 14 | 16 (13 updated, 3 created) |
| Tool count | 21 | 21 |
| Backend tests | — | 356 passing (16 new) |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Restore the langgraph pin | Complete | `langgraph==1.2.9`; already present in `.venv`, no install needed |
| 2 | Add the six new tools | Complete | Function-local imports avoided the predicted circular import |
| 3 | Register tools, group by intent | Complete | `INTENT_TOOLS` + `WRITE_INTENTS`; 21 tools, all reachable |
| 4 | Per-branch prompts | Complete | `SYSTEM` and `DONT_KNOW` left byte-identical |
| 5 | Build the graph | Complete | Deviated — explicit path map on the conditional edge |
| 6 | Teach EchoProvider the branches | Complete | Deviated — `ACTION_RE` name collision found and renamed |
| 7 | Wire the router to the graph | Complete | Deviated — graph now owns the `request` event |
| 8 | Schema for the action card | Complete | `StagedAction` |
| 9 | Frontend types | Complete | `ChatIntent`, `StagedAction`, 2 stream events |
| 10 | The action card component | Complete | Presentational; calls nothing |
| 11 | Render card + classify step | Complete | Added CSS (not in the plan's file list) |
| 12 | Tests | Complete | Reordered before frontend; 16 new tests |
| 13 | Amend `loop.py` docstring | Complete | Original argument preserved |
| 14 | Update the codebase map | Complete | New §20.2; later subsections renumbered |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | Pass | `npx tsc --noEmit` clean; `compileall` clean |
| Lint | Pass | `npm run lint` (eslint) clean |
| Unit Tests | Pass | 356 passed / 0 failed, 16 new |
| Build | Pass | `npm run build` — 8 routes generated |
| Integration | Pass | Graph exercised end-to-end through `POST /chat` and `/chat/stream` in tests |
| Edge Cases | Pass | Empty assist table, zero dormant rows, bad verdict, already-confirmed pending, viewer downgrade, nonsense classification |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `backend/app/agent/graph.py` | CREATED | +271 |
| `backend/tests/test_chat_graph.py` | CREATED | +367 |
| `frontend/src/components/ActionCard.tsx` | CREATED | +72 |
| `backend/app/agent/tools.py` | UPDATED | +387 |
| `backend/app/llm/echo.py` | UPDATED | +156 |
| `backend/app/agent/prompts.py` | UPDATED | +138 |
| `frontend/src/app/chat/page.tsx` | UPDATED | +51 / -6 |
| `docs/CURRENT_CODEBASE_END_TO_END.md` | UPDATED | +49 / -3 |
| `frontend/src/app/globals.css` | UPDATED | +44 |
| `backend/app/routers/chat.py` | UPDATED | +27 / -5 |
| `frontend/src/lib/types.ts` | UPDATED | +27 |
| `backend/app/schemas.py` | UPDATED | +22 |
| `backend/app/agent/loop.py` | UPDATED | +13 |
| `backend/tests/test_chat_routing.py` | UPDATED | +8 |
| `backend/tests/test_agent_boundary.py` | UPDATED | +6 |
| `backend/requirements.txt` | UPDATED | +5 |

## Deviations from Plan

1. **Explicit path map on the conditional edge** (Task 5). The plan mirrored the
   deleted `graph.py`, which called `add_conditional_edges(source, router)` with
   no mapping. Compiled that way the branch targets are not part of the static
   graph — `get_graph()` rendered `classify → __end__` and dropped every branch
   edge. Added `{name: name for name in INTENTS}`, which makes the shape
   assertable and turns a branch name that is not a node into an error at the
   edge instead of a silent route.

2. **The graph owns the `request` event** (Task 7). `run_agent` opens with
   `request`, which was correct when it was the entry point. With the graph in
   front, `classify` fired first and `test_chat_stream_exposes_grounded_progress`
   failed on `kinds[0] == "request"` — and a console drawing "Routing the
   question" above "Request accepted" describes the wrong order of events.
   `run_chat` now emits `request` before invoking, and `_branch_sink` filters the
   loop's duplicate. The existing test passes unmodified.

3. **The `unknown` node emits `fallback`** (Task 5). Off-domain questions used to
   reach `run_agent`, which emitted `fallback` with a reason. The `unknown`
   branch answers without calling the loop, so that event disappeared and
   `test_chat_stream_reports_fallback_reason` failed. The node now emits the same
   `fallback` and `agent_complete` events. Fixed in the graph, not the test — the
   console must not lose the reason it says "I don't know".

4. **Vague writes route to `propose`, not `unknown`** (Task 6). "increase item
   100005 a little" carries no quantity. Routing it to `unknown` returns
   "I don't know"; routing it to `propose` puts it in front of the one prompt
   that says *"if they were vague, ask for the exact value"*. Observable CI
   behaviour is identical (the stub stages nothing without a number), and the
   behaviour with a real model is better.

5. **`globals.css` was not in the plan's file list** (Task 11). The plan had
   `ActionCard.tsx` mirror sibling components' class names but never added the
   classes. 44 lines added, reusing the existing token vocabulary and the
   warning tone `.staged-notice` already uses for "nothing recorded yet".

6. **Property 7 asserted in `test_chat_graph.py`, not duplicated into
   `test_agent_boundary.py`** (Task 12). The plan asked for the property in both.
   It needs the graph's fixtures, and a second copy would drift. The boundary
   file's docstring lists property 7 and names where it lives.

7. **Task order: tests before frontend.** The plan sequenced 9–11 (frontend)
   before 12 (tests). The two gated tools had no execution coverage at that
   point, so the tests ran first. Content unchanged.

8. **Graph-shape validation command replaced.** The plan's
   `CHAT_GRAPH.get_graph().draw_ascii()` raises `ImportError: Install grandalf to
   draw graphs`. Not adding a dependency to render a diagram — the shape is
   asserted by listing `g.nodes` / `g.edges` instead.

## Issues Encountered

1. **`ACTION_RE` name collision.** `echo.py:46` already defined `ACTION_RE` for
   the Increase/Maintain/Decrease queue filter; the new review-action regex
   shadowed it, and `test_route_extracts_filters_and_respects_precedence` caught
   it immediately ("which items are set to decrease" lost its `action` filter).
   Renamed the new one to `REVIEW_ACTION_RE`.

2. **Circular import risk, as predicted.** `assist/chain.py` imports from
   `agent.tools`, so `run_assist` and `get_dormant_coverage` import
   `assist.chain`, `similarity`, `dormant_rules` and `part_category` inside the
   function bodies, mirroring `recall_context`. No cycle.

3. **A malformed f-string** in the first `_render` case (a string literal split
   across an interpolation). Caught on the first import.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_chat_graph.py` | 16 | Intent-group integrity, per-branch tool subsetting, assist/advisory routing, nonsense-classification fallback, `run_assist` cost gate (gated + confirmed + role refusal), action-card boundary (`review_history` stays 0, pending stays `pending`), already-confirmed refusal, viewer downgrade, empty assist table, zero dormant rows, bad verdict, stream `classify` event |
| `backend/tests/test_chat_routing.py` | +5 cases | The five new tools routed through the real `/chat` endpoint |

## Manual Validation Not Yet Done

The plan's browser checklist has not been run — `make dev` and the three UX
flows (assist queue, cost gate, action card) still need a human at the console.
Everything asserted above comes from the automated suite and the production
build.

## Code Review (`/code-review high`, 4 backend files)

The first run over the whole working tree stalled at 600s with no output. Re-run
scoped to `graph.py`, `tools.py`, `echo.py`, `chat.py`; 11 findings, all
verified against the code and all fixed. Suite after fixes: **359 passing**
(3 new regression tests).

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Critical | `staged_action` carried the audit breadcrumb, not the card. `ctx.sources` is the only channel out of a tool that reaches the HTTP response; the real payload was in the return value, which dies in `dispatch`. The console then posted `final_rop: 0, final_min: 0`, and `3 >= 0 >= 0` passes the ordering check — a silent zero into `review_history` and the export. | Append the whole action to `ctx.sources`. Test asserts `proposed_max`, `batch_id`, `pending_id`, `stockroom_id` on the card. |
| 2 | High | The cost gate appended no source, so `run_agent`'s no-source control replaced the answer with `DONT_KNOW`. The engineer never saw the row count and could never reach the confirm path — the feature did not work. | The row count is retrieved data, so it is a source (`assist_gate`). Test asserts the count appears in the answer and `DONT_KNOW` does not. |
| 3 | Medium | `LOOKUP_SYSTEM` was `SYSTEM` verbatim, advertising `get_similar_parts` and `propose_change` — neither in the lookup subset. Since `lookup` is both the fallback intent and the downgrade target, a misrouted question made the model call an unoffered tool → no source → "I don't know". | Lookup gets its own prompt, listing only its tools and telling the engineer how to phrase a change instead. |
| 4 | Medium | `REVIEW_ACTION_RE` matched `cancel`, but kind selection was `"discard" in ql` — "cancel pending 4" staged a **confirm** card. | `cancel`/`throw away`/`drop it` all map to discard. Test covers it. |
| 5 | Medium | Every card rendered Confirm *and* Discard, so a `discard_pending` card put the button that writes to `review_history` one mis-click away. | The button follows `kind`; the caveat text does too. |
| 6 | Medium | `get_dormant_coverage` and `get_similarity_outliers` had no role check, while their REST twins are `require_role(*REVIEW_ROLES)` — chat was a way around the endpoint. | `_require_review_role()` on both, mirroring the endpoint. Test asserts a viewer is refused. |
| 7 | Low/Med | `open_review` cards were inert — `pending_id=None`, both handlers no-op'd, two live buttons doing nothing. | Removed from `ALLOWED_ACTIONS`, the schema and the frontend union. Add it with a navigation handler, not before. |
| 8 | Low | A comment claimed `specs(allow_writes=...)` was a second gate on the gated tools; it only filters `propose_change`. | Comment corrected to name the gates that actually exist. |
| 9 | Low | `_history` reaches only `classify`, so the docstring's "and its history?" promise was overstated — the branch answers without the referent. | Comment states classification-only and names the follow-up. |
| 10 | Low (security) | `_history` selected on `session_id` alone; every other read in `chat.py` scopes by `session_id AND user`. Another user's turns could be replayed into the classify prompt, and off-box with an external provider. | `AND user=?`, plus a guard for a missing user. Test asserts a foreign user gets `[]`. |
| 11 | Low | The classify call was not counted in `model_calls`; `unknown` turns reported zero despite spending one. | `classify` returns `model_calls: 1`. |

### Pre-existing bug fixed alongside

`confirm()` in `chat/page.tsx` sent `final_rop: p.proposed_rop ?? 0`. For a
proposal that stated only a Max, that recorded **ROP=0/Min=0** rather than
letting the endpoint merge with the staged and current values — and `3 >= 0 >= 0`
passes validation. This is the existing console tray's behaviour, not something
this feature introduced, but the action card reached the same code path. Nulls
are now sent as nulls.

## Next Steps
- [ ] Manual browser pass on `/chat` (assist question, cost gate, action card confirm)
- [ ] Consider threading conversation history into the branch, not just `classify` (finding 9)
- [ ] Create PR via `/prp-pr`
