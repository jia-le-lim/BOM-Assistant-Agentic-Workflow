# Plan: Chatbot LangGraph Tool Router

## Summary
The chatbot is the first layer an engineer touches, but today it can only reach 15 read/stage tools — it cannot see assist verdicts, run the assist chain, read batch-level similarity outliers, read dormant-rule coverage, or act on the review queue. Those live behind console buttons (`Run Assist`, the batch page lanes). This plan puts a LangGraph `StateGraph` in front of `/chat` that classifies intent, routes to a branch that offers only that branch's tools, and returns either a grounded answer or a **human-confirmable action card** — so the chatbot becomes the entry point to the whole workflow without the agent ever writing a decision.

## User Story
As a **BOM review engineer**,
I want **to ask the chatbot for assist verdicts, peer outliers, dormant coverage, and review actions in plain language**,
So that **I do not have to know which console page hides which button, and the agent brings the right layer to me**.

## Problem → Solution
**Current state**: `/chat` runs `loop.run_agent()` — a flat bounded tool loop offering all 15 specs on every model call. The assist chain (`assist/chain.py`) only executes when the frontend calls `POST /assist/run` from a button. Similarity outliers, dormant coverage, and the review queue are console-only. A question like *"which items did assist flag for review?"* returns `DONT_KNOW`.

**Desired state**: `/chat` runs `CHAT_GRAPH.invoke()`. A `classify` node picks one of five branches (`lookup | assist | advisory | propose | action`), each branch offers a **3–6 tool subset** to the model, and an `action` branch emits an action card the engineer clicks to confirm. The write boundary in `tests/test_agent_boundary.py` is unchanged: the agent still never touches `review_history`.

## Metadata
- **Complexity**: Large
- **Source PRD**: `docs/PRD_Technical_BOM_Review_Assistant_v3.md` (sections 4.3, 8, 10) — not a phase source; this is a standalone plan
- **PRD Phase**: standalone
- **Estimated Files**: 14 (2 create backend, 6 update backend, 1 create test, 2 update test, 1 create frontend, 2 update frontend)

---

## Decisions Locked Before Implementation

Confirmed with the requester. Do not re-litigate during implementation.

| Decision | Choice | Consequence |
|---|---|---|
| Driver | **Real LangGraph `StateGraph`**, not the flat loop | `langgraph==1.2.9` returns to `backend/requirements.txt`. Already installed in `.venv` (verified by import), so `make install` is not blocking. |
| Tool scope | All four: assist verdict reads, `/assist/run` trigger, similarity + dormant reads, review-queue actions | Six new tools. `run_assist` is the only expensive one. |
| Review-queue actions | **Action card, not direct execution** | The agent emits a card; the engineer clicks; the click calls the *existing* `POST /review/{item}/confirm-pending` under its own RBAC. `review_history` stays untouched by the agent — `test_agent_boundary.py:35` needs **no change**. |
| Multi-turn memory | **Rehydrate from `conversation_turn`**, no LangGraph checkpointer | See "Alternatives Considered". |

### Why a graph beats the flat loop here (state this in the new module docstring)

`loop.py:1-20` argues *against* LangGraph. That argument was about wrapping the **deterministic engine**, and it still holds — nothing in this plan puts a graph around `analysis/engine/engine.py` or around `assist/chain.py`'s fixed four steps.

What changes is the **chat router**, and there the argument inverts:

1. **Tool subsetting.** `loop.run_agent()` offers every spec on every model call (`loop.py:89`, `loop.py:105`). Today that is 15 tools; after this plan it is 21. Routing accuracy degrades with tool-list length, and `prompts.SYSTEM`'s hand-written routing table (`prompts.py:26-38`) is already at its readable limit. A classify node that narrows to 3–6 tools per branch is the fix, and a branch is exactly what `add_conditional_edges` expresses.
2. **Per-branch system prompts.** `assist` questions need different hard rules than `propose` questions. One monolithic `SYSTEM` cannot say both without growing.
3. **Cost gating.** `run_assist` spends one model call per live row. That needs a node that can refuse and ask, not a tool the model may fire mid-sentence.

`run_agent()` is **not** replaced — it already accepts `system_prompt`, `tool_names`, and `max_model_calls` (`loop.py:73-79`). The graph's branch nodes are thin wrappers that call it with a subset. That is why this is a bounded diff and not a rewrite.

---

## UX Design

### Before
```
┌──────────────────────────────────────────────────────────┐
│ Engineer wants an assist verdict                         │
│                                                          │
│  chat: "which items did assist flag?"                    │
│     └─> DONT_KNOW ("no data source matches")             │
│                                                          │
│  Engineer must instead:                                  │
│    1. navigate to /batches/{id}                          │
│    2. find and press [Run Assist]                        │
│    3. read the triage lanes                              │
└──────────────────────────────────────────────────────────┘
```

### After
```
┌──────────────────────────────────────────────────────────┐
│  chat: "which items did assist flag?"                    │
│     classify -> assist branch                            │
│     tool: list_assist_queue(verdict="flag_for_review")   │
│     └─> "14 rows flagged in batch 7. Top by exposure:    │
│          100005 ($42k) ..." + source chips               │
│                                                          │
│  chat: "run assist on batch 7"                           │
│     classify -> assist branch                            │
│     tool: run_assist(batch_id=7)          [confirm=False]│
│     └─> cost gate: "That assists 2,814 live rows and     │
│          spends ~2,814 model calls. Confirm?"            │
│                                                          │
│  chat: "confirm the pending change on 100005"            │
│     classify -> action branch                            │
│     tool: stage_review_action(kind="confirm_pending")    │
│     └─> ACTION CARD (nothing executed):                  │
│         ┌────────────────────────────────────────┐       │
│         │ Confirm pending #12 · item 100005      │       │
│         │ max 3 / rop 1 / min 0                  │       │
│         │ Nothing recorded yet.                  │       │
│         │ Needs senior approval after confirming │       │
│         │        [ Confirm ]   [ Discard ]       │       │
│         └────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Assist verdicts | `/batches/{id}` triage lanes only | Also reachable in chat | Read-only, no model calls |
| Run assist | `[Run Assist]` button only | Also chat, behind a cost gate | Same `run_batch()`, same audit entry |
| Similarity outliers | `/batches/{id}` only | Batch-level list in chat | `get_similar_parts` already covers per-item |
| Dormant coverage | `/config/dormant` only | Chat read | Read-only |
| Confirm pending | Chat tray at page bottom | Also an inline action card in the answer | Same endpoint, same RBAC, same senior-approval path |
| Agent trace | Flat step list | Adds a `classify` step naming the branch | `AgentActivity.tsx` renders it with no change |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/app/agent/loop.py` | 1-195 | `run_agent()` is reused verbatim as the branch executor; its `system_prompt`/`tool_names`/`max_model_calls` params are the seam. Its docstring must be amended, not deleted. |
| P0 | `backend/app/agent/tools.py` | 30-44, 482-680 | `ToolContext`, `ToolError`, `EMPTY`, `REGISTRY` shape, `specs()`, `dispatch()`. All new tools plug in here. |
| P0 | `backend/app/routers/chat.py` | 1-125, 166-253 | The `/chat` and `/chat/stream` contract, `_record_turn`, `_resolve_batch`, the NDJSON worker-thread pattern. |
| P0 | `backend/app/llm/echo.py` | 54-195 | The regex router. **Every new tool needs a route here or it is untestable in CI** — `LLM_BASE_URL` is empty in `conftest.py:21`. |
| P1 | `git show HEAD:backend/app/agent/graph.py` | all | The repo's own proven LangGraph idiom at this exact version: `TypedDict` state, `Annotated[..., operator.add]` reducers, `add_conditional_edges`, `compile(checkpointer=False)`. Mirror it. |
| P1 | `backend/app/assist/chain.py` | 68-72, 226-250 | `LIVE_ROUTES`, `live_rows()`, `run_batch()` — what `run_assist` calls. Do not reimplement. |
| P1 | `backend/app/routers/assist.py` | 22-58 | The peers-first ordering `run_assist` must reproduce, and the audit calls. |
| P1 | `backend/app/routers/review.py` | 221-310 | `confirm-pending` / `discard-pending` request shapes the action card must produce, and the three ownership checks. |
| P1 | `backend/app/agent/prompts.py` | 9-77 | `SYSTEM` routing-table style and the verbatim `DONT_KNOW` string (asserted in tests). |
| P2 | `backend/tests/test_agent_boundary.py` | 1-60 | The six safety properties. Property 1 (`review_history` stays 0) is the one this plan must not break. |
| P2 | `backend/tests/test_chat_routing.py` | 1-90 | The `CASES` table and `tools_used(db_file)` helper — extend, do not replace. |
| P2 | `frontend/src/app/chat/page.tsx` | 66-130, 420-475, 589-621 | `applyTraceEvent`, the `stream()` handler, and the existing `confirm(p)` / `discard(p)` functions the action card reuses. |
| P2 | `frontend/src/lib/types.ts` | 240-386 | `AssistResult`, `ChatStreamEvent` union, `PendingChange`. |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| LangGraph `StateGraph` | In-repo: `git show HEAD:backend/app/agent/graph.py` | No web research needed. The repo shipped a working graph on `langgraph==1.2.9`; copy its import surface (`from langgraph.graph import END, START, StateGraph`) and its `compile(checkpointer=False)` call. |
| Conditional edges | same file: `route_specialists()` + `add_conditional_edges("intake", route_specialists)` | Returning `str` picks one node; returning `list[str]` fans out. This plan needs only the `str` form. |

**External research**: none needed — LangGraph usage is established internally, and every other integration point is an existing module in this repo.

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: backend/app/agent/tools.py:66-86
def get_recommendation(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None,
                       stockroom_id: str | None = None) -> dict:
```
Tool functions: `snake_case`, first arg always `ctx: ToolContext`, `batch_id`/`stockroom_id` optional and last, return a plain `dict`. Registry keys equal function names. Module-level constants `SCREAMING_SNAKE`. Regexes suffixed `_RE`.

### ERROR_HANDLING
```python
# SOURCE: backend/app/agent/tools.py:39-41, 452-456
class ToolError(Exception):
    """Rejected at the tool boundary; surfaced to the model, not raised to HTTP."""

    raise ToolError(f"Item {item_id} is not in scored batch {bid}. The "
                    f"monthly roster rotates, so it may not be in the "
                    f"current extract.")
```
```python
# SOURCE: backend/app/agent/tools.py:654-670
    try:
        return json.dumps(redact_for_prompt(fn(ctx, **args)), default=str)
    except ToolError as e:
        return json.dumps({"error": str(e)})
```
A rejected tool call is a normal outcome returned as `{"error": ...}` for the model to read — never an HTTP exception. Empty results return the `EMPTY` sentinel `{"_empty": True}`, not `None`.

### LOGGING_PATTERN
```python
# SOURCE: backend/app/agent/loop.py:50-52, 111-117
def _emit(sink: AgentEventSink | None, event: dict) -> None:
    if sink is not None:
        sink(event)

        _emit(on_event, {
            "type": "model_start",
            "attempt": model_attempt,
            "phase": phase,
            "provider": provider.name,
            "model": provider.model,
        })
```
No `logging` module anywhere in `app/agent`. Observability is (a) NDJSON events through an optional `on_event` sink and (b) rows in `audit_log` / `conversation_turn`. Follow both.

```python
# SOURCE: backend/app/routers/chat.py:61-70
def _record_turn(conn, result: dict, actor: dict, question: str,
                 batch_id: int | None, endpoint: str = "/chat") -> int:
    turn_id = log_turn(conn, result, actor, question)
    audit(conn, actor, "POST", endpoint, "chat", batch_id or "-",
          {"question": question[:500],
           "answered": bool(result["sources"]),
           "tools": [c["name"] for c in result["tool_calls"]],
           "turn_id": turn_id})
    conn.commit()
    return turn_id
```

### REPOSITORY_PATTERN
```python
# SOURCE: backend/app/agent/tools.py:374-393
def batch_summary(ctx: ToolContext, batch_id: int | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    recs = ctx.conn.execute(
        "SELECT * FROM recommendation_result WHERE batch_id=?", (bid,)).fetchall()
    ...
    ctx.sources.append({"type": "batches", "batch_id": bid})
    return {"batch_id": bid, "label": b["label"], ...}
```
Raw SQL through `ctx.conn.execute(sql, params)` with `?` placeholders (`db._translate` rewrites for Postgres — **never** write `%s`). Every tool that produces grounded data **must** append to `ctx.sources`; `loop.py:170` turns an empty `ctx.sources` into `DONT_KNOW`, so a tool that forgets this silently makes the agent say "I don't know".

### SERVICE_PATTERN
```python
# SOURCE: backend/app/routers/assist.py:41-55
        has_peers = conn.execute(
            "SELECT 1 FROM similarity_result WHERE batch_id=? LIMIT 1",
            (batch_id,)).fetchone() is not None
        similarity = None
        if refresh_peers or not has_peers:
            similarity = run_similarity(conn, batch_id, refresh_peers)
            audit(conn, actor, "POST", "/assist/run", "similarity",
                  batch_id, similarity)
        summary = run_batch(conn, batch_id, actor, active_config(conn))
```
Business logic lives in `app/assist/`, `app/similarity.py`, `app/services.py`. Routers and tools orchestrate and audit; they never recompute.

### GRAPH_PATTERN
```python
# SOURCE: git show HEAD:backend/app/agent/graph.py  (the repo's own LangGraph idiom)
from langgraph.graph import END, START, StateGraph

class TriageState(TypedDict, total=False):
    conn: Any
    actor: dict
    sources: Annotated[list[dict], operator.add]
    llm_calls: Annotated[int, operator.add]

def route_specialists(state: TriageState) -> str | list[str]:
    if state["features"]["demand_only"]:
        return "demand_only"
    return ["history_standard", "demand_standard"]

def build_graph():
    builder = StateGraph(TriageState)
    builder.add_node("intake", intake)
    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", route_specialists)
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=False)

TRIAGE_GRAPH = build_graph()
```
State is a `TypedDict(total=False)` carrying the live `conn`. Accumulating fields use `Annotated[..., operator.add]`. The compiled graph is a module-level singleton. `checkpointer=False` — the repo does not checkpoint graph state.

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_chat_routing.py:41-60
def scored_batch(client, csv_bytes):
    b = upload(client, csv_bytes).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b

def tools_used(db_file) -> list[str]:
    """Which tools the last turn actually ran."""
    conn = sqlite3.connect(db_file)
    try:
        raw = conn.execute("SELECT tool_calls FROM conversation_turn "
                           "ORDER BY turn_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return [c["name"] for c in json.loads(raw[0])] if raw else []
```
```python
# SOURCE: backend/tests/test_triage_graph.py (deleted; recover with git show) — hostile provider
    class UnsafeProvider:
        name = "unsafe"
        model = "unsafe-stub"
        def chat(self, messages, tools):
            return Response(content='{"tier":"clear_candidate",...}')

    monkeypatch.setattr("app.agent.specialists.get_provider",
                        lambda: UnsafeProvider())
```
Fixtures from `conftest.py`: `client`, `synth_csv`, `db_file`, and the role headers `ENG` / `SENIOR` / `ADMIN` / `VIEWER` / `AUDITOR`. Assertions read state back from SQLite, not from mocks. Safety properties get a deliberately hostile provider, never a happy-path mock. Test names state the property, not the function.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/requirements.txt` | UPDATE | Restore `langgraph==1.2.9` under the LLM block (already present in `.venv`) |
| `backend/app/agent/graph.py` | CREATE | The `StateGraph`: classify → branch → synthesize → END |
| `backend/app/agent/prompts.py` | UPDATE | Add `CLASSIFY_SYSTEM` + per-branch system prompts; keep `SYSTEM` and `DONT_KNOW` verbatim |
| `backend/app/agent/tools.py` | UPDATE | Six new tools + registry entries + `INTENT_TOOLS` grouping |
| `backend/app/agent/loop.py` | UPDATE | Docstring amendment only. No behaviour change. |
| `backend/app/llm/echo.py` | UPDATE | Classify branch + regex routes + renderers for the six new tools |
| `backend/app/routers/chat.py` | UPDATE | Invoke the graph; carry `intent` / `staged_action`; narrow the suggestion tool set |
| `backend/app/schemas.py` | UPDATE | `StagedAction` model for the response payload |
| `backend/tests/test_chat_graph.py` | CREATE | Branch routing, tool subsetting, cost gate, action-card boundary |
| `backend/tests/test_chat_routing.py` | UPDATE | Add the five new intents to `CASES` |
| `backend/tests/test_agent_boundary.py` | UPDATE | Add property 7: an action card writes nothing |
| `frontend/src/lib/types.ts` | UPDATE | `StagedAction`, `ChatIntent`, two new `ChatStreamEvent` members |
| `frontend/src/components/ActionCard.tsx` | CREATE | The inline confirm/discard card |
| `frontend/src/app/chat/page.tsx` | UPDATE | Render the card and the classify trace step; reuse existing `confirm()` / `discard()` |

## NOT Building

- **No graph around `assist/chain.py`.** Its four steps stay plain function calls (`chain.py:1-14`). The graph routes *to* the chain, never *inside* it.
- **No graph around the deterministic engine.** `analysis/engine/engine.py` is untouched.
- **No LangGraph checkpointer.** See Alternatives Considered.
- **No resurrection of `specialists.py` / `triage.py` / `triage_result`.** Deleted deliberately; assist replaced them. Do not restore.
- **No new HTTP endpoints.** The action card calls endpoints that already exist.
- **No streaming of `run_assist` per-row progress.** The cost gate returns a summary; per-row progress stays a console concern.
- **No agent-initiated senior approval.** `POST /review/{item}/approve` is not exposed as a tool or a card.
- **No mem0 changes.** `recall_context` stays exactly as it is, in the `lookup` branch only.

---

## Step-by-Step Tasks

### Task 1: Restore the langgraph pin
- **ACTION**: Re-add the dependency removed in the working tree.
- **IMPLEMENT**: In `backend/requirements.txt`, under the `# --- LLM ---` block after `openai==2.50.0`:
  ```
  # Drives the /chat intent router (app/agent/graph.py). The deterministic
  # engine and the assist chain deliberately do NOT use it.
  langgraph==1.2.9
  ```
- **MIRROR**: The block already comments *when* each dependency is needed; match that voice.
- **IMPORTS**: n/a
- **GOTCHA**: `langgraph` is already importable in `.venv` (verified). Do **not** run a bare `pip install langgraph` — that resolves a newer major and the `compile(checkpointer=False)` signature this plan mirrors is version-specific.
- **VALIDATE**: `.venv/Scripts/python.exe -c "from langgraph.graph import END, START, StateGraph; print('ok')"`

### Task 2: Add the six new tools
- **ACTION**: Extend `backend/app/agent/tools.py` with the capabilities the chatbot cannot currently reach.
- **IMPLEMENT**: Read tools go in the read-tools section (before the `# the single write tool` banner at line ~422). `run_assist` and `stage_review_action` go after it, under a new `# ---- gated tools ----` banner.

  1. `get_assist_verdict(ctx, item_id, batch_id=None, stockroom_id=None) -> dict`
     — resolve the row with the existing `_resolve(ctx, bid, item_id, stockroom_id)` helper (`tools.py:50`) so the ambiguous-stockroom message matches every other tool. Then
     `SELECT batch_id, item_id, stockroom_id, verdict, reasons_json, narrative, suggested_max, suggested_rop, suggestion_basis, model_version, assisted_at FROM assist_result WHERE batch_id=? AND item_id=? AND stockroom_id=?`.
     Parse `reasons_json` into `reasons`. Return `EMPTY` when no row. Append `{"type": "assist_result", "batch_id": bid, "item_id": item_id}` to `ctx.sources`. Include `"note": "advisory verdict; it changes no stock level and records no decision"`.
  2. `list_assist_queue(ctx, batch_id=None, verdict=None, limit=10) -> dict`
     — validate `verdict` against `assist.rules.VERDICTS`, raising `ToolError` that names the valid values. Clamp `limit` to 1..25 the way `top_exposure` clamps `n` (`tools.py:326`). Return `{"batch_id", "items", "counts"}` and append one source.
  3. `get_similarity_outliers(ctx, batch_id=None, limit=10) -> dict`
     — `SELECT item_id, stockroom_id, neighbour_count, is_outlier, outlier_score, historical_override_rate, advisory_codes FROM similarity_result WHERE batch_id=? AND is_outlier=1 ORDER BY outlier_score DESC LIMIT ?`. `EMPTY` when none. This is the batch-level companion to the per-item `get_similar_parts` (`tools.py:242`) — do not duplicate that one.
  4. `get_dormant_coverage(ctx, batch_id=None) -> dict`
     — the read half of `routers/rules_config.py:386`. Reuse `dormant_rules.load_rules(conn)` / `dormant_rules.resolve(...)` and `part_category.categorise`; **do not** re-derive the coverage arithmetic. Return `{"batch_id", "dormant_rows", "matched", "uncovered", "engine_usd", "rule_usd"}`.
  5. `run_assist(ctx, batch_id=None, confirm=False, refresh_peers=False) -> dict` — **gated**:
     ```python
     if ctx.actor.get("role") not in REVIEW_ROLES:
         raise ToolError("Running assist needs a review role.")
     rows = live_rows(ctx.conn, bid)
     if not confirm:
         return {"gated": True, "batch_id": bid, "live_rows": len(rows),
                 "estimated_model_calls": len(rows),
                 "note": "not run; ask the engineer to confirm first"}
     ```
     With `confirm=True`: reproduce `routers/assist.py:41-55` exactly — peers first when the batch has none, then `run_batch(conn, batch_id, ctx.actor, active_config(conn))`, then `audit(...)`. Append `{"type": "assist_result", "batch_id": bid, "rows_assisted": n}`.
  6. `stage_review_action(ctx, kind, item_id, pending_id=None, batch_id=None, stockroom_id=None) -> dict` — **stages a card, executes nothing**:
     ```python
     ALLOWED_ACTIONS = ("confirm_pending", "discard_pending", "open_review")
     ```
     Validate `kind` against that tuple (`ToolError` otherwise). For the two `*_pending` kinds, read the row from `pending_change` and reject with `ToolError` when it is missing, not `status='pending'`, or belongs to a different `item_id` — the same three checks `routers/review.py:232-241` makes, so a card can never be built for an action the endpoint would reject. Return `{"staged_action": {...}, "executed": False, "note": "nothing has been recorded; the engineer must press confirm"}` and append `{"type": "staged_action", "kind": kind, "item_id": item_id, "executed": False}` to `ctx.sources`.
- **MIRROR**: REPOSITORY_PATTERN and ERROR_HANDLING above. Copy the `_resolve` + `ctx.sources.append` + `EMPTY` shape from `get_recommendation` (`tools.py:66-86`) line for line.
- **IMPORTS**: module level, add `from ..security import REVIEW_ROLES`. `active_config` is **already** imported at `tools.py:21` — do not add it twice. Everything else is function-local (see GOTCHA).
- **GOTCHA**:
  - **Circular import.** `assist/chain.py:21` imports from `agent.tools`. A module-level `from ..assist.chain import live_rows, run_batch` in `tools.py` creates an import cycle. Put the `assist.chain`, `assist.rules`, `similarity`, `dormant_rules`, `part_category` and `audit` imports **inside** the functions that use them, exactly as `recall_context` does with `from ..memory import search_memory` (`tools.py:414`).
  - Every new read tool **must** append to `ctx.sources` or the whole turn degrades to `DONT_KNOW` (`loop.py:170`).
  - `assist_result.reasons_json` is a JSON string; `routers/assist.py:90` parses it. Do the same — never hand raw JSON text to the model.
- **VALIDATE**: from `backend/`, `../.venv/Scripts/python.exe -c "from app.agent import tools; print(len(tools.REGISTRY))"` prints `21`.

### Task 3: Register the tools and group them by intent
- **ACTION**: Add six `REGISTRY` entries and an `INTENT_TOOLS` mapping in `backend/app/agent/tools.py`.
- **IMPLEMENT**: Registry entries follow the existing `ToolSpec` shape, and descriptions must carry the "Use for … NOT for … that is X" contrast the existing entries use — that contrast is what the routing test measures. Example:
  ```python
      "get_assist_verdict": (ToolSpec(
          "get_assist_verdict",
          "The ADVISORY assist verdict for one item: flag_for_review, "
          "bulk_accept_candidate or needs_context, with its reasons and the "
          "one-sentence narrative. Use for 'what does assist say', 'should I "
          "look at this row'. NOT the engine's recommendation -- that is "
          "get_recommendation.",
          {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH,
                                            "stockroom_id": _STOCK},
           "required": ["item_id"]}), get_assist_verdict),
  ```
  Then below `READ_ONLY_TOOLS` (`tools.py:643`):
  ```python
  # Which tools each graph branch offers. Subsetting is the point of the graph:
  # 21 specs on every call is more than the routing layer can discriminate, and
  # a branch that cannot see propose_change cannot accidentally stage anything.
  INTENT_TOOLS: dict[str, frozenset[str]] = {
      "lookup": frozenset({
          "get_recommendation", "get_current_values", "get_item_history",
          "get_item_notes", "get_agreement_history", "get_triage_context",
          "get_procurement_context", "search_similar_reviews", "top_exposure",
          "list_review_queue", "batch_summary", "explain_rules",
          "recall_context"}),
      "assist": frozenset({
          "get_assist_verdict", "list_assist_queue", "run_assist",
          "batch_summary"}),
      "advisory": frozenset({
          "get_similar_parts", "get_similarity_outliers",
          "get_dormant_coverage", "explain_rules"}),
      "propose": frozenset({"propose_change", "get_current_values",
                            "get_recommendation"}),
      "action": frozenset({"stage_review_action", "get_current_values"}),
      "unknown": frozenset(),
  }

  # Branches a read-only role may never be routed into. security.REVIEW_ROLES is
  # the same gate the equivalent endpoint uses; graph.classify downgrades to
  # `lookup` rather than refusing, so a viewer still gets an answer.
  WRITE_INTENTS = frozenset({"propose", "action"})
  ```
  `assist` is deliberately not in `WRITE_INTENTS` — it also serves pure reads, and `run_assist` carries its own role check and `confirm` gate.
- **MIRROR**: `READ_ONLY_TOOLS = frozenset(REGISTRY) - {"propose_change"}` (`tools.py:643`) — frozensets, derived where possible.
- **IMPORTS**: none new.
- **GOTCHA**: `specs(allow_writes, names)` (`tools.py:646`) filters by name **and** by write permission. The graph passes `names=INTENT_TOOLS[intent]`; `allow_writes` stays driven by the actor's role in `chat.py:99`. Keep both — a role check that lives only in the branch router is a role check an intent misclassification can bypass.
- **VALIDATE**: the `test_every_intent_group_names_real_tools` test from Task 12.

### Task 4: Per-branch prompts
- **ACTION**: Add the classify prompt and the branch prompts to `backend/app/agent/prompts.py`.
- **IMPLEMENT**: Keep `SYSTEM` and `DONT_KNOW` **verbatim** — `DONT_KNOW` is asserted character-for-character in tests (`prompts.py:69`). Add below them:
  ```python
  # The classify node's only job. It names a branch; it never answers.
  CLASSIFY_SYSTEM = """\
  Classify the engineer's message into exactly one branch. Reply with the branch
  name alone, lowercase, nothing else.

    lookup    an item's engine recommendation, current levels, history, notes,
              agreement, the review queue, batch totals, thresholds
    assist    the advisory assist verdict, the assist queue, or running assist
    advisory  peer/similar parts, outliers, dormant-rule coverage
    propose   the engineer states a stock level they want recorded
    action    the engineer wants to confirm, discard or open a review for a
              staged proposal
    unknown   anything else, including anything outside BOM review

  If two branches could fit, prefer the narrower one. Prefer `lookup` over
  `assist` when the question is about what the ENGINE said rather than what
  ASSIST said."""
  ```
  Then `LOOKUP_SYSTEM = SYSTEM` (the existing prompt already *is* the lookup prompt), and four new prompts composed as `SHARED_RULES + branch text`, where `SHARED_RULES` is lifted from the existing `Hard rules` block so rules 1, 3, 4, 5, 6 are stated once:
  - `ASSIST_SYSTEM`: an assist verdict is advisory, it is not a decision and never sets a level; `run_assist` spends one model call per live row, so always report the row count and ask before confirming.
  - `ADVISORY_SYSTEM`: hard rule 6 verbatim ("Peer analogues are advisory evidence, never a recommendation…"), plus "Dormant coverage describes rules an engineer wrote; it is not a recommendation to write more."
  - `PROPOSE_SYSTEM`: hard rules 1, 2, 3 verbatim — this branch is where they bite.
  - `ACTION_SYSTEM`: "You stage an action card. You never execute one. Always end by saying nothing has been recorded and the engineer must press confirm. An override still needs senior approval afterwards."
- **MIRROR**: The existing prompt's shape — `What you do` / `Choosing a tool` / `Answer shape` / `Hard rules` / `Style` — and the module docstring's rule: *"The prompt states the rules; the code enforces them… Never rely on wording alone for a safety property."*
- **IMPORTS**: none.
- **GOTCHA**: Do not edit `DONT_KNOW`. Do not restore the triage prompt block — it is already deleted in the working tree; leave it deleted.
- **VALIDATE**: `.venv/Scripts/python.exe -m pytest backend/tests/test_agent_boundary.py -q` (it asserts the `DONT_KNOW` string).

### Task 5: Build the graph
- **ACTION**: Create `backend/app/agent/graph.py`.
- **IMPLEMENT**:
  ```python
  """The /chat intent router, as a LangGraph StateGraph.

  Why a graph here, when loop.py argues against one
  -------------------------------------------------
  loop.py's argument is about the deterministic engine and the assist chain, and
  it still stands -- neither is wrapped here. What this graph routes is the CHAT
  layer, where the tradeoff inverts:

    * the tool registry is now 21 specs. loop.run_agent() offers every one on
      every model call, and routing accuracy falls off with list length.
      Branching lets each intent see 3-6.
    * `run_assist` spends one model call per live row. A cost gate needs a node
      that can refuse and ask, not a tool the model may fire mid-sentence.
    * `action` needs its own hard rules ("you stage, you never execute") that
      would otherwise have to live in the one prompt every branch reads.

  run_agent() is NOT replaced. It already takes system_prompt, tool_names and
  max_model_calls, so each branch node is a thin call into it with a subset. The
  tool registry, the source-grounding control and the audit path are untouched.

  No checkpointer. Conversation state already lives in `conversation_turn`, and
  a checkpointer would make that two stores for the same history -- the same
  objection loop.py raised about approval state. `_history()` rehydrates prior
  turns from SQL instead: one query, survives a restart, correct across workers.
  """

  from __future__ import annotations

  import operator
  from typing import Annotated, Any, TypedDict

  from langgraph.graph import END, START, StateGraph

  from ..llm import Message, get_provider
  from . import tools as T
  from .loop import run_agent
  from .prompts import (ACTION_SYSTEM, ADVISORY_SYSTEM, ASSIST_SYSTEM,
                        CLASSIFY_SYSTEM, DONT_KNOW, LOOKUP_SYSTEM,
                        PROPOSE_SYSTEM)

  INTENTS = ("lookup", "assist", "advisory", "propose", "action", "unknown")
  BRANCH_PROMPT = {"lookup": LOOKUP_SYSTEM, "assist": ASSIST_SYSTEM,
                   "advisory": ADVISORY_SYSTEM, "propose": PROPOSE_SYSTEM,
                   "action": ACTION_SYSTEM}
  HISTORY_TURNS = 4          # prior turns rehydrated into the classify prompt


  class ChatState(TypedDict, total=False):
      conn: Any
      question: str
      batch_id: int | None
      actor: dict
      session_id: str
      on_event: Any
      allow_writes: bool
      intent: str
      answer: str
      sources: Annotated[list[dict], operator.add]
      tool_calls: Annotated[list[dict], operator.add]
      model_calls: Annotated[int, operator.add]
      staged_action: dict | None
      provider: str
      model: str
  ```
  Nodes:
  - `_history(conn, session_id)` — `SELECT question, answer FROM conversation_turn WHERE session_id=? ORDER BY turn_id DESC LIMIT ?`, reversed. Returns `[]` when `session_id` is falsy.
  - `classify(state)` — send `CLASSIFY_SYSTEM` + the rehydrated history + the question with **no tools**, normalise `resp.content.strip().lower()` against `INTENTS`, and default to `"lookup"` on anything unrecognised (never `"unknown"` — an unparseable classification must not become a refusal). Emit `{"type": "classify", "intent": intent, "provider": ..., "model": ...}`. When `intent in T.WRITE_INTENTS and not state["allow_writes"]`, downgrade to `"lookup"` and emit `{"type": "intent_downgraded", "from": intent, "reason": "role is read-only"}`.
  - `route(state) -> str` — returns `state["intent"]`; wired with `add_conditional_edges("classify", route)`.
  - One node per branch, all delegating to one helper:
    ```python
    def _branch(state: ChatState, intent: str) -> dict:
        result = run_agent(
            state["conn"], question=state["question"],
            batch_id=state["batch_id"], actor=state["actor"],
            session_id=state["session_id"],
            allow_writes=state["allow_writes"],
            on_event=state.get("on_event"),
            system_prompt=BRANCH_PROMPT[intent],
            tool_names=T.INTENT_TOOLS[intent])
        staged = next((s for s in result["sources"]
                       if s.get("type") == "staged_action"), None)
        return {"answer": result["answer"], "sources": result["sources"],
                "tool_calls": result["tool_calls"],
                "model_calls": result["model_calls"],
                "provider": result["provider"], "model": result["model"],
                "staged_action": staged}
    ```
  - `unknown(state)` — returns `DONT_KNOW` with empty sources and `model_calls: 0`. No model call.
  - `synthesize(state)` — the one place the source-grounding control is applied for every branch: when `not state["sources"]` or not `state["answer"]`, set `answer = DONT_KNOW`.
  - `build_graph()` — `START → classify`, `add_conditional_edges("classify", route)`, every branch node → `synthesize` → `END`, `compile(checkpointer=False)`. Module-level `CHAT_GRAPH = build_graph()`.
  - `run_chat(conn, question, batch_id, actor, session_id, allow_writes=True, on_event=None)` — builds the initial state, calls `CHAT_GRAPH.invoke(state)`, and returns **the same dict shape `run_agent` returns** plus `"intent"` and `"staged_action"`, so `chat.py`'s `log_turn` and `_record_turn` keep working unchanged.
- **MIRROR**: GRAPH_PATTERN above, copied from the repo's own deleted `graph.py`.
- **IMPORTS**: as written above. Note `staged_action` is a plain `dict | None` (last-write-wins), not an accumulating field.
- **GOTCHA**:
  - **`persist` is not a graph node.** `chat.py:_record_turn` already logs and audits inside the router's `try/finally`; a persist node would either duplicate that or move the commit away from the rollback handler in `chat_stream`'s worker (`chat.py:221-235`). Leave persistence in the router.
  - `Annotated[..., operator.add]` reducers **append**. Never return the full accumulated list from a branch node or entries double.
  - `conn` in state is a live `Conn` with an `RLock` (`db.py:825`). Never fan out branch nodes in parallel — they would serialise on that lock anyway, and `add_conditional_edges` returning a single `str` keeps it sequential by construction.
  - `compile(checkpointer=False)` — not `None`, matching the repo's proven call.
  - `run_agent` returns its model-call count under the key `"model_calls"` (`loop.py:193`); keep the name so the reducer sums correctly.
- **VALIDATE**: from `backend/`, `../.venv/Scripts/python.exe -c "from app.agent.graph import CHAT_GRAPH; print(CHAT_GRAPH.get_graph().draw_ascii())"`

### Task 6: Teach EchoProvider the new branches and tools
- **ACTION**: Extend `backend/app/llm/echo.py` so the whole graph runs offline.
- **IMPLEMENT**:
  1. At the very top of `chat()` — **before** the existing `tool_results` short-circuit at `echo.py:69` — add the classify branch. The classify call is the only one that arrives with `CLASSIFY_SYSTEM` as `messages[0].content` and no tools:
     ```python
     if messages and messages[0].content.startswith("Classify the engineer's"):
         return Response(content=self._classify(last_user),
                         model=self.model, provider=self.name)
     ```
  2. `_classify(self, q)` — a regex ladder returning one branch name, narrowest first:
     ```python
     ACTION_INTENT_RE   = re.compile(r"\b(confirm|discard|cancel)\b.*\bpending\b|"
                                     r"\bpending\s*#?\d+", re.IGNORECASE)
     ASSIST_INTENT_RE   = re.compile(r"\b(assist|verdict|flagged for review|"
                                     r"bulk[- ]accept|needs context)\b", re.IGNORECASE)
     ADVISORY_INTENT_RE = re.compile(r"\b(similar|comparable|peer|analogue|"
                                     r"outlier|dormant|coverage)\b", re.IGNORECASE)
     ```
     Order: `action` → `propose` (`WRITE_RE` **and** `QTY_RE` both match, and the quantity is not the item id) → `assist` → `advisory` → `lookup` when any existing `_route` branch would fire → `unknown`.
  3. Add `_route` branches for the six new tools, each guarded by `name in available` so a branch that was not offered cannot be selected:
     - `get_assist_verdict` — item id + assist words
     - `list_assist_queue` — assist words + `LIST_RE`, extracting `verdict` from the three `VERDICTS` strings
     - `run_assist` — `\brun\b` + assist words; emit `{}` with **no** `confirm` so the gate fires on the first ask
     - `get_similarity_outliers` — `outlier` with no item id
     - `get_dormant_coverage` — `dormant` + `coverage`
     - `stage_review_action` — item id + confirm/discard, extracting `pending_id` from `#(\d+)` or `\bpending\s+(\d+)\b`
  4. Add `_render` cases for all six so the stub composes a readable sentence instead of falling through to `json.dumps(data)[:400]` (`echo.py:296`).
- **MIRROR**: `echo.py:92-194` — the docstring's rule holds: *"First match wins, narrowest signal first"*, and every branch checks `in available`.
- **IMPORTS**: none new; add the `_RE` constants beside the existing ones.
- **GOTCHA**:
  - `echo.py:69` short-circuits to `_summarise` whenever any `tool` message is present. The classify check **must** come first, or the second turn of a branch misclassifies.
  - Do not teach EchoProvider to read the system prompt for routing — `test_chat_routing.py:9-11` explicitly forbids it ("that would make the stub a model and the test a tautology"). Classification here is a regex ladder measured against a hand-written expectation table, exactly like `_route`.
- **VALIDATE**: `.venv/Scripts/python.exe -m pytest backend/tests/test_chat_routing.py -q`

### Task 7: Wire the router to the graph
- **ACTION**: Update `backend/app/routers/chat.py`.
- **IMPLEMENT**: In `chat()` and in `chat_stream()`'s `worker()`, replace the `run_agent(...)` call with `run_chat(...)` (same kwargs; `chat_stream` keeps `on_event=emit`). Add `"intent"` and `"staged_action"` to both response dicts and to the `complete` stream event. Extend `_record_turn`'s audit payload with `"intent": result["intent"]` and `"staged_action": bool(result.get("staged_action"))`. In `_predict_next_steps` (`chat.py:82-87`), narrow the tool set:
  ```python
  def _predict_next_steps(question: str, answer: str) -> dict:
      # allow_writes=False drops propose_change but NOT run_assist or
      # stage_review_action. suggestions.SYSTEM forbids suggesting an action in
      # words; this enforces it.
      safe = agent_tools.INTENT_TOOLS["lookup"] | agent_tools.INTENT_TOOLS["advisory"]
      return predict_next_steps(
          question, answer,
          agent_tools.specs(allow_writes=False, names=safe))
  ```
- **MIRROR**: `chat.py:90-122` and `chat.py:166-252` — the worker-thread + `Queue` + NDJSON generator pattern is unchanged; only the call and the payload keys change.
- **IMPORTS**: `from ..agent.graph import run_chat`; keep `from ..agent.loop import log_turn`, and drop `run_agent` from that import once unused.
- **GOTCHA**: `log_turn` (`loop.py:197`) reads `session_id`, `answer`, `tool_calls`, `provider`, `model` off the result. `run_chat` must return every one or the insert raises `KeyError` **inside the worker thread**, where it surfaces only as a stream `error` event.
- **VALIDATE**: `.venv/Scripts/python.exe -m pytest backend/tests/test_api_flow.py backend/tests/test_agent_boundary.py -q`

### Task 8: Schema for the action card
- **ACTION**: Add the response model to `backend/app/schemas.py`.
- **IMPLEMENT**:
  ```python
  class StagedAction(BaseModel):
      """What the agent staged for a human to press. Nothing is recorded until
      the engineer confirms, and the confirm goes through the same endpoint the
      console uses -- see routers/review.py confirm_pending."""
      kind: Literal["confirm_pending", "discard_pending", "open_review"]
      item_id: str
      batch_id: int | None = None
      stockroom_id: str | None = None
      pending_id: int | None = None
      proposed_max: int | None = None
      proposed_rop: int | None = None
      proposed_min: int | None = None
      executed: bool = False
  ```
- **MIRROR**: `ChatRequest` (`schemas.py:33-36`) — `BaseModel`, explicit `| None = None` optionals, docstring only where the field set needs justification.
- **IMPORTS**: `Literal` from `typing` if not already imported.
- **GOTCHA**: `executed` is constant `False` here on purpose. If it ever needs to be `True`, that change belongs in the review router, not in a chat schema.
- **VALIDATE**: `.venv/Scripts/python.exe -m pytest backend/tests/test_schema_parity.py -q`

### Task 9: Frontend types
- **ACTION**: Extend `frontend/src/lib/types.ts`.
- **IMPLEMENT**:
  ```ts
  export type ChatIntent =
    "lookup" | "assist" | "advisory" | "propose" | "action" | "unknown";

  /** Staged by the chat agent, executed by nothing until the engineer clicks.
   *  The click calls the same review endpoint the console tray uses. */
  export interface StagedAction {
    kind: "confirm_pending" | "discard_pending" | "open_review";
    item_id: string;
    batch_id: number | null;
    stockroom_id: string | null;
    pending_id: number | null;
    proposed_max: number | null;
    proposed_rop: number | null;
    proposed_min: number | null;
    executed: false;
  }
  ```
  Add `intent: ChatIntent` and `staged_action: StagedAction | null` to `ChatResponse` (`types.ts:303`), and two members to the `ChatStreamEvent` union (`types.ts:365`):
  ```ts
    | { type: "classify"; intent: ChatIntent; provider: string; model: string }
    | { type: "intent_downgraded"; from: ChatIntent; reason: string }
  ```
- **MIRROR**: `types.ts:387-403` — the `PendingChange` doc comment explains *why the type is safe*, not what the fields are. Match that.
- **IMPORTS**: none.
- **GOTCHA**: `ChatStreamComplete extends ChatResponse` (`types.ts:354`), so adding the two fields to `ChatResponse` covers the stream too. `executed: false` as a literal type is deliberate — it makes an "executed" card a compile error.
- **VALIDATE**: `cd frontend && npx tsc --noEmit`

### Task 10: The action card component
- **ACTION**: Create `frontend/src/components/ActionCard.tsx`.
- **IMPLEMENT**: A presentational component only:
  ```tsx
  export function ActionCard({ action, busy, onConfirm, onDiscard }: {
    action: StagedAction; busy: boolean;
    onConfirm: (a: StagedAction) => void; onDiscard: (a: StagedAction) => void;
  })
  ```
  Renders the kind as a heading, the item id, the proposed `max / rop / min`, an explicit "Nothing has been recorded yet" line, and two buttons disabled while `busy`.
- **MIRROR**: `frontend/src/components/SuggestionCards.tsx` and `NextStepSuggestions.tsx` for card/button markup and class names; the existing pending tray in `chat/page.tsx` for the senior-approval wording.
- **IMPORTS**: `import type { StagedAction } from "@/lib/types";`
- **GOTCHA**: This component must not call the API. `chat/page.tsx` already owns `confirm(p)` (`page.tsx:589`) and `discard(p)` (`page.tsx:613`) with the correct request bodies, `setNote` messaging and `refreshPending()` — pass those in.
- **VALIDATE**: `cd frontend && npx tsc --noEmit && npm run lint`

### Task 11: Render the card and the classify step
- **ACTION**: Update `frontend/src/app/chat/page.tsx`.
- **IMPLEMENT**:
  1. Add `stagedAction?: StagedAction | null` to the local `Turn` interface (`page.tsx:20`).
  2. In `applyTraceEvent` (`page.tsx:66`), handle the two new events:
     ```tsx
     if (event.type === "classify") {
       return upsertTraceStep({ ...trace, provider: event.provider, model: event.model }, {
         id: "classify", label: "Routing the question",
         detail: `${event.provider} · ${event.model}`,
         result: `${event.intent} branch`, status: "done",
       });
     }
     if (event.type === "intent_downgraded") {
       return upsertTraceStep(trace, {
         id: "classify-downgrade", label: "Read-only route",
         detail: event.reason, result: `${event.from} not available`,
         status: "warning",
       });
     }
     ```
  3. In the `complete` handler (`page.tsx:439`), carry `completed.staged_action` into the new turn.
  4. Render `<ActionCard>` under the answer when `turn.stagedAction` is set and `canReview`, wiring `onConfirm` / `onDiscard` to the existing `confirm` / `discard` and building the `PendingChange`-shaped argument they expect from the card's fields.
- **MIRROR**: `page.tsx:66-130` for the trace-event shape, `page.tsx:435-455` for the complete handler.
- **IMPORTS**: `import { ActionCard } from "@/components/ActionCard";`
- **GOTCHA**:
  - The non-streaming fallback (`page.tsx:466-473`) builds a synthetic `ChatStreamComplete` by spreading `...response`, so `staged_action` and `intent` come through **only** because Task 9 put them on `ChatResponse` — verify, do not assume.
  - `canReview` gates rendering only. The backend enforces regardless (`security.require_role`).
- **VALIDATE**: `cd frontend && npx tsc --noEmit`, then `make dev` and drive the three UX flows above.

### Task 12: Tests
- **ACTION**: Create `backend/tests/test_chat_graph.py`; extend the two existing test files.
- **IMPLEMENT**: `test_chat_graph.py` opens with:
  ```python
  """The graph routes to a branch, and a branch cannot exceed its tool subset.

  test_agent_boundary.py owns the safety properties and test_chat_routing.py owns
  flat tool routing; this file owns the property neither can see -- that the
  intent classifier picks a branch, and that a branch is offered only its own
  tools.
  """
  ```
  Tests:
  1. `test_every_intent_group_names_real_tools` — every name in `INTENT_TOOLS` is a `REGISTRY` key.
  2. `test_branch_is_offered_only_its_own_tools` — monkeypatch a provider that records the `tools` argument of every `chat()` call; for each of the five branches assert the recorded names are a subset of `INTENT_TOOLS[intent]`.
  3. `test_assist_question_reaches_the_assist_branch` — `POST /chat` "which items did assist flag for review?"; `tools_used(db_file)` contains `list_assist_queue`.
  4. `test_run_assist_is_gated_on_first_ask` — "run assist on batch N" writes **zero** `assist_result` rows and the answer names the row count.
  5. `test_run_assist_runs_when_confirmed` — call the tool directly with `confirm=True`; `assist_result` count equals `len(live_rows(conn, batch_id))`.
  6. `test_action_card_records_nothing` — stage a proposal, then "confirm the pending change on item X": `review_history` count stays 0, `pending_change.status` stays `'pending'`, response `staged_action["executed"] is False`.
  7. `test_viewer_is_downgraded_to_lookup` — a `VIEWER` request whose text is a propose utterance runs no write tool and writes no `pending_change` row.
  8. `test_unclassifiable_intent_falls_back_to_lookup` — hostile provider returning `"banana"` from classify; the turn still runs a lookup tool rather than returning `DONT_KNOW`.
  9. `test_assist_verdict_on_absent_row` — an item with no `assist_result` yields `_empty` and the turn says it does not know.
  10. `test_dormant_coverage_on_batch_without_dormant_rows` — clean zeros, not a crash.
  In `test_chat_routing.py`, append to `CASES`:
  ```python
      ("what does assist say about item 100005", "get_assist_verdict"),
      ("show the items assist flagged for review", "list_assist_queue"),
      ("which items are outliers",                "get_similarity_outliers"),
      ("dormant rule coverage for this batch",    "get_dormant_coverage"),
      ("confirm pending 1 for item 100005",       "stage_review_action"),
  ```
  In `test_agent_boundary.py`, add property 7 to the module docstring ("7. an action card is staged, not executed") and the matching test.
- **MIRROR**: TEST_STRUCTURE above — `conftest` fixtures, assertions read back from SQLite, hostile providers for safety properties.
- **IMPORTS**: `from conftest import ENG, SENIOR, VIEWER, upload`
- **GOTCHA**: The suite runs on `EchoProvider` (`conftest.py:21` clears `LLM_BASE_URL`). Every case added to `CASES` needs its `_route` branch from Task 6, or it fails as a routing miss rather than as a graph bug.
- **VALIDATE**: `make test`

### Task 13: Amend loop.py's docstring
- **ACTION**: Update `backend/app/agent/loop.py`'s module docstring so it no longer contradicts the module above it.
- **IMPLEMENT**: Keep the existing paragraphs — the argument about the deterministic engine and about approval state is still correct and still load-bearing. Append:
  ```
  What changed
  ------------
  agent/graph.py now sits in front of this loop and picks which subset of tools
  it is given, because the registry outgrew a single flat tool list. That graph
  routes; it still does not wrap the engine or the assist chain, and it still
  does not checkpoint -- conversation state stays in `conversation_turn` and
  approval state stays in `review_history`, exactly as argued above.
  ```
- **MIRROR**: the file's own voice — it argues, it does not announce.
- **IMPORTS**: none.
- **GOTCHA**: Do not delete the original argument. A future reader needs to know why the engine is *not* in a graph, and that reason did not change.
- **VALIDATE**: `make test`

### Task 14: Update the codebase map
- **ACTION**: Add the graph to `docs/CURRENT_CODEBASE_END_TO_END.md`.
- **IMPLEMENT**: In the chat/agent section, insert the graph between the router and the loop: `/chat → graph.classify → branch → run_agent(subset) → synthesize → _record_turn`. Note that `run_assist` is the chat path into the assist chain and that it is gated.
- **MIRROR**: the document's existing per-layer prose.
- **IMPORTS**: n/a
- **GOTCHA**: The document may still describe `triage.py` / `specialists.py` if those sections were not cleaned when they were deleted. Correct only the chat section; a wider cleanup is out of scope for this plan.
- **VALIDATE**: manual read.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_every_intent_group_names_real_tools` | `INTENT_TOOLS` | every name is in `REGISTRY` | no |
| `test_branch_is_offered_only_its_own_tools` | recorded provider `tools` arg | subset of the branch's group | no |
| `test_assist_question_reaches_the_assist_branch` | "which items did assist flag for review?" | `list_assist_queue` in `tool_calls` | no |
| `test_run_assist_is_gated_on_first_ask` | "run assist on batch 1" | 0 `assist_result` rows; answer names the count | **yes** |
| `test_run_assist_runs_when_confirmed` | `run_assist(confirm=True)` | rows == `len(live_rows())` | no |
| `test_action_card_records_nothing` | "confirm the pending change on 100005" | `review_history` == 0; pending still `'pending'` | **yes** |
| `test_viewer_is_downgraded_to_lookup` | `VIEWER` + a propose utterance | no write tool; no `pending_change` row | **yes** |
| `test_unclassifiable_intent_falls_back_to_lookup` | classify returns `"banana"` | a lookup tool runs; not `DONT_KNOW` | **yes** |
| `test_assist_verdict_on_absent_row` | item with no `assist_result` | `_empty`; turn says it does not know | **yes** |
| `test_dormant_coverage_on_batch_without_dormant_rows` | batch with 0 dormant rows | clean zeros, no crash | **yes** |
| `CASES` additions | five new utterances | the five new tools | no |

### Edge Cases Checklist
- [ ] Empty input — `ChatRequest.question` has `min_length=1`; unchanged
- [ ] No scored batch — `_resolve_batch` returns `None` (`chat.py:52`); every new tool handles `bid is None` the way `batch_summary` does
- [ ] Item in two stockrooms — reuse `_resolve` so the `AmbiguousItem` message is identical across tools
- [ ] Batch with 0 live rows — `run_assist` returns a clean no-op, matching `routers/assist.py:34`
- [ ] `assist_result` empty (assist never run) — `list_assist_queue` returns `EMPTY`; the answer says so
- [ ] Classifier returns junk or empty — falls back to `lookup`, never to `unknown`
- [ ] Read-only role hits a write intent — downgraded, with a trace event saying so
- [ ] `pending_change` already confirmed — `stage_review_action` raises `ToolError`; no card
- [ ] Concurrent access — one `Conn` with an `RLock`; branches are sequential by construction
- [ ] Provider down — `run_agent` has no retry; the turn fails as an `error` stream event, same as today
- [ ] Non-streaming backend — the `page.tsx` fallback must carry `staged_action`

---

## Validation Commands

### Static Analysis
```bash
cd frontend && npx tsc --noEmit
```
EXPECT: Zero type errors

```bash
.venv/Scripts/python.exe -m compileall -q backend/app
```
EXPECT: no output

### Unit Tests
```bash
.venv/Scripts/python.exe -m pytest backend/tests/test_chat_graph.py backend/tests/test_chat_routing.py backend/tests/test_agent_boundary.py -q
```
EXPECT: All tests pass

### Full Test Suite
```bash
make test
```
EXPECT: No regressions. `test_assist_chain.py`, `test_similarity.py`, `test_dormant_rules.py` and `test_bulk_review.py` must be untouched by this change — if any of them moves, a shared function was modified instead of wrapped.

### Graph Shape
```bash
cd backend && ../.venv/Scripts/python.exe -c "from app.agent.graph import CHAT_GRAPH; print(CHAT_GRAPH.get_graph().draw_ascii())"
```
EXPECT: `__start__ → classify → {lookup, assist, advisory, propose, action, unknown} → synthesize → __end__`

### Browser Validation
```bash
make dev     # backend :8011, frontend :3010
```
EXPECT: `http://localhost:3010/chat` answers all three UX flows above.

### Manual Validation
- [ ] "why item \<id\>" still answers exactly as before (no regression on the lookup path)
- [ ] "what does assist say about \<id\>" returns the verdict + narrative with an `assist_result` source chip
- [ ] "show the items assist flagged" lists rows with counts
- [ ] "run assist on batch \<n\>" returns the cost gate and writes nothing; confirming runs it
- [ ] "which items are outliers" lists outliers; "dormant coverage" returns the numbers
- [ ] "set item \<id\> max to 3" still stages a `pending_change` and says it is not applied
- [ ] "confirm the pending change on \<id\>" renders a card; Confirm records the review and the tray refreshes
- [ ] As `VIEWER`: the same propose/action utterances answer read-only and stage nothing
- [ ] The trace panel shows a "Routing the question" step naming the branch

---

## Acceptance Criteria
- [ ] All 14 tasks completed
- [ ] All validation commands pass
- [ ] `make test` green, with the four untouched suites unchanged
- [ ] `npx tsc --noEmit` clean
- [ ] `review_history` still zero after any chat turn (`test_agent_boundary.py` property 1)
- [ ] `run_assist` writes nothing without `confirm=True`
- [ ] Every branch's offered tool set is a subset of its `INTENT_TOOLS` group

## Completion Checklist
- [ ] New tools mirror `get_recommendation`'s shape and every one appends to `ctx.sources`
- [ ] `ToolError` used for every rejection; no `HTTPException` inside a tool
- [ ] No `logging` calls added — events and `audit_log` only
- [ ] Tests use `conftest` fixtures and read state back from SQLite
- [ ] No hardcoded thresholds — row counts and configs read from the DB
- [ ] `loop.py`'s docstring amended, not deleted
- [ ] `docs/CURRENT_CODEBASE_END_TO_END.md` updated
- [ ] Nothing from `NOT Building` was built

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Circular import `tools → assist.chain → tools` | **High** | Import-time crash on every request | Function-local imports in `run_assist` / `get_dormant_coverage`, mirroring `recall_context` (`tools.py:414`). Any test run catches it immediately. |
| Misclassification sends a real question to `unknown` | Medium | A spurious `DONT_KNOW` — which `test_chat_routing.py:5` calls "a wrong answer with good manners" | Unrecognised classifier output defaults to `lookup`, never `unknown`; asserted by `test_unclassifiable_intent_falls_back_to_lookup` |
| `run_assist` fired without a gate | Low | Thousands of model calls against the production endpoint | Two independent controls: `confirm=False` default in the tool, and no confirm keyword in EchoProvider's route. Asserted by `test_run_assist_is_gated_on_first_ask`. |
| Action card drifts from the endpoint's validation | Medium | A card the engineer clicks that then 400s | `stage_review_action` reproduces `confirm_pending`'s three ownership checks (`review.py:232-241`) before staging |
| `predict_next_steps` suggests a gated action | Medium | Suggestion chips offer "run assist", violating `suggestions.SYSTEM:53` | Pass an explicit `names=` subset in `_predict_next_steps` (Task 7) rather than relying on `allow_writes=False` |
| Extra model call per turn (classify) | **Certain** | ~1 extra call per chat turn, higher latency | Accepted: it is what buys tool subsetting. `unknown` short-circuits with zero further calls, and branch subsets often save a retry the 15-tool list was causing. |
| Two stores for conversation state | Low | Contradicts `loop.py`'s own argument | No checkpointer; `conversation_turn` stays the single store |

## Notes

### Alternatives Considered

**Extend `loop.py` with more tools, no graph.** Rejected by the requester. It is the smaller diff and keeps the dependency count down, but it puts 21 specs in front of a single model call and grows `SYSTEM`'s routing table past readability. The graph exists for tool subsetting — state that in the module docstring so a future reader does not "simplify" it back.

**LangGraph checkpointer keyed on `session_id`.** Rejected on this codebase's own reasoning. `loop.py:12-15` refused a checkpointer because approval state already lives in SQL and a second store would make the audit trail ambiguous. The same objection applies to conversation state: `conversation_turn` already holds every turn verbatim (it is the ML label corpus — `Feature_Selection_TCB_Jan26.md` section 12.6), `GET /chat/sessions/{id}` already reads it back, and an `InMemorySaver` would be per-process and lost on restart. `classify` rehydrates the last four turns with one query instead. If durable graph checkpointing is ever genuinely needed, it is a one-line change at `build_graph()`'s `compile()` call — that is the seam, and it is deliberately left clean.

**Executing review actions directly from chat.** Rejected on the safety boundary, and the requester's ask is met without it. `test_agent_boundary.py:35-47` asserts `review_history` stays empty after a chat turn, and `chat.py:5-7` states the guarantee that the agent "has no path to `review_history`, so nothing it does can reach a WINGS export file". The action card gives the engineer the same one-click convenience while the write still originates from a human click against an RBAC-guarded endpoint. Net effect: the capability is delivered, the invariant holds, and no existing test needed weakening.

### Sequencing
Tasks 1–8 are backend and can land as one commit; the suite is green at task 8. Tasks 9–11 are frontend and land second — the backend returning `staged_action` before the UI renders it is harmless. Tasks 12–14 close it out. If the work splits across sessions, task 8 is the natural checkpoint.
