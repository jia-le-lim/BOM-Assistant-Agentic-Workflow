# Plan: Dormant Rules from Natural Language

## Summary
An engineer can already ask the chatbot how much of the dormant tail the rules
cover, but cannot write a rule — `get_dormant_coverage` is read-only and
`propose_change` writes one item's stock levels, not `dormant_rule_config`.
This adds one write tool, `propose_dormant_rule`, so "keep all filter parts at
2" records an **unconfirmed** rule that a second person with approval rights
still has to confirm before it sizes anything.

## User Story
As a **BOM review engineer who works in the chat window**,
I want **to state a dormant stocking rule in plain language**,
So that **I do not have to leave the conversation, open `/config/dormant`, and
re-enter a decision I already articulated**.

## Problem → Solution
**Current**: `get_dormant_coverage` reports that N dormant rows are uncovered.
The engineer reads that, opens `/config/dormant`, and fills a form. The chatbot
that told them about the gap cannot help them close it. "Keep all filter parts
at max 2" classifies to no branch that can act and answers "I don't know".

**Desired**: the same sentence calls `propose_dormant_rule(scope="category",
match_key="filter", policy="fixed_qty", fixed_qty=2)`, which writes a row with
`confirmed=0`. The engine does not read it (`load_rules` is
`WHERE confirmed=1`), and a different person with `APPROVE_ROLES` confirms it in
the console exactly as they do today.

## Metadata
- **Complexity**: Medium
- **Source PRD**: N/A — standalone, follows `completed/chatbot-langgraph-tool-router.plan.md`
- **PRD Phase**: standalone
- **Estimated Files**: 7 updated (0 created)

---

## Decisions Locked Before Implementation

I suggested "behind an action card" in conversation. **Exploration changed
that** — record the reason so it is not re-argued:

| Decision | Choice | Why |
|---|---|---|
| Card or direct write? | **Direct write, `confirmed=0`** | `dormant_rules.load_rules()` is `WHERE confirmed=1`, and its docstring calls that "the safety property". `POST /config/dormant-rules/{id}/confirm` is `APPROVE_ROLES` **and** refuses the proposer (`rules_config.py:351`). The second gate already exists in the data model; an action card would be a third gate on a row that sizes nothing. This is precisely the shape `propose_change` → `pending_change` already has. |
| Which branch? | **`propose`** | It is in `WRITE_INTENTS`, so a read-only role is downgraded before the tool is ever offered. A dormant rule is a stocking decision, which is what that branch is for. |
| `fixed_qty` | **Verbatim numbers only** | A `fixed_qty` rule sets Min/ROP/Max for every matching dormant row (`dormant_rules.apply` returns `(q, q, q)`). That is a stock level, and hard rule 1 says the model never invents one. Mirror `propose_change`'s `INT_RE` check exactly. |
| `scope="default"` | **Refused from chat** | The default rule sizes every dormant row no category or item rule matches — the entire tail. Console-only. |
| `scope="category"` | **Validated against the live lexicon** | `part_category.categories()` lists what the confirmed lexicon can actually produce. An unvalidated key writes a rule that matches zero rows and looks confirmed. |

---

## THE CRITICAL GOTCHA

`POST /config/dormant-rules` (`rules_config.py:312`) is an upsert:

```sql
ON CONFLICT(scope, match_key) DO UPDATE SET
  policy=excluded.policy, fixed_qty=excluded.fixed_qty,
  set_by=excluded.set_by,
  confirmed=0, confirmed_by=NULL, updated_at=datetime('now')
```

`confirmed=0` on conflict. `test_reproposing_resets_the_confirmation`
(`test_dormant_rules.py:161`) asserts this is deliberate for the console: an
engineer editing a rule must have it re-approved.

**But it makes a naive chat tool stock-moving.** If a confirmed
`("category", "filter")` rule is live and the model proposes the same
`(scope, match_key)` — because the engineer restated it, or because it
misparsed — that live rule silently drops to `confirmed=0`, `load_rules()`
stops returning it, and **the next `/run-recommendation` sizes every filter part
differently**. No one confirmed that change; a sentence caused it.

**The tool must therefore refuse to overwrite a confirmed rule** and tell the
engineer to change it in the console. `replace=True` exists as an explicit
opt-in, and it is the only path that may reset a confirmed row.

---

## UX Design

### Before
```
engineer: "how much of the dormant tail do our rules cover?"
NYRA:     "412 of 1,203 dormant rows matched (34.2%) ..."
engineer: "ok, keep all the filter parts at 2"
NYRA:     "I don't know — no data source matches that question."
          [engineer opens /config/dormant and fills the form by hand]
```

### After
```
engineer: "ok, keep all the filter parts at 2"
     classify -> propose branch
     tool: propose_dormant_rule(scope="category", match_key="filter",
                                policy="fixed_qty", fixed_qty=2)
NYRA: "Recorded a proposed rule: dormant parts in category 'filter' hold a
       fixed quantity of 2. It is NOT active — it sizes nothing until someone
       with approval rights confirms it, and it takes effect on the next
       engine run, not on any batch already scored."

engineer: "keep the filter parts a bit higher"
NYRA: "I need the exact quantity — I don't set stock levels. Say the number
       you want, e.g. 'keep filter parts at 3'."

engineer: "keep all dormant parts at 5"        <- default scope
NYRA: "A rule with no category or item applies to every dormant row that no
       other rule matches. That one is set in Config -> Dormant Rules, not
       here."
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Writing a rule | `/config/dormant` form only | Also chat, item + category scope | Same table, same `confirmed=0` |
| Confirming a rule | Console, `APPROVE_ROLES`, not the proposer | Unchanged | Deliberately not exposed to chat |
| Default-scope rule | Console form | Console form only | Chat refuses with a pointer |
| Editing a live rule | Console (resets to unconfirmed) | Chat refuses unless `replace=True` | Prevents a sentence un-confirming a live rule |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/app/dormant_rules.py` | 1-124 | `POLICIES`, `SCOPES`, `load_rules` (`WHERE confirmed=1`), `resolve`, `apply`. The module docstring states the propose→confirm→engine contract. |
| P0 | `backend/app/agent/tools.py` | `propose_change` | The verbatim-number enforcement to mirror line for line |
| P0 | `backend/app/routers/rules_config.py` | 312-345 | The upsert, its `confirmed=0` on conflict, and the audit call |
| P0 | `backend/app/schemas.py` | 123-147 | `DormantRuleRequest` and its `model_validator` — the two coherence rules to reproduce |
| P1 | `backend/app/part_category.py` | 70-125 | `load_rules`, `categorise`, `categories()` — how to validate a category key |
| P1 | `backend/app/agent/prompts.py` | `PROPOSE_SYSTEM` | The branch prompt to extend |
| P1 | `backend/app/llm/echo.py` | `_classify`, `_route` | Every new tool needs a branch here or CI cannot reach it |
| P2 | `backend/tests/test_dormant_rules.py` | 1-200 | Test voice, `BODY` fixture, the confirm-gate assertions |
| P2 | `backend/tests/test_chat_graph.py` | all | Where the chat-side tests live and how they read state back |

## External Documentation

None. Every integration point is an existing module in this repo.

**No external research needed — feature uses established internal patterns.**

---

## Patterns to Mirror

### VERBATIM_NUMBER_ENFORCEMENT
```python
# SOURCE: backend/app/agent/tools.py  (propose_change)
    bid = batch_id or ctx.batch_id
    stated = set(INT_RE.findall(ctx.question))
    proposed = {"proposed_max": proposed_max, ...}
    given = {k: v for k, v in proposed.items() if v is not None}
    if not given:
        raise ToolError("propose_change needs at least one of ...")

    for key, val in given.items():
        if str(val) not in stated:
            raise ToolError(
                f"Refusing to stage {key}={val}: that number does not appear "
                f"in the engineer's message. Ask them to state the value "
                f"explicitly. (The assistant does not calculate stock levels.)")
```
`INT_RE = re.compile(r"\d+")` is module-level near the top of `tools.py`. The
check is against `ctx.question` — the engineer's own words for this turn.

### ERROR_HANDLING
```python
# SOURCE: backend/app/agent/tools.py
class ToolError(Exception):
    """Rejected at the tool boundary; surfaced to the model, not raised to HTTP."""
```
Every rejection is a `ToolError` with a sentence the model can relay. Never an
`HTTPException` inside a tool.

### ROLE_GATE
```python
# SOURCE: backend/app/agent/tools.py  (added by the router work)
def _require_review_role(ctx: "ToolContext", what: str) -> None:
    if ctx.actor.get("role") not in REVIEW_ROLES:
        raise ToolError(f"{what} needs a review role; this account has "
                        f"read-only access.")
```

### WRITE_TOOL_SHAPE
```python
# SOURCE: backend/app/agent/tools.py  (propose_change tail)
    pending_id = ctx.conn.insert_returning(
        "INSERT INTO pending_change (...) VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
        (...), "pending_change")

    ctx.sources.append({"type": "pending_change", "pending_id": pending_id,
                        "item_id": item_id, "status": "pending"})
    return {"pending_id": pending_id, ...,
            "note": "staged only; a human must confirm before this can be "
                    "reviewed, approved or exported"}
```
Append to `ctx.sources` — the only channel out of a tool that reaches the HTTP
response — and return a `note` stating plainly that nothing is applied.

### REPOSITORY_PATTERN
```python
# SOURCE: backend/app/routers/rules_config.py:315-327
        conn.execute(
            "INSERT INTO dormant_rule_config (scope, match_key, "
            "policy, fixed_qty, set_by, confirmed, updated_at) "
            "VALUES (?,?,?,?,?,0,datetime('now')) "
            "ON CONFLICT(scope, match_key) DO UPDATE SET "
            "policy=excluded.policy, fixed_qty=excluded.fixed_qty, "
            "set_by=excluded.set_by, "
            "confirmed=0, confirmed_by=NULL, updated_at=datetime('now')",
            (body.scope, body.match_key, body.policy,
             body.fixed_qty, actor["user"]))
```
`?` placeholders always (`db._translate` rewrites for Postgres).
`datetime('now')` for timestamps.

### AUDIT_PATTERN
```python
# SOURCE: backend/app/routers/rules_config.py:328-330
        audit(conn, actor, "POST", "/config/dormant-rules", "dormant_rule",
              f"{body.scope}:{body.match_key}", body.model_dump())
```
`audit` is imported function-locally inside agent tools to avoid the import
cycle — see `run_assist` in `tools.py`.

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_dormant_rules.py:147-158
def test_propose_is_pending_and_confirm_needs_a_second_person(client):
    r = client.post("/config/dormant-rules", json=BODY, headers=ENG)
    assert r.json()["confirmed"] is False
    assert client.post(f"/config/dormant-rules/{rule_id}/confirm",
                       headers=ENG).status_code == 403
    assert client.post(f"/config/dormant-rules/{rule_id}/confirm",
                       headers=SENIOR).status_code == 200
```
```python
# SOURCE: backend/tests/test_dormant_rules.py:178-186
def test_load_rules_reads_confirmed_only(client, db_file):
    from app.db import get_conn
    client.post("/config/dormant-rules", json=BODY, headers=ENG)
    conn = get_conn()
    try:
        assert DR.load_rules(conn) == []
    finally:
        conn.close()
```
Assert the safety property by reading state back, not by trusting the response.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/app/agent/tools.py` | UPDATE | `propose_dormant_rule` + registry entry + add to `INTENT_TOOLS["propose"]` |
| `backend/app/agent/prompts.py` | UPDATE | `PROPOSE_SYSTEM` gains the dormant-rule case and its refusals |
| `backend/app/llm/echo.py` | UPDATE | Classify signal, `_route` branch, `_render` case |
| `backend/tests/test_dormant_rules.py` | UPDATE | The chat path's tests, beside the console path's |
| `backend/tests/test_chat_routing.py` | UPDATE | Two routing cases |
| `backend/tests/test_chat_graph.py` | UPDATE | Only if a subset assertion needs the new name; verify |
| `docs/CURRENT_CODEBASE_END_TO_END.md` | UPDATE | §20.2 tool list, and the dormant section's chat path |

## NOT Building

- **No confirm-from-chat.** `POST /config/dormant-rules/{id}/confirm` stays
  console-only and stays `APPROVE_ROLES`. The whole value of the gate is that a
  second human looks at it.
- **No delete-from-chat.** `DELETE /config/dormant-rules/{id}` is `APPROVE_ROLES`.
- **No default-scope rule from chat.** It sizes the entire dormant tail.
- **No new endpoint.** The tool writes the same table the existing endpoint writes.
- **No action card.** The `confirmed=0` row is already the staging state.
- **No part-category rules from chat.** `part_category_config` is a different
  table with the same propose/confirm shape; it is a separate feature.
- **No re-scoring.** Rules bite at the next `/run-recommendation`; the tool must
  never trigger one.

---

## Step-by-Step Tasks

### Task 1: The tool
- **ACTION**: Add `propose_dormant_rule` to `backend/app/agent/tools.py`, in the
  gated-tools section beside `run_assist` (it writes, so it does not belong in
  the read block).
- **IMPLEMENT**:
  ```python
  def propose_dormant_rule(ctx: ToolContext, scope: str, policy: str,
                           match_key: str = "", fixed_qty: int | None = None,
                           replace: bool = False) -> dict:
  ```
  In order:
  1. `_require_review_role(ctx, "Proposing a dormant rule")`.
  2. Reject `scope` not in `("item", "category")` — name the scopes and point
     `default` at the console.
  3. Reject `policy` not in `dormant_rules.POLICIES`.
  4. Require a non-empty `match_key` (mirrors `DormantRuleRequest.coherent`).
  5. `policy == "fixed_qty"` requires `fixed_qty is not None` (same validator),
     **and** `str(fixed_qty)` must appear in `INT_RE.findall(ctx.question)` —
     the VERBATIM_NUMBER_ENFORCEMENT pattern, with `propose_change`'s wording.
     Also reject `fixed_qty` outside `0..10_000` (the `Field` bounds on
     `DormantRuleRequest`).
  6. Reject a `fixed_qty` on a policy that takes none (`hold_current`, `zero`) —
     silently dropping it would record something the engineer did not ask for.
  7. **Validate `match_key`:**
     - `scope="category"` → must be in `category_names(rules)` where
       `rules, _ = load_category_rules(ctx.conn)`. On a miss, raise a `ToolError`
       **listing the available categories** so the model can offer them.
     - `scope="item"` → `_resolve(ctx, ctx.batch_id, match_key, None)` must find
       the row, so a typo'd part number cannot become a permanent rule. With no
       scored batch, accept the id and say so in the `note`.
  8. **The confirmed-rule guard** (see THE CRITICAL GOTCHA):
     ```python
     existing = ctx.conn.execute(
         "SELECT rule_id, confirmed, policy, fixed_qty FROM dormant_rule_config "
         "WHERE scope=? AND match_key=?", (scope, match_key)).fetchone()
     if existing is not None and existing["confirmed"] and not replace:
         raise ToolError(
             f"A CONFIRMED rule already covers {scope} '{match_key}' "
             f"({existing['policy']}). Re-proposing it would un-confirm it and "
             f"change what the next engine run sizes. Ask the engineer to "
             f"confirm they want it replaced, then call this again with "
             f"replace=true -- or send them to Config > Dormant Rules.")
     ```
  9. Upsert with the REPOSITORY_PATTERN SQL above, `set_by=ctx.actor["user"]`.
  10. `audit(...)` mirroring AUDIT_PATTERN, endpoint `"/chat"`, entity
      `"dormant_rule"`, key `f"{scope}:{match_key}"`.
  11. Read the `rule_id` back, append
      `{"type": "dormant_rule", "rule_id": ..., "scope": ..., "match_key": ...,
      "confirmed": False}` to `ctx.sources`, and return the rule plus:
      ```python
      "note": "proposed only; it sizes nothing until a different person with "
              "approval rights confirms it, and it takes effect on the next "
              "engine run -- a batch already scored keeps the numbers its "
              "reviewer saw"
      ```
- **MIRROR**: VERBATIM_NUMBER_ENFORCEMENT, ROLE_GATE, WRITE_TOOL_SHAPE,
  REPOSITORY_PATTERN, AUDIT_PATTERN.
- **IMPORTS**: function-local, mirroring `run_assist`:
  ```python
  from .. import dormant_rules
  from ..audit import audit
  from ..part_category import categories as category_names
  from ..part_category import load_rules as load_category_rules
  ```
  `INT_RE`, `ToolError`, `_resolve`, `_require_review_role` and `REVIEW_ROLES`
  are already in the module.
- **GOTCHA**:
  - The confirmed-rule guard is the reason this tool is not three lines. Without
    it a restated sentence un-confirms a live rule and moves stock at the next
    scoring run.
  - Do **not** call `dormant_rules.seed()` here. The listing endpoint seeds; a
    write path that also seeds would create the default row as a side effect of
    proposing something unrelated.
  - `insert_returning` needs a table in `IDENTITY_PK`, and this is an upsert, not
    a plain insert. Do the `execute` then `SELECT rule_id`, exactly as the
    endpoint does at `rules_config.py:333-337`.
  - `dormant_rules` imported function-locally: `agent.tools` is imported by
    `assist/chain.py`, and module-level imports here have already caused a cycle
    once.
- **VALIDATE**: `from app.agent import tools as T; print(len(T.REGISTRY))` → `22`.

### Task 2: Register it and put it in the propose branch
- **ACTION**: Add the `REGISTRY` entry and extend `INTENT_TOOLS["propose"]`.
- **IMPLEMENT**:
  ```python
      "propose_dormant_rule": (ToolSpec(
          "propose_dormant_rule",
          "Record a proposed DORMANT STOCKING RULE: how much stock parts with "
          "no consumption keep. Scope 'item' for one part, 'category' for a "
          "part category. Policy 'hold_current' keeps today's level, "
          "'fixed_qty' a stated quantity, 'zero' the engine's own answer. Use "
          "for 'keep all filter parts at 2', 'hold the current level for "
          "100005'. This does NOT activate the rule -- a second person "
          "confirms it. NOT for changing one item's Max/ROP/Min on a scored "
          "batch, which is propose_change.",
          {"type": "object", "properties": {
              "scope": {"type": "string", "enum": ["item", "category"]},
              "match_key": {"type": "string",
                            "description": "item id, or category name"},
              "policy": {"type": "string",
                         "enum": ["hold_current", "fixed_qty", "zero"]},
              "fixed_qty": {"type": "integer",
                            "description": "Required for fixed_qty; must be a "
                                           "number the engineer stated"},
              "replace": {"type": "boolean",
                          "description": "Only true when the engineer has "
                                         "agreed to replace a CONFIRMED rule"}},
           "required": ["scope", "match_key", "policy"]}), propose_dormant_rule),
  ```
  and in `INTENT_TOOLS`:
  ```python
      "propose": frozenset({"propose_change", "propose_dormant_rule",
                            "get_current_values", "get_recommendation",
                            "get_dormant_coverage"}),
  ```
  `get_dormant_coverage` joins the branch so the model can answer "how many rows
  would that cover?" without a second turn. It carries its own role gate.
- **MIRROR**: the "Use for … NOT for … that is X" contrast every registry entry
  uses — `test_chat_routing.py` measures exactly that contrast.
- **IMPORTS**: none new.
- **GOTCHA**: `propose` is in `WRITE_INTENTS`, so a viewer is downgraded to
  `lookup` before this tool is offered. That is the intended first gate; the
  tool's own `_require_review_role` is the second.
- **VALIDATE**: `test_every_intent_group_names_real_tools` and
  `test_every_tool_is_reachable_from_some_branch` still pass.

### Task 3: Extend the propose prompt
- **ACTION**: Add a dormant-rule section to `PROPOSE_SYSTEM` in
  `backend/app/agent/prompts.py`.
- **IMPLEMENT**: after the existing "This branch only" bullets, add:
  ```
  Two different things can be recorded here, and they are not interchangeable
  - ONE ITEM's Max/ROP/Min on the current batch -> propose_change
  - HOW MUCH DORMANT PARTS KEEP, as a standing rule -> propose_dormant_rule
  A dormant rule is about parts with no consumption. It applies to every
  matching part, on every future batch, so it is the bigger of the two.

  Dormant rules
  - `fixed_qty` needs a number the engineer wrote. "a bit more" is not a
    quantity; ask for the exact one.
  - `hold_current` and `zero` take no quantity. Do not invent one.
  - A rule with no category or item applies to the whole dormant tail. You
    cannot record that one -- send them to Config > Dormant Rules.
  - A proposed rule activates NOTHING. Say so, and say a different person with
    approval rights has to confirm it, and that it takes effect on the next
    engine run rather than on a batch already scored.
  - If a CONFIRMED rule already covers that scope, do not replace it on your own
    initiative. Report what is there and ask.
  ```
- **MIRROR**: the existing branch prompts' `This branch only` / `Choosing a tool`
  shape, and the module docstring's rule — *"The prompt states the rules; the
  code enforces them… Never rely on wording alone for a safety property."*
- **IMPORTS**: none.
- **GOTCHA**: every rule above is enforced in Task 1. The prompt exists to save a
  rejected turn, not to be the control.
- **VALIDATE**: `pytest backend/tests/test_agent_boundary.py -q`.

### Task 4: Teach EchoProvider the phrasing
- **ACTION**: `backend/app/llm/echo.py` — classify signal, `_route` branch,
  `_render` case.
- **IMPLEMENT**:
  1. Module-level, beside the other branch regexes:
     ```python
     DORMANT_RULE_RE = re.compile(
         r"\b(?:dormant|no consumption|not moving|non[- ]moving)\b", re.IGNORECASE)
     KEEP_RE = re.compile(r"\b(?:keep|hold|stock|maintain)\b", re.IGNORECASE)
     CATEGORY_AT_RE = re.compile(
         r"\ball\s+(?:the\s+)?([a-z][a-z0-9_-]{2,30})\s+(?:parts?|items?)\b",
         re.IGNORECASE)
     HOLD_CURRENT_RE = re.compile(
         r"\b(?:hold|keep)\b[^.]{0,30}\b(?:current|where it is|as is|today)\b",
         re.IGNORECASE)
     ZERO_POLICY_RE = re.compile(r"\b(?:zero|nothing|no stock|don'?t stock)\b",
                                 re.IGNORECASE)
     ```
  2. In `_classify`, **after** the `REVIEW_ACTION_RE` check and **before**
     `ASSIST_RE` / `ADVISORY_RE`:
     ```python
     if (DORMANT_RULE_RE.search(q) or CATEGORY_AT_RE.search(q)) and KEEP_RE.search(q):
         return "propose"
     ```
     `DORMANT_RULE_RE` overlaps `ADVISORY_RE`'s `dormant`, so "how much do
     dormant rules cover" must still classify `advisory` — the `KEEP_RE`
     conjunct is what separates asking from instructing.
  3. In `_route`, ahead of the `get_dormant_coverage` branch (which matches the
     bare word `dormant`):
     ```python
     if "propose_dormant_rule" in available and KEEP_RE.search(q):
         cat = CATEGORY_AT_RE.search(q)
         rule_args: dict = {}
         if item:
             rule_args = {"scope": "item", "match_key": item.group(1)}
         elif cat:
             rule_args = {"scope": "category", "match_key": cat.group(1).lower()}
         if rule_args:
             qty = QTY_RE.search(q)
             if HOLD_CURRENT_RE.search(q):
                 rule_args["policy"] = "hold_current"
             elif ZERO_POLICY_RE.search(q):
                 rule_args["policy"] = "zero"
             elif qty:
                 value = int(next(g for g in qty.groups() if g))
                 if not item or str(value) != item.group(1):
                     rule_args["policy"] = "fixed_qty"
                     rule_args["fixed_qty"] = value
             if "policy" in rule_args:
                 return ToolCall("c1", "propose_dormant_rule", rule_args)
     ```
  4. `_render` case:
     ```python
     if tool == "propose_dormant_rule":
         qty = data.get("fixed_qty")
         return (f"Recorded a proposed dormant rule for {data.get('scope')} "
                 f"'{data.get('match_key')}': {data.get('policy')}"
                 f"{'' if qty is None else f' of {qty}'}. It is not active — "
                 f"someone with approval rights must confirm it, and it applies "
                 f"from the next engine run.")
     ```
- **MIRROR**: `echo.py`'s own rule — *"First match wins, narrowest signal first"*
  — and every branch's `in available` guard.
- **IMPORTS**: none new.
- **GOTCHA**:
  - **Name collisions are a live hazard in this file.** `ACTION_RE` already
    existed and a new constant silently shadowed it during the router work,
    dropping the `action` filter from `list_review_queue`. Grep each new name
    before adding it — `KEEP_RE`, `ZERO_POLICY_RE`, `CATEGORY_AT_RE`,
    `HOLD_CURRENT_RE`, `DORMANT_RULE_RE`. `DORMANT_RE` already exists; do not
    reuse or shadow it.
  - The local variable is `rule_args`, not `args` — `args` is already bound in
    two other branches of `_route`.
  - `QTY_RE`'s `max <digits>` alternative can span words and capture the item id;
    the `str(value) != item.group(1)` guard is why `propose_change` has the same
    check.
  - Do not teach the stub to read the system prompt — `test_chat_routing.py:9-11`
    forbids it.
- **VALIDATE**: `pytest backend/tests/test_chat_routing.py -q`.

### Task 5: Tests
- **ACTION**: Add chat-path tests to `backend/tests/test_dormant_rules.py` under
  a new `# --- chat path ---` banner, and two cases to `test_chat_routing.py`.
- **IMPLEMENT**:
  1. `test_chat_proposal_is_unconfirmed_and_sizes_nothing` — `POST /chat` "keep
     all filter parts at 2"; the row exists with `confirmed=0` and
     `DR.load_rules(conn) == []`. The safety property, asserted the way
     `test_load_rules_reads_confirmed_only` asserts it.
  2. `test_chat_cannot_replace_a_confirmed_rule` — propose + confirm via the
     console, then call the tool directly for the same `(scope, match_key)`;
     `ToolError` mentioning "CONFIRMED", and the row is **still** `confirmed=1`.
     **Write this one first** — it is the regression that matters most.
  3. `test_chat_replaces_a_confirmed_rule_only_when_told` — same, `replace=True`;
     the row is now `confirmed=0`.
  4. `test_chat_refuses_a_quantity_the_engineer_did_not_state` — `ctx.question`
     "keep filter parts a bit higher", `fixed_qty=3` → `ToolError`, no row.
  5. `test_chat_refuses_a_default_scope_rule` — `scope="default"` → `ToolError`
     naming the console.
  6. `test_chat_refuses_an_unknown_category` — `match_key="widgets"` →
     `ToolError` listing the real categories.
  7. `test_chat_refuses_an_item_not_in_the_batch` — a 6-digit id absent from the
     scored batch.
  8. `test_chat_refuses_fixed_qty_without_a_quantity` — mirrors
     `test_fixed_qty_without_a_quantity_is_rejected_at_the_api`, at the tool.
  9. `test_viewer_cannot_propose_a_dormant_rule` — `VIEWER` → refused, no row.
  10. `test_a_proposed_rule_does_not_rescore_a_batch` — the scored batch's
      `recommendation_result` rows are identical before and after.
  In `test_chat_routing.py`, append:
  ```python
      ("keep all filter parts at 2",            "propose_dormant_rule"),
      ("hold the current level for dormant item 100005", "propose_dormant_rule"),
  ```
- **MIRROR**: TEST_STRUCTURE above; `conftest` fixtures and role headers; read
  state back from SQLite, never from the response alone.
- **IMPORTS**: `from conftest import ENG, SENIOR, VIEWER, upload`;
  `from app import dormant_rules as DR` is already at the top of the file.
- **GOTCHA**: the synthetic fixture's `item_desc` is `"TEST PART"` for every row
  (`conftest.make_row`), which categorises to `''` — **no category rule will
  match it**. Before writing the category tests, either seed a
  `part_category_config` row that matches the fixture text, or build a row with
  a description that hits an existing lexicon pattern (e.g. `"FILTER ASSY"` for
  the `filter` category). Check with
  `part_category.categorise(desc, load_rules(conn)[0])` first; a category that
  cannot be produced makes every category test fail on the unknown-category
  refusal instead of the behaviour under test.
- **VALIDATE**: `make test`.

### Task 6: Documentation
- **ACTION**: Update `docs/CURRENT_CODEBASE_END_TO_END.md`.
- **IMPLEMENT**: in §20.2 add `propose_dormant_rule` to the `propose` branch's
  tool list, noting it writes `confirmed=0`. In the dormant-rules section, add
  that rules may now be proposed from chat as well as the console; the confirm
  gate and the `WHERE confirmed=1` read are unchanged; chat refuses default scope
  and refuses to overwrite a confirmed rule.
- **MIRROR**: the document's per-layer prose.
- **IMPORTS**: n/a
- **GOTCHA**: §20.x was renumbered by the router work — check the current numbers
  before referencing one.
- **VALIDATE**: manual read.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_chat_proposal_is_unconfirmed_and_sizes_nothing` | "keep all filter parts at 2" | row `confirmed=0`; `load_rules() == []` | no |
| `test_chat_cannot_replace_a_confirmed_rule` | same scope as a confirmed rule | `ToolError`; row stays `confirmed=1` | **yes** |
| `test_chat_replaces_a_confirmed_rule_only_when_told` | `replace=True` | row now `confirmed=0` | **yes** |
| `test_chat_refuses_a_quantity_the_engineer_did_not_state` | "a bit higher", `fixed_qty=3` | `ToolError`; no row | **yes** |
| `test_chat_refuses_a_default_scope_rule` | `scope="default"` | `ToolError` naming the console | **yes** |
| `test_chat_refuses_an_unknown_category` | `match_key="widgets"` | `ToolError` listing real categories | **yes** |
| `test_chat_refuses_an_item_not_in_the_batch` | absent item id | `ToolError` | **yes** |
| `test_chat_refuses_fixed_qty_without_a_quantity` | `policy="fixed_qty"`, no qty | `ToolError` | **yes** |
| `test_viewer_cannot_propose_a_dormant_rule` | `VIEWER` | refused; no row | **yes** |
| `test_a_proposed_rule_does_not_rescore_a_batch` | propose after scoring | `recommendation_result` unchanged | **yes** |
| routing cases | two phrasings | `propose_dormant_rule` | no |

### Edge Cases Checklist
- [ ] Empty `match_key` on a non-default scope — refused (mirrors the schema validator)
- [ ] `fixed_qty` on `hold_current` / `zero` — refused, not silently dropped
- [ ] `fixed_qty` above 10,000 or negative — refused (schema bounds)
- [ ] No scored batch — item scope accepted, `note` says it could not be verified
- [ ] Category lexicon empty — the refusal lists nothing; message must still read sensibly
- [ ] Same rule proposed twice while still unconfirmed — allowed, updates in place
- [ ] Concurrent access — one `Conn` with an `RLock`; unchanged
- [ ] Permission denied — viewer blocked at the branch and at the tool

---

## Validation Commands

### Static Analysis
```bash
.venv/Scripts/python.exe -m compileall -q backend/app
```
EXPECT: no output

### Registry Sanity
```bash
cd backend && ../.venv/Scripts/python.exe -c "from app.agent import tools as T; print(len(T.REGISTRY)); print(sorted(T.INTENT_TOOLS['propose']))"
```
EXPECT: `22`, and the propose set contains `propose_dormant_rule`

### Unit Tests
```bash
.venv/Scripts/python.exe -m pytest backend/tests/test_dormant_rules.py backend/tests/test_chat_routing.py backend/tests/test_chat_graph.py -q
```
EXPECT: all pass

### Full Test Suite
```bash
make test
```
EXPECT: no regressions. `test_statistical_engine.py` and the existing engine
assertions in `test_dormant_rules.py` must be untouched — this feature adds a way
to propose a rule, not a change to how a rule sizes anything.

### Browser Validation
```bash
make dev
```
EXPECT: `/chat` records a rule that then appears unconfirmed at `/config/dormant`.

### Manual Validation
- [ ] "keep all filter parts at 2" records a rule; `/config/dormant` shows it pending
- [ ] The answer says it is not active and that someone else must confirm
- [ ] Confirming it in the console as a *different* user activates it
- [ ] Re-stating the same rule in chat now refuses, naming the confirmed rule
- [ ] "keep filter parts a bit higher" asks for the exact number
- [ ] "keep all dormant parts at 5" points at the console
- [ ] As `VIEWER`, the same sentence records nothing
- [ ] A batch scored before the rule keeps its numbers

---

## Acceptance Criteria
- [ ] All 6 tasks completed
- [ ] All validation commands pass
- [ ] A chat-proposed rule is `confirmed=0` and invisible to `load_rules()`
- [ ] A confirmed rule cannot be overwritten without `replace=True`
- [ ] `fixed_qty` only ever carries a number from the engineer's own message
- [ ] Default scope is refused from chat
- [ ] No existing test weakened

## Completion Checklist
- [ ] Tool mirrors `propose_change`'s shape and appends to `ctx.sources`
- [ ] `ToolError` for every rejection; no `HTTPException` in a tool
- [ ] Function-local imports for `dormant_rules`, `part_category`, `audit`
- [ ] `audit()` called on the write
- [ ] No `logging` calls
- [ ] Docs updated
- [ ] Nothing from `NOT Building` was built

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Re-proposal silently un-confirms a live rule and moves stock at the next run | **High without the guard** | **High** — thousands of dormant rows resize with nobody approving it | The confirmed-rule guard + `replace=True` opt-in; two tests, one asserting the row stays `confirmed=1` |
| Model invents a `fixed_qty` | Medium | High — an invented stock level on every matching part, on every future batch | Verbatim-number check against `ctx.question`, copied from `propose_change` |
| Category key that matches nothing | Medium | Medium — a rule that looks live and covers zero rows | Validated against `part_category.categories()`; the refusal lists the real ones |
| `DORMANT_RULE_RE` steals `advisory`'s "dormant coverage" questions | Medium | Medium — coverage questions stop working | The `KEEP_RE` conjunct, classify-order placement, and a routing case for each |
| New regex shadows an existing one in `echo.py` | Medium | High — silent, and it already happened once (`ACTION_RE`) | Grep every new constant name before adding; `DORMANT_RE` already exists |
| Fixture descriptions categorise to `''`, so category tests test the wrong thing | **High** | Low — noisy failures, not a bug | Called out in Task 5's GOTCHA; check `categorise()` before writing those tests |

## Notes

### Why this is Medium and not Large
One tool, one prompt section, one regex ladder, tests. No new endpoint, no new
table, no frontend, no new dependency. The router work already built the branch
this tool lives in and the role gate it reuses.

### The one thing to get right
Everything else here is ordinary. The confirmed-rule guard is not: without it,
this feature can move stock across an entire category because someone restated a
sentence, and nothing in the existing tests would notice. Write that test first.

### Follow-ups deliberately excluded
- The same treatment for `part_category_config` (identical propose→confirm shape)
- Reading back "what rules exist" in chat — `GET /config/dormant-rules` has no
  tool; `get_dormant_coverage` reports totals, not the rule list
- Confirming from chat, which would need a second person's session
