# Plan: Tune NYRA Response Quality — Tool-Selection Accuracy

## Summary
NYRA picks the wrong tool (or no tool) for a large class of legitimate engineer
questions, which surfaces to the user as either a wrong answer or a spurious
`DONT_KNOW`. Fix it at the three places that actually decide routing: the
`ToolSpec` descriptions the model sees, the `SYSTEM` prompt's (currently absent)
intent→tool contract, and `EchoProvider._route` — which today cannot reach 3 of
the 10 registered tools, so routing regressions are invisible to CI. Add a
golden routing test so the improvement is measured, not asserted.

## User Story
As a BOM review engineer,
I want NYRA to reach for the right tool for the question I actually asked,
So that I get the engine's stored answer instead of "I don't know" or a reply
about the wrong thing.

## Problem → Solution
**Current**: routing is implicit. The model gets 10 terse tool descriptions with
overlapping scopes and a `SYSTEM` prompt that describes capabilities in prose
without naming a single tool. Offline (`EchoProvider`) routing is a regex ladder
that misses common phrasings entirely and never reaches
`get_current_values` / `list_review_queue` / `recall_context`.
**Desired**: every tool description states *use when* and *do not use when* for
its nearest neighbour; `SYSTEM` carries an explicit intent→tool table plus an
output contract; `EchoProvider._route` covers all 10 tools with fixed
precedence; a golden fixture asserts intent→tool for 16 phrasings so the pass
rate is a number.

## Metadata
- **Complexity**: Medium
- **Source PRD**: `docs/PRD_Technical_BOM_Review_Assistant.md` (section 8 — NYRA controls)
- **PRD Phase**: N/A — standalone tuning pass
- **Estimated Files**: 4 changed, 1 created

---

## UX Design

### Before
```
┌──────────────────────────────────────────────────────────────┐
│ Eng: "what is item 100005 max right now?"                    │
│ NYRA: "I don't know — no data source matches that question."  │
│        ^ get_current_values exists. No branch matched.        │
│                                                               │
│ Eng: "how many high risk items are left?"                    │
│ NYRA: "Batch 1: 8 scored, statuses {...}, pending $..."      │
│        ^ batch_summary. Question asked for a filtered count.  │
│                                                               │
│ Eng: "show me the review queue"                              │
│ NYRA: "I don't know — ..."                                    │
│        ^ list_review_queue is registered but unreachable.     │
└──────────────────────────────────────────────────────────────┘
```

### After
```
┌──────────────────────────────────────────────────────────────┐
│ Eng: "what is item 100005 max right now?"                    │
│ NYRA: "Item 100005 current: max 1/rop 0/min 0                │
│        (stockroom 24)"             [get_current_values]      │
│                                                               │
│ Eng: "how many high risk items are left?"                    │
│ NYRA: "Review queue for batch 1 (3 items): 100005 ..."       │
│                                    [list_review_queue]       │
│                                                               │
│ Eng: "show me the review queue"                              │
│ NYRA: "Review queue for batch 1 (8 items): ..."              │
│                                    [list_review_queue]       │
│                                                               │
│ Eng: "what is the capital of France?"                        │
│ NYRA: "I don't know — ..." (unchanged; correct refusal)        │
└──────────────────────────────────────────────────────────────┘
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `POST /chat` current-value question | `DONT_KNOW` | `get_current_values` result | No API shape change |
| `POST /chat` filtered-list question | `batch_summary` | `list_review_queue` with filter | `sources` now carries a `count` |
| `POST /chat` off-domain question | `DONT_KNOW` | `DONT_KNOW` (unchanged) | Hard control at `loop.py:87-91` untouched |
| `DONT_KNOW` capability list | 4 examples | 6 examples incl. current values + queue | String is asserted in tests — see Task 5 |
| Response shape | free-form | lead line + evidence, contract stated in `SYSTEM` | Prompt-side only; no formatter module |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/app/agent/prompts.py` | 1-53 | The `SYSTEM` string and `DONT_KNOW` you are editing. Header comment states the governing rule: prompt states, code enforces. |
| P0 | `backend/app/agent/tools.py` | 293-386 | `REGISTRY` — the 10 `ToolSpec`s whose `description` fields the model routes on, plus `specs()` |
| P0 | `backend/app/llm/echo.py` | 22-90 | `EchoProvider._route` — the offline routing ladder to extend |
| P0 | `backend/app/agent/loop.py` | 47-101 | How `SYSTEM`, `specs()` and `DONT_KNOW` compose. The `if not ctx.sources or not answer` control at 87-91 is the safety net — do not weaken it. |
| P1 | `backend/app/agent/tools.py` | 65-226 | The read-tool bodies: what each actually returns, and which set `EMPTY` |
| P1 | `backend/tests/conftest.py` | 21-25, 97-135 | `ENG`/`VIEWER`/`SENIOR` headers and the `synth_csv` fixture — items `100001`-`100008` |
| P1 | `backend/tests/test_api_flow.py` | 119-138 | The existing chat-routing test to mirror (`test_chat_read_only_tools`) |
| P2 | `backend/tests/test_agent_boundary.py` | 169-194 | The `DONT_KNOW` and `specs()` assertions your changes must not break |
| P2 | `backend/app/llm/echo.py` | 105-156 | `_render` — the offline answer renderer; new routes need new render branches |
| P2 | `backend/app/llm/nyra.py` | 51-62 | Real provider: `temperature=0`, `tool_choice="auto"`. Sampling stays as-is. |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Tool-description quality | OpenAI function-calling guidance (the wire format `nyra.py` speaks) | The `description` field is the primary routing signal — more load-bearing than the system prompt. Disambiguate overlapping tools *in the descriptions*. |
| Tool count vs accuracy | Same | Selection accuracy degrades as near-duplicate tools accumulate. 10 tools with 3 overlapping pairs is exactly that shape. |

> No new library, no new endpoint, no version-specific gotcha. Everything below
> is prompt text, a regex ladder, and a pytest module.

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: backend/app/agent/tools.py:65-83
def get_recommendation(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None,
                       stockroom_id: str | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    r = _resolve(ctx, bid, item_id, stockroom_id)
    if r is None:
        return EMPTY
    ctx.sources.append({"type": "recommendation_result", "batch_id": bid, ...})
    return {"item_id": r["item_id"], ...}
```
snake_case tool functions, `ctx` first, `-> dict`, `EMPTY` for no-hit, always
append to `ctx.sources` before returning data.

### MODULE_DOCSTRING_STYLE
```python
# SOURCE: backend/app/agent/loop.py:1-20
"""Bounded tool-calling loop.

Why this and not LangGraph
--------------------------
The deterministic workflow this agent fronts already exists and is already
deterministic: analysis/engine/engine.py (rule_version 0.2.0-tcb) ...
"""
```
Every module opens with a docstring that justifies the *design choice*, not just
what the code does, and cites the PRD section or file it derives from. New
comments must match this register — decisions with reasons, not restatements.

### TOOLSPEC_DESCRIPTION
```python
# SOURCE: backend/app/agent/tools.py:301-307
    "get_recommendation": (ToolSpec(
        "get_recommendation",
        "What the engine recommended for one item, with its reason code and "
        "explanation. Use this to answer 'why' questions.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH,
                                          "stockroom_id": _STOCK},
         "required": ["item_id"]}), get_recommendation),
```
Positional `ToolSpec(name, description, json_schema)`; description is an
implicitly-concatenated string literal; shared param schemas come from the
`_ITEM` / `_BATCH` / `_STOCK` constants at `tools.py:293-298`.

### ECHO_ROUTE_LADDER
```python
# SOURCE: backend/app/llm/echo.py:55-90
    def _route(self, q: str, available: set[str]) -> ToolCall | None:
        ql = q.lower()
        item = ITEM_RE.search(q)

        if item and any(v in ql for v in WRITE_VERBS):
            qty = QTY_RE.search(q)
            if qty and "propose_change" in available:
                value = int(next(g for g in qty.groups() if g))
                return ToolCall("c1", "propose_change", {...})

        if item and any(w in ql for w in ("why", "explain", "reason")):
            return ToolCall("c1", "get_recommendation",
                            {"item_id": item.group(1)})
        ...
        return None
```
First match wins, top to bottom. `available` gates write tools. `ToolCall` id is
the literal `"c1"`. Returning `None` yields no content and no tool call, which
`loop.py:87-91` converts to `DONT_KNOW`.

### ECHO_RENDER_BRANCH
```python
# SOURCE: backend/app/llm/echo.py:114-125
        if tool == "get_recommendation":
            return (f"Item {data['item_id']}: {data['explanation']} "
                    f"(action: {data['action']}, review required: "
                    f"{data['review_required']}, risk: {data['risk_level']}, "
                    f"confidence: {data['confidence']:.2f})")

        if tool == "get_item_history":
            rows = data.get("reviews", [])
            lines = [f"{r['reviewed_at']}: {r['reviewer']} {r['decision']} -> "
                     f"max {r['final_max']}/rop {r['final_rop']}/"
                     f"min {r['final_min']}" for r in rows]
            return f"Review history for {data['item_id']}: " + "; ".join(lines)
```
Flat `if tool == "..."` chain, f-strings, `data.get(...)` for list payloads,
money as `${x:,.0f}`, `"; ".join(lines)` for multi-row.

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_api_flow.py:119-138
def test_chat_read_only_tools(client, synth_csv):
    b, _ = scored_batch(client, synth_csv)
    why = client.post("/chat", json={"question": "why item 100007?"},
                      headers=VIEWER).json()
    assert "recommends 0" in why["answer"]
    assert why["sources"]
    ...
    unknown = client.post("/chat", json={"question": "what is the meaning of life"},
                          headers=VIEWER).json()
    assert "I don't know" in unknown["answer"]
    assert unknown["sources"] == []
```
Function-level tests, `client` + `synth_csv` fixtures, header constants imported
`from conftest import ...`, assert on the response JSON. Note
`test_api_flow.scored_batch` returns a tuple `(batch_id, response)` while
`test_agent_boundary.scored_batch` returns just `batch_id` — each module defines
its own helper; do the same rather than importing across test modules.

### TEST_DB_INSPECTION
```python
# SOURCE: backend/tests/test_agent_boundary.py:24-29
def rows(db_file, sql):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()
```
Direct sqlite3 read against the `db_file` fixture when asserting persisted
state. `conversation_turn.tool_calls` is a JSON string — this is how a test
asserts *which tool ran* (see `test_agent_boundary.py:206-211`).

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/tests/test_chat_routing.py` | CREATE | Golden intent→tool fixture; the measurement that makes this a tuning pass and not a guess |
| `backend/app/agent/tools.py` | UPDATE | Rewrite the 10 `ToolSpec.description` strings with use-when / not-when disambiguation. Registry structure and every tool body unchanged. |
| `backend/app/agent/prompts.py` | UPDATE | Add an intent→tool routing table and an output contract to `SYSTEM`; extend `DONT_KNOW`'s capability list |
| `backend/app/llm/echo.py` | UPDATE | Reach the 3 unreachable tools, fix precedence, add matching `_render` branches |
| `backend/tests/test_agent_boundary.py` | UPDATE | Only if the `DONT_KNOW` substring assertion at line 175 breaks — see Task 5 GOTCHA |

## NOT Building

- **No `format.py` / answer-formatter module.** The output contract goes in
  `SYSTEM` as text (zero code) and `echo._render` already *is* the offline
  renderer. A third formatting layer would be a second source of truth for
  answer shape. Add one only if a golden test proves prompt-side shaping
  insufficient.
- **No sampling/decoding config.** `nyra.py:56` keeps `temperature=0`; PRD
  section 10 wants determinism and nothing here needs entropy.
- **No SFT / model fine-tuning.** `conversation_turn` is the label corpus
  (`loop.py:104-107`) but training is blocked on PRD section 15 question 2
  (data sensitivity governance).
- **No few-shot examples in `SYSTEM` on the first pass.** They cost tokens on
  every turn. Earn them: add only if Task 6's pass rate stays under target.
- **No new tools.** All 10 registered tools stay; the fix is describing and
  reaching the ones that exist.
- **No eval run against the real `NyraProvider`.** Needs `LLM_BASE_URL` and
  egress; CI has neither. Golden tests run on `EchoProvider`.
- **No frontend change.** The `/chat` response shape is unchanged.

---

## Step-by-Step Tasks

### Task 1: Write the golden routing test (RED first)
- **ACTION**: Create `backend/tests/test_chat_routing.py`.
- **IMPLEMENT**: A module-level list of `(question, expected_tool)` cases,
  parametrised into one test that posts to `/chat` and reads which tool ran out
  of `conversation_turn.tool_calls`. Include the currently-broken cases so the
  test starts RED, plus the currently-passing ones as regression guards, plus
  two negative cases that must route to *nothing*.
  ```python
  """Intent -> tool routing accuracy.

  test_agent_boundary.py asserts the safety properties; this module asserts the
  *usefulness* one -- that a legitimate question reaches the tool that can
  answer it. Both matter: a spurious "I don't know" is a wrong answer with good
  manners.

  Routing runs on EchoProvider (llm/echo.py), which is the only provider CI can
  reach. It is a regex ladder, not a model, so this measures the contract the
  ladder and the ToolSpec descriptions agree on -- not model judgement.
  """

  import json
  import sqlite3

  import pytest
  from conftest import ENG, upload

  CASES = [
      # (question, expected tool or None)
      ("why item 100005?",                      "get_recommendation"),
      ("explain the recommendation for 100007",  "get_recommendation"),
      ("what is item 100005 max right now?",    "get_current_values"),
      ("current min for 100005",                "get_current_values"),
      ("history 100005",                        "get_item_history"),
      ("what notes are on item 100005",         "get_item_notes"),
      ("top exposure items",                    "top_exposure"),
      ("highest value items left to review",    "top_exposure"),
      ("show me the review queue",              "list_review_queue"),
      ("list the high risk items",              "list_review_queue"),
      ("which items are set to Decrease",       "list_review_queue"),
      ("batch summary",                         "batch_summary"),
      ("what thresholds is the engine using",   "explain_rules"),
      ("set item 100005 max to 3",              "propose_change"),
      ("increase item 100005 a little",         None),   # vague -> no stage
      ("what is the capital of France",         None),   # off-domain
  ]


  def scored_batch(client, csv_bytes):
      b = upload(client, csv_bytes).json()["batch_id"]
      client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
      return b


  def tools_used(db_file) -> list[str]:
      conn = sqlite3.connect(db_file)
      try:
          raw = conn.execute("SELECT tool_calls FROM conversation_turn "
                             "ORDER BY turn_id DESC LIMIT 1").fetchone()
      finally:
          conn.close()
      return [c["name"] for c in json.loads(raw[0])] if raw else []


  @pytest.mark.parametrize("question,expected", CASES)
  def test_routes_to_expected_tool(client, synth_csv, db_file, question, expected):
      scored_batch(client, synth_csv)
      r = client.post("/chat", json={"question": question}, headers=ENG)
      assert r.status_code == 200, r.text
      used = tools_used(db_file)
      if expected is None:
          assert "propose_change" not in used, used
      else:
          assert expected in used, f"{question!r} -> {used}, wanted {expected}"
  ```
- **MIRROR**: TEST_STRUCTURE and TEST_DB_INSPECTION above; module docstring per
  MODULE_DOCSTRING_STYLE.
- **IMPORTS**: `json`, `sqlite3`, `pytest`, `from conftest import ENG, upload`.
- **GOTCHA**: `upload` is a plain helper in `conftest.py` (imported the same way
  at `test_agent_boundary.py:15`), not a fixture — import it, do not request it
  as a test parameter. Both `client` and `db_file` are needed: `client` depends
  on `db_file`, and the test reads that same sqlite file directly. `ENG` is used
  even for read questions so the `propose_change` case has the write tool
  offered (`chat.py:52` gates on `REVIEW_ROLES`). For the `None` cases assert
  only that nothing was *staged* — an off-domain question legitimately produces
  an empty `used`, and a vague one may still consult a read tool.
- **VALIDATE**: `cd backend && python -m pytest tests/test_chat_routing.py -q`
  → expect roughly 5-7 FAILED (`get_current_values` ×2, `list_review_queue` ×3,
  plus any phrasing variant). Record the exact pass count; it is the baseline.

### Task 2: Disambiguate the overlapping ToolSpec descriptions
- **ACTION**: Rewrite the `description` argument of each `ToolSpec` in
  `REGISTRY` (`backend/app/agent/tools.py:300-379`). Touch descriptions only —
  names, schemas, and callables stay byte-identical.
- **IMPLEMENT**: Three overlapping pairs need explicit *not*-clauses:
  - `get_recommendation` — "…what the engine **recommends changing to**, with
    reason code, risk level, confidence and USD exposure. Use for 'why', 'what
    does the engine say', 'should this change'. NOT for the item's present
    stock levels — that is `get_current_values`."
  - `get_current_values` — "…the item's **present** Max/ROP/Min as loaded from
    the source workbook. Use for 'what is it now', 'current max'. NOT for what
    the engine recommends — that is `get_recommendation`."
  - `top_exposure` — "…the **N highest-USD** items still requiring review,
    ranked. Use only when the question is about value at risk or 'what should I
    look at first'. For any other filter or listing use `list_review_queue`."
  - `list_review_queue` — "…**list or count** scored items, optionally filtered
    by risk_level, action or reason_code, with workflow status. Use for 'show
    the queue', 'which items are High risk', 'how many Decrease items'. NOT for
    whole-batch totals — that is `batch_summary`."
  - `batch_summary` — "…**whole-batch** counts by status and total pending USD
    exposure. Use for 'how is the batch doing'. NOT for a filtered subset or a
    per-item list — that is `list_review_queue`."
  - Leave `get_item_history`, `get_item_notes`, `explain_rules`,
    `recall_context`, `propose_change` semantically as-is; add a one-clause
    "Use for…" only where absent. `recall_context`'s existing NOT-clause
    ("NOT a system record and never a basis for proposing a value") is already
    correct — keep it verbatim.
- **MIRROR**: TOOLSPEC_DESCRIPTION — implicitly-concatenated string literals,
  same positional-arg layout, reuse `_ITEM` / `_BATCH` / `_STOCK`.
- **IMPORTS**: none new.
- **GOTCHA**: Descriptions ship to the provider on **every** turn
  (`tools.py:384-386` → `loop.py:59,66` → `ToolSpec.to_openai()` at
  `provider.py:38-42`). Keep each under ~40 words; this is per-request token
  cost on a loop that can make 6 provider calls (`loop.py:33`
  `MAX_TOOL_CALLS = 5`, plus the final tool-less turn).
- **VALIDATE**: `cd backend && python -m pytest tests/test_agent_boundary.py -q`
  → all pass. `test_tool_specs_exclude_writes_when_disallowed` (line 189)
  asserts names, not descriptions, so it must stay green.

### Task 3: Add the routing table and output contract to `SYSTEM`
- **ACTION**: Edit `SYSTEM` in `backend/app/agent/prompts.py:9-44`.
- **IMPLEMENT**: Keep the existing four blocks (identity, division of labour,
  "What you do", "Hard rules") and the `Style:` line. Insert two new blocks
  between "What you do" and "Hard rules":
  ```
  Choosing a tool
  - "why / should this change / what does the engine say" -> get_recommendation
  - "what is it now / current max" -> get_current_values
  - "past decisions" -> get_item_history    "notes / what was said" -> get_item_notes
  - "what should I look at first / value at risk" -> top_exposure
  - "show / list / count items, filtered" -> list_review_queue
  - "how is the batch doing" -> batch_summary   "thresholds / rules" -> explain_rules
  If two tools could fit, call the narrower one first. If none fits, say you do
  not know -- do not call the nearest tool and answer around it.

  Answer shape
  - Lead with the answer in one sentence.
  - Then the evidence: item id, reason code, and the numbers you were given.
  - If you staged a proposal, end by saying it is not applied yet.
  ```
- **MIRROR**: existing `SYSTEM` style — `"""\` opener, backslash-continued
  lines to stay inside the column limit, numbered hard rules, terse imperative
  voice. Do **not** renumber or reword the existing hard rules 1-5.
- **IMPORTS**: none.
- **GOTCHA**: The module docstring (`prompts.py:1-7`) is the governing
  constraint: *"Where the two overlap … the code is the control and the prompt
  is only there to stop the model wasting a turn."* So the routing table is a
  hint, never a guarantee. The real guarantees stay in code —
  `loop.py:87-91` (`not ctx.sources` → `DONT_KNOW`) and `tools.py:255-260`
  (`propose_change` number check). Do not phrase any new prompt line as if it
  were enforced.
- **VALIDATE**: `cd backend && python -m pytest tests/ -q -k "chat or agent"`
  → no regressions. `EchoProvider` ignores `SYSTEM` entirely, so this task
  cannot move the golden pass rate — its effect is only on `NyraProvider`. That
  asymmetry is expected and is why Task 4 exists.

### Task 4: Reach the unreachable tools in `EchoProvider._route`
- **ACTION**: Edit `_route` (`backend/app/llm/echo.py:55-90`) and the module
  regexes at lines 22-27.
- **IMPLEMENT**: Preserve first-match-wins ordering, and place the *narrower*
  patterns before the broader ones:
  1. Keep the `propose_change` branch first, but require a write verb **and** a
     quantity **and** no history/notes keyword, so "change the history for
     100005" cannot fall into it.
  2. Keep `get_recommendation` (`why|explain|reason`) next.
  3. `get_item_history` before the new current-values branch — "history" is the
     narrower signal.
  4. `get_item_notes` — unchanged.
  5. **NEW** `get_current_values`: an item id plus a present-tense stock word —
     `current|currently|right now|now|today|at the moment` combined with
     `max|maximum|rop|min|minimum|level|stock`, and no write verb. Add a
     `CURRENT_RE` / `LEVEL_RE` module constant pair beside `ITEM_RE`.
  6. `top_exposure` — relax to `top|highest|biggest|most` **or** `exposure`,
     still combined with `risk|exposure|value|expensive|first`.
  7. **NEW** `list_review_queue`: `queue|list|show|which items|how many` with an
     optional filter extracted from the question — `risk_level` from
     `High|Medium|Low`, `action` from `Increase|Maintain|Decrease` (title-cased
     to match the schema `enum` at `tools.py:341-344`). Pass only the filters
     actually present.
  8. `explain_rules`, `batch_summary` — unchanged, and they stay **last** so the
     broad `summary|how many|status` words no longer swallow a filtered-list
     question.
  9. **NEW** `recall_context`: `remember|recall|prefer|usually|last time`
     without an item id, `{"query": q}`.
  Gate every new branch on `in available` the way the `propose_change` branch
  does at `echo.py:61` — the new tools are read-only so they are always
  available, but the guard keeps the ladder uniform if `specs()` ever narrows.
  Then add `_render` branches for the new tools:
  ```python
  if tool == "get_current_values":
      return (f"Item {data['item_id']} current: max {data['current_max']}/"
              f"rop {data['current_rop']}/min {data['current_min']} "
              f"(stockroom {data['stockroom_id']})")

  if tool == "list_review_queue":
      rows = data.get("items", [])
      lines = [f"{r['item_id']} {r['status']} {r['action']} "
               f"{r['risk_level']} (${r['exposure_usd']:,.0f})" for r in rows]
      return (f"Review queue for batch {data['batch_id']} "
              f"({len(rows)} items): " + "; ".join(lines))

  if tool == "recall_context":
      hits = data.get("results", [])
      return ("Recalled working context (not a system record): "
              + "; ".join(str(h) for h in hits))
  ```
- **MIRROR**: ECHO_ROUTE_LADDER and ECHO_RENDER_BRANCH above.
- **IMPORTS**: none new — `re` and `json` are already imported at
  `echo.py:17-18`.
- **GOTCHA**: `_render` is reached only when `data` is a dict without `_empty`
  and without `error` (`echo.py:106-112`). Tools returning `EMPTY` render as
  `""`, which makes the loop's `not answer` check fire and produce `DONT_KNOW` —
  correct behaviour, do not special-case it. Also: `recall_context` returns
  `EMPTY` unless `MEM0_ENABLED=1` (`tools.py:212-226`), so its route is
  reachable but its render is unexercised in CI; that is acceptable, and
  `test_agent_boundary.py:228-234` must keep passing (mem0 stays unimported).
- **VALIDATE**: `cd backend && python -m pytest tests/test_chat_routing.py -q`
  → all 16 cases pass, including both `None` cases.

### Task 5: Extend the `DONT_KNOW` capability list
- **ACTION**: Edit `DONT_KNOW` in `backend/app/agent/prompts.py:47-52`.
- **IMPLEMENT**: Keep the leading sentence *verbatim* —
  `"I don't know — no data source matches that question. "` — and extend the
  second sentence to name the two newly-reachable capabilities, e.g. add
  `"show an item's current Max/ROP/Min ('current max for <id>')"` and
  `"list the review queue ('show high risk items')"` to the existing four
  examples.
- **MIRROR**: existing implicit-concatenation layout and the `'<example>'`
  quoting style already used for each capability.
- **IMPORTS**: none.
- **GOTCHA**: The comment at `prompts.py:46` says *"The string is asserted in
  tests; keep it verbatim."* Two assertions depend on it —
  `test_agent_boundary.py:175` and `test_api_flow.py:136`, both
  `assert "I don't know" in r["answer"]`. Keeping the em-dash first sentence
  intact satisfies both, so no test edit should be needed. Run the suite to
  confirm before touching any test file; if something does break, fix the
  assertion to match the new string rather than reverting the prompt.
- **VALIDATE**: `cd backend && python -m pytest tests/ -q` → full suite green.

### Task 6: Measure and decide on few-shot
- **ACTION**: Re-run the golden test and compare against the Task 1 baseline.
- **IMPLEMENT**: Record baseline vs final pass count in the PR description. If
  any case still fails after Tasks 2-5, add *only* the minimum extra pattern or
  the single few-shot exchange that fixes it — then re-run.
- **MIRROR**: N/A — verification step.
- **IMPORTS**: none.
- **GOTCHA**: Do not "fix" a failing case by widening the golden expectation.
  If a phrasing genuinely has two defensible tools, delete the case and note why
  in a comment; a golden set that asserts the wrong thing is worse than a
  smaller one.
- **VALIDATE**: `cd backend && python -m pytest tests/ -q` → full suite green,
  16/16 routing cases pass.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_routes_to_expected_tool` | `"why item 100005?"` | `get_recommendation` in tool_calls | no |
| ↳ | `"what is item 100005 max right now?"` | `get_current_values` | **yes — currently DONT_KNOW** |
| ↳ | `"current min for 100005"` | `get_current_values` | yes — no "max" keyword |
| ↳ | `"show me the review queue"` | `list_review_queue` | **yes — currently unreachable** |
| ↳ | `"list the high risk items"` | `list_review_queue` + `risk_level=High` | yes — filter extraction |
| ↳ | `"which items are set to Decrease"` | `list_review_queue` + `action=Decrease` | yes — enum case-match |
| ↳ | `"highest value items left to review"` | `top_exposure` | yes — no literal "top" |
| ↳ | `"batch summary"` | `batch_summary`, **not** `list_review_queue` | yes — precedence |
| ↳ | `"increase item 100005 a little"` | no `propose_change` | yes — vague quantity |
| ↳ | `"what is the capital of France"` | no tool, `DONT_KNOW` | yes — off-domain |
| `test_tool_specs_exclude_writes_when_disallowed` (existing) | `specs(allow_writes=False)` | `propose_change` absent | regression guard |
| `test_no_source_means_dont_know` (existing) | off-domain question | `"I don't know"` in answer, `sources == []` | regression guard |
| `test_propose_change_rejects_unstated_numbers` (existing) | `"bump a bit"`, `proposed_max=7` | `ToolError` "does not appear" | regression guard |

### Edge Cases Checklist
- [ ] Empty question — `chat.py:54` does `body.question.strip()`; confirm an
      empty string routes to nothing rather than 500
- [ ] Two intents in one message (`"why 100005 and set its max to 3"`) —
      precedence must be deterministic, and the number check must still apply
- [ ] Item id present but not in the batch (`999999`) — `_resolve` → `EMPTY`
      → `DONT_KNOW`; already covered by `test_agent_boundary.py:160-166`
- [ ] Filter word with no item id (`"high risk"`) — must reach
      `list_review_queue`, not `get_recommendation` with a missing `item_id`
- [ ] Write phrasing from a `VIEWER` — `propose_change` not offered
      (`chat.py:52`), so the ladder's `in available` guard must hold; covered by
      `test_agent_boundary.py:181-186`
- [ ] Filter enum case (`"decrease"` lowercase) — must be title-cased or
      `list_review_queue` silently drops the filter (`tools.py:157-158`)
- [ ] Invalid types — `dispatch` already returns
      `{"error": "bad arguments for ..."}` on `TypeError` (`tools.py:406-407`)
- [ ] Concurrent access — N/A, per-request connection (`chat.py:39,72`)
- [ ] Network failure — N/A, CI runs `EchoProvider` with no egress
- [ ] Permission denied — covered by the `VIEWER` cases above

---

## Validation Commands

### Static Analysis
```bash
cd backend && python -m compileall -q app/agent app/llm
```
EXPECT: no output. (No `ruff` or `mypy` in `requirements.txt`; do not add one
for this change.)

### Unit Tests — affected area
```bash
cd backend && python -m pytest tests/test_chat_routing.py tests/test_agent_boundary.py -q
```
EXPECT: all pass, 16/16 routing cases.

### Full Test Suite
```bash
cd backend && python -m pytest tests/ -q
```
EXPECT: no regressions vs the pre-change baseline. Capture that baseline
**before** Task 1 — `tests/test_real_data.py` may skip or fail depending on
whether the real extract is present locally, and you need to know which
failures you inherited.

### Database Validation
N/A — no schema change, no migration. `conversation_turn` is read, never
altered.

### Browser Validation
```bash
cd backend && LLM_BASE_URL= uvicorn app.main:create_app --factory --reload
# then, in another shell:
curl -s localhost:8000/chat -H 'X-User: alice' -H 'X-Role: engineer' \
  -H 'Content-Type: application/json' \
  -d '{"question":"show me the high risk items"}' | python -m json.tool
```
EXPECT: `answer` names items, `sources` non-empty with
`"type": "recommendation_result"`. Requires a scored batch to exist in whichever
DB the process is pointed at.

### Manual Validation
- [ ] With `LLM_BASE_URL` set to the real endpoint, ask the six "Choosing a
      tool" phrasings from the `SYSTEM` table and confirm each reaches the
      named tool (check `conversation_turn.tool_calls`). This is the only check
      that exercises Task 3 at all.
- [ ] Ask one deliberately off-domain question and confirm `DONT_KNOW`
- [ ] Ask `"bump item 100005 a bit"` and confirm nothing is staged
- [ ] Confirm the frontend chat view renders the new `list_review_queue`
      answers without layout breakage (the multi-item string can get long)

---

## Acceptance Criteria
- [ ] All 6 tasks completed
- [ ] `tests/test_chat_routing.py` exists and is 16/16 green
- [ ] Full backend suite shows no regressions vs the recorded baseline
- [ ] All 10 registry tools are reachable from `EchoProvider._route`
- [ ] The 3 overlapping tool pairs each carry an explicit NOT-clause
- [ ] `SYSTEM` carries an intent→tool table and an answer-shape contract
- [ ] `DONT_KNOW` still opens with the verbatim first sentence
- [ ] Matches the After diagram for all three example questions

## Completion Checklist
- [ ] Code follows discovered patterns (`ToolSpec` layout, echo ladder, test structure)
- [ ] Error handling unchanged — `ToolError` / `dispatch` paths untouched
- [ ] Module docstrings/comments justify decisions, per MODULE_DOCSTRING_STYLE
- [ ] Tests follow `test_api_flow.py` / `test_agent_boundary.py` conventions
- [ ] No hardcoded item ids outside test fixtures
- [ ] No new dependency in `requirements.txt`
- [ ] No safety control weakened: `loop.py:87-91` and `tools.py:255-260` byte-identical
- [ ] No unnecessary scope additions (no `format.py`, no sampling config, no new tools)
- [ ] Self-contained — no codebase search needed during implementation

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Widening the echo ladder makes it route where it should refuse, softening `DONT_KNOW` | Medium | High — PRD section 8 hard control | The two `None` golden cases; `test_agent_boundary.py:171-176` untouched; `loop.py:87-91` unchanged |
| New `list_review_queue` route steals `batch_summary` questions | Medium | Low | Ordered ladder with `batch_summary` last + the `"batch summary"` golden case |
| Golden tests measure the regex ladder, not model judgement — Tasks 2-3 stay unmeasured in CI | High | Medium | Accepted and documented in the test docstring. Manual validation covers `NyraProvider`. Real-provider eval is out of scope (no egress). |
| Longer tool descriptions raise per-turn token cost | High | Low | ~40-word cap per description; loop bounded at `MAX_TOOL_CALLS = 5` |
| A relaxed `propose_change` guard lets an invented number through | Low | Critical | The guard is in `tools.py:255-260` and is not edited. `test_propose_change_rejects_unstated_numbers` is the gate. |
| `DONT_KNOW` edit breaks a verbatim assertion | Low | Low | First sentence preserved; Task 5 validates the full suite before any test edit |

## Notes
- **Root cause of the reported pain, precisely.** `EchoProvider._route`
  (`echo.py:55-90`) has no branch that can return `get_current_values`,
  `list_review_queue`, or `recall_context`. Three of ten registered tools are
  dead offline, so every local run and every CI run silently reports "no
  routing problem" for a third of the tool surface. That is why this fix leads
  with a test.
- **Precedence bug worth naming.** The `batch_summary` branch at
  `echo.py:86-88` triggers on `how many` and `status`, which are precisely the
  words a filtered-list question uses ("how many high risk items are left").
  Moving it last is a behaviour change for those phrasings — intended, and
  covered by a golden case.
- **Why the prompt work is deliberately unmeasured in CI.** `EchoProvider`
  never reads `SYSTEM` (`echo.py:34-51` only inspects the last user message and
  the tool results). So Tasks 2-3 — the tool descriptions and the routing table
  — are exactly the parts CI cannot score, and Task 4 is the part it can. Both
  are needed: Task 4 makes the offline path honest, Tasks 2-3 make the real
  path good. Do not "improve" the golden test by teaching `EchoProvider` to
  parse `SYSTEM`; that would make the stub a model and the test a tautology.
- If a future pass does want real-provider scoring, the natural shape is
  `scripts/eval_chat.py` reusing `CASES` from `tests/test_chat_routing.py` with
  `LLM_BASE_URL` set — deliberately deferred, not designed here.
