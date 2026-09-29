# Chat fallback investigation and fixes

## Confirmed incident

Saved conversation turn 37, workspace 13 (`hist-bom_review_jan_26`),
2026-09-15 08:45:52 UTC:

> show me what the item that catogorise in cable

The audit records `intent=lookup`, `tools=[]`, `answered=false`.
The configured provider was `nyra`, model `qwen3.8:latest`. Replaying the
lookup selection with both the original wording and corrected spelling
produced no tool call. The model correctly identified that its queue tool
had no category filter. The loop discarded that useful explanation because
there were no sources and substituted the canonical fallback.

Workspace 13 contained 2,780 uploaded rows. Confirmed category rules identified
144 raw cable rows, including one quarantined row. The new search follows the
review queue's exclusion of quarantined data and returns **143 eligible cable
rows**. Counts refer to item/stockroom rows, not distinct item IDs.

## Failure matrix

| Case | Earlier failure | Implemented behavior |
| --- | --- | --- |
| Category membership, including the original typo | No matching tool/filter | `search_items(category=...)`; queue supports the same filter |
| Description without an item ID | ID-based readers could not resolve the part | Search descriptions, then retrieve the selected recommendation |
| Category query routed to settings or advisory | Relevant retrieval tool was absent | Item discovery is available in all working branches; prompt distinguishes category membership from rule configuration |
| BOM request classified as unknown | No branch/tool attempt | Conservative BOM vocabulary check recovers into the lookup branch |
| Quoted classifier output | Valid labels were not recognized | Normalize surrounding quotes/code delimiters |
| Model answers before using a tool | Text immediately became the canned fallback | One bounded recovery attempt with read tools only |
| Uncovered dormant item list | Coverage tool returned only aggregate metrics | Queue filters individual rows using the same confirmed-rule resolution and applicability functions |
| No peer/category cache | Category unavailable despite usable descriptions | Evaluate current confirmed category rules during retrieval |
| Missing workspace, nonexistent workspace, unscored workspace | Empty data or confusing zero-result output | Explain the missing prerequisite; description/category/current-value reads work before scoring |
| Brief for a draft workspace | Review exposure was unavailable | Brief retrieves workspace status and skips exposure until scoring |
| Item in multiple stockrooms | Ambiguity clarification was discarded | Preserve a code-generated stockroom question, honoring redaction |
| Leading-zero IDs and multiple stockrooms | Discovery lacked full row identity | Keep string IDs; join and sort by the complete item/stockroom key |
| Unknown category or invalid filter | Unsupported request or silently ignored filter | Reject explicitly; return available categories where applicable |
| No matches | Generic fallback obscured the successful empty search | Grounded zero count with exact search scope |
| Filtered count beyond the page limit | Page length was presented as the count | Separate total count, displayed count, offset, limit, and more-results flag |
| Page beyond the end | Empty page could look like no matching items | Preserve total count and explain the empty page |
| `%`, `_`, SQL-like search text | Wildcards could unintentionally broaden a search | Treat description search as a literal substring; use parameterized SQL |
| Missing current quantities | Blank values could become zero | Return unknown quantities as null |
| Malformed JSON tool arguments | Provider replaced malformed arguments with `{}` | Preserve invalidity and reject; never silently remove filters |
| Invalid model-supplied workspace ID | Falsy IDs could select the current workspace | Reject booleans, non-integers and nonpositive IDs |
| Tool unavailable in the selected branch | No-source fallback masked routing rejection | Explicit invalid-tool outcome, without execution |
| Database read failure | Could be confused with empty data | Distinct data-error outcome; discard partial source markers and recover read transactions |
| Provider failure before retrieval | Request failure or no-data response | Explain model unavailability without asserting anything about records |
| Empty/failed narration after retrieval | Useful records were lost | Render the retrieved records deterministically |
| Failed narration after a staged ROP/Min proposal | Generic fallback or incomplete acknowledgement | Render the exact staged fields and pending proposal ID; keep the human confirmation requirement |
| Many tools requested in one response | Parallel requests could exceed the intended cap | Execute at most five tools per turn |
| Vague quantity / missing item / unsupported spending | Useful clarification was discarded | Code-owned `clarify_request` replies, with no invented quantities or substitute spending measures |
| Reloaded conversation | Fallback flag and reason disappeared | Restore outcome metadata from the existing audit record and retain tool status/summary |

## Design boundaries

- Factual model answers still require retrieved sources. An arbitrary model
  assertion without evidence never becomes a user-facing factual answer.
- Operational replies are generated from fixed templates or actual tool
  outcomes. They do not grant the model permission to invent record facts.
- Recovery never offers proposal writes or analysis jobs. Role checks and
  human confirmation remain in force.
- Searches are scoped to one workspace. Current workspace context takes
  precedence over older conversation context; an explicit workspace in the
  current question can be passed to a tool.
- Actual purchasing expenditure remains unsupported because these chat tools
  do not retrieve purchasing transactions. Inventory exposure is not substituted.
- Literal description search and confirmed category membership are separate
  filters. This is not unrestricted SQL or a general semantic search engine.
- True off-domain questions and repeated failure to select a tool can still
  produce the generic fallback. The changes reduce false fallbacks and expose
  specific failure reasons; they do not guarantee every phrasing will route.

## Validation

- Offline regressions: `backend/tests/test_chat_recovery.py`, covering retrieval,
  ambiguity, invalid arguments, provider/data failures, source grounding, tool
  budget, permissions, and history/stream persistence.
- Final full backend suite: **540 passed**, including **46** new discovery and
  recovery regression cases. Focused chat suites also passed.
- Eleven read-only live-model cases passed against workspace 13. The original
  cable request, category count, description search, uncovered dormant list,
  missing item, unsupported spending, empty search, high-risk queue, active
  route, unknown category, and tubing explanation all reached the intended
  records or specific operational response.
- Evidence: `chat_retrieval_live_check.json` and
  `chat_retrieval_live_edge_check.json`. These retain tool/outcome metadata;
  raw answer tables are omitted.
- Browser regression: `frontend/scripts/check_chat_outcomes.mjs` verifies saved
  statuses, reloads, and streamed clarification using isolated API fixtures.
- Frontend TypeScript and ESLint checks passed.

Run the live checks explicitly, using the configured local provider/database:

```powershell
cd backend
..\.venv-ollama\Scripts\python.exe scripts/check_chat_retrieval.py --batch-id 13
```

The live checker allows only SELECT statements and does not save diagnostic
conversation turns, proposals, or decisions. Offline tests use isolated SQLite
fixtures. No database migration is required.

## Deployment

On 2026-09-15 the updated source was staged through the existing application
deployment workflow. Both Docker images were rebuilt and the backend (8011)
and frontend (3010) containers recreated successfully; both passed health checks.

A read-only smoke check inside the running backend repeated the original cable
question with the configured model. It called `search_items`, retrieved the
143 eligible cable rows, and returned `response_status=answered`, `fallback=false`.
The frontend login and session endpoints also returned their expected statuses.

The preceding images remain tagged `before-chat-fix-20260915` for rollback.
