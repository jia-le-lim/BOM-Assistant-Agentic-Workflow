# Plan: Two-Layer Review Assist — dormant hard rule + fixed assist chain

## Summary

Split BOM review into two layers. **Layer 1**: dormant rows are sized by an engineer-owned
rule table that *overrides* the statistical engine, with a settings sub-page where engineers
maintain those rules. **Layer 2**: the ~89 active/dying rows per cycle run through a fixed
four-step `langchain-core` chain that emits a **deterministic** verdict
(`flag_for_review` / `bulk_accept_candidate` / `needs_context`) plus an LLM-written sentence,
and pre-ticks the bulk-accept rows in the review queue.

## User Story

As a BOM review engineer, I want dormant wear-and-tear parts held at a level I control rather
than zeroed by the engine, and the remaining rows pre-sorted with the item's own match/diverge
history spelled out, so that I spend my review time on the rows that actually need judgement.

## Problem → Solution

The engine zeroes 1,344 dormant parts the engineer chooses to keep, and every active/dying row
is reviewed cold with no visibility of how the engine has fared on that part before →
dormant parts follow an engineer-owned rule, and each live row arrives with a verdict, a
reason, and its own agreement history.

## Metadata

- **Complexity**: XL
- **Source PRD**: N/A (free-form; derived from the Two-Layer Review Assist architecture proposal)
- **PRD Phase**: N/A
- **Estimated Files**: 12 created, 10 updated
- **Dependencies added**: **none** — `langchain-core` 1.5.6 already installed

---

## Decisions Locked (confirmed with owner, 2026-09-01)

| # | Decision | Choice | Consequence |
|---|---|---|---|
| Q1 | Dormant keep quantity | **Engineer decision is ground truth**, editable over time via a settings sub-page | Needs a rule table + CRUD API + frontend page, not a constant |
| Q2 | Does the dormant rule move stock? | **Yes — it overrides the engine's Min/ROP/Max** | Engine change, not a triage change. Needs a stock-value impact number before enabling |
| Q3 | Bulk accept behaviour | **Pre-tick** the candidates | Guard rails matter more; see Risks |
| Q4 | May the LLM see `justification` text? | **Yes** | `redact.py` needs an explicit allowlist entry; it is a `MEMORY_COLS` field |
| — | Framework | `langchain-core` only, **never** the `langchain` umbrella | Zero new dependencies; Intel offline-install risk avoided |
| — | Existing LangGraph triage | **Kept, not replaced** | The graph earns its conditional routing on the expensive path |

### Measured baseline — the plan is written to these numbers

| Fact | Value | Source |
|---|---|---|
| Total reviewed rows | 9,054 across 8 cycles (2024-07 → 2026-01) | `analysis/output/s20_payloads.pkl` |
| Dormant rows | 8,343 (92.1%) | `engine_statistical.run()` route |
| **Dormant rows the engine zeroes but the engineer keeps** | **1,344** | The Layer 1 target |
| …of those, engineer holds current `max_qty` | 522 (38.8%) | Default policy candidate |
| …median quantity kept | 1 | |
| Live rows (active + dying) | 711 — mean 89/cycle, peak 193 (2026-01), min 9 | Layer 2 volume |
| Acceptance when engine == prior accepted Max | 58.3% | Signal behind `flag`/`accept` |
| …when gap > 50% | 23.3% | Monotone across buckets |
| Live rows with no prior cycle (cold start) | 29% | Why `needs_context` exists |

---

## UX Design

### Before
```
┌────────────────────────────────────────────────────────┐
│  Batch 13 — 1,209 rows to review                       │
│                                                        │
│  500699364  FILTER,ASSEMBLY,DIE   dormant              │
│    Engine: Max 0 / ROP 0 / Min 0   ← engineer overrides│
│    [ Accept ] [ Override ]           this ~1,344 times │
│                                                        │
│  500184754  BEARING,LINEAR        active               │
│    Engine: Max 11 / ROP 5 / Min 2                      │
│    [ Accept ] [ Override ]                             │
│    (no indication the engine has diverged here 4×)     │
└────────────────────────────────────────────────────────┘
```

### After
```
┌────────────────────────────────────────────────────────┐
│  Batch 13 — 1,209 rows · 89 need attention             │
│                                                        │
│  DORMANT (1,120)          rule: hold current · v3      │
│  500699364  FILTER,ASSEMBLY,DIE                        │
│    Rule: Max 2 / ROP 2 / Min 2   DORMANT_RULE_APPLIED  │
│    (engine said 0 — overridden by category rule filter)│
│                                                        │
│  NEEDS REVIEW (34)                                     │
│  500184754  BEARING,LINEAR              ⚑ flag         │
│    Engine: Max 11 / ROP 5 / Min 2                      │
│    "Diverged 4 cycles running, twice with a bulk-      │
│     withdraw reason. Flagged for manual review."       │
│    [ Accept ] [ Override ]                             │
│                                                        │
│  BULK ACCEPT (55)                    [55 pre-ticked]   │
│  ☑ 500221847  SEAL,ORING       matched last 3 cycles   │
│  ☑ 500334192  NOZZLE,SPRAY     matched last 2 cycles   │
│    [ Accept 55 selected ]   [ Clear selection ]        │
└────────────────────────────────────────────────────────┘
```

### Settings sub-page (new)
```
┌────────────────────────────────────────────────────────┐
│  Config › Dormant stocking rules                       │
│                                                        │
│  Default    hold current max_qty          confirmed    │
│  category=filter    fixed 2      pri 100  confirmed    │
│  category=seal      fixed 1      pri 100  pending      │
│  item=500699364     fixed 4      pri 10   confirmed    │
│                                                        │
│  + Add rule:  scope [category▾] key [____] crit [any▾] │
│               policy [fixed_qty▾] qty [__] pri [___]   │
│                                              [ Propose ]│
│                                                        │
│  Coverage: 7,912 / 8,343 dormant rows matched (94.8%)  │
│  Impact:   proposed book $2.61M  (+$81k vs engine)     │
└────────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Dormant row | Engine proposes 0, engineer overrides by hand | Rule proposes the kept quantity | Reason code `DORMANT_RULE_APPLIED` names the rule |
| Live row | Numbers only | Numbers + verdict + one-sentence reason | Verdict deterministic, sentence generated |
| Bulk accept | Manual filter selection | Candidates **pre-ticked**, still confirmed by a human | Q3 |
| Config page | Thresholds + categories | New "Dormant stocking rules" section | Propose → confirm, mirrors part-categories |
| Engineer edits a rule | N/A | Takes effect on the next `/run-recommendation` | Rules are read at score time, not review time |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/app/engine_statistical.py` | 354-372 | The dormant sizing branch Layer 1 overrides |
| P0 | `backend/app/engine_statistical.py` | 278-300 | `run()` signature and how `cfg` is read — the engine takes **no DB handle** |
| P0 | `backend/app/part_category.py` | 1-55 | The propose→confirm→engine-reads pattern the rule table copies exactly |
| P0 | `backend/app/routers/rules_config.py` | 181-243 | CRUD + confirm-gate endpoints to mirror verbatim |
| P0 | `backend/app/db.py` | 186-217, 452-485 | Dual SQLite/Postgres DDL — **every table must be declared twice** |
| P0 | `backend/app/db.py` | 836-870 | `_ensure_columns()` — additive migration for existing databases |
| P0 | `backend/app/engine_adapter.py` | 136-180 | `score_batch`: where rules get resolved and passed into the engine |
| P1 | `backend/app/routers/review.py` | 78-137 | `_bulk_targets` — the filter surface the pre-tick reuses |
| P1 | `backend/app/agent/tools.py` | 87-190 | `ToolContext`, `ctx.sources`, `EMPTY` — tool conventions |
| P1 | `backend/app/agent/specialists.py` | 1-60 | `_plain()` narrative sanitising + char caps |
| P1 | `backend/app/agent/graph.py` | 88-118 | `persist()` — the upsert shape `assist_result` copies |
| P1 | `frontend/src/app/config/page.tsx` | 1-60 | Page structure, `useApi`, `can()`, editable-field tables |
| P1 | `backend/tests/conftest.py` | 1-50 | Offline test boundary and role headers |
| P2 | `backend/tests/test_part_category.py` | 1-30 | Config-feature test style |
| P2 | `backend/app/audit.py` | all | Every write is audit-logged |
| P2 | `backend/app/redact.py` | all | Key-name masking — Q4 needs an entry here |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| `langchain-core` Runnables | LangChain docs, `langchain_core.runnables` | `RunnableLambda` wraps a plain callable; `RunnableParallel` fans out to a dict; `\|` composes. No agent executor, no `langchain` package needed |
| `RunnableParallel` | LangChain docs | Runs branches concurrently and merges into one dict keyed by branch name — exactly the step-2 fan-out |
| Structured output | `langchain_core.output_parsers.PydanticOutputParser` | Available in 1.5.6; but see GOTCHA — this codebase's provider seam returns text, so parse with the project's own Pydantic models |

```
KEY_INSIGHT: langchain-core 1.5.6 is already installed; the `langchain` umbrella package is NOT.
APPLIES_TO: Task 12 (the chain)
GOTCHA: Never `import langchain`, `langchain.agents`, or `langchain_openai`. Only
`langchain_core.*`. Adding the umbrella package needs an offline-mirror install on the Intel
network -- the same risk docs/ML_Implementation_Plan_TCB.md section 4 flags.
```

```
KEY_INSIGHT: engine_statistical.run() takes (df, cfg) and holds NO database handle. That is
deliberate -- it is why the engine is unit-testable and re-runnable offline in analysis/.
APPLIES_TO: Tasks 3, 4
GOTCHA: Do NOT give the engine a conn to read dormant rules. Resolve rules in engine_adapter
and pass them through `cfg`, the same way SL_BY_CRIT and the policy levers already work.
```

```
KEY_INSIGHT: The LLM must never produce the verdict, only the sentence explaining it.
APPLIES_TO: Tasks 10, 12
GOTCHA: Same row, same tools, two runs, two verdicts -- a generative model cannot give the
reproducibility the owner asked for. assist_rules.evaluate() decides; narrate() is handed the
decision as an INPUT and may not change it. Task 13 asserts this.
```

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: backend/app/part_category.py:25-34
MODEL_VERSION = "cat-v1"
UNCATEGORISED = ""

# Longest a stored pattern may be. A user-supplied regex is arbitrary code for
# the matching engine, and nested quantifiers like (a+)+$ backtrack
# catastrophically; the cap bounds the damage. Enforced again at the Pydantic
# layer so a bad rule is rejected at the API, not discovered mid-batch.
MAX_PATTERN_LEN = 200
```
Module-level `MODEL_VERSION`, SCREAMING_SNAKE constants, and a comment that states *why* the
number is what it is. Every new module gets a `*_VERSION`.

### CONFIG_TABLE_DDL
```sql
-- SOURCE: backend/app/db.py:206-217 (SQLite) and 475-486 (Postgres)
-- What KIND of part this is, matched against item_desc. Engineer-owned on the
-- same terms as machine_criticality_config: anyone with review rights may
-- PROPOSE a rule, only an approver confirms, and similarity reads confirmed
-- rows only. Lower priority wins, so specific rules sit above generic ones.
CREATE TABLE IF NOT EXISTS part_category_config (
  pattern TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 500,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now'))
);
```
**Both dialects, same comment.** Postgres uses `BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`
for surrogate keys and `{PG_NOW}` instead of `datetime('now')`.

### PROPOSE_CONFIRM_ENDPOINTS
```python
# SOURCE: backend/app/routers/rules_config.py:198-243
@router.post("/config/part-categories")
def propose_part_category(body: PartCategoryRequest,
                          actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO part_category_config (pattern, category, priority, "
            "set_by, confirmed, updated_at) VALUES (?,?,?,?,0,datetime('now')) "
            "ON CONFLICT(pattern) DO UPDATE SET category=excluded.category, "
            "priority=excluded.priority, set_by=excluded.set_by, "
            "confirmed=0, confirmed_by=NULL, updated_at=datetime('now')",
            (body.pattern, body.category, body.priority, actor["user"]))
        audit(conn, actor, "POST", "/config/part-categories", "part_category",
              body.pattern, body.model_dump())
        conn.commit()
        return {...}
    finally:
        conn.close()


@router.post("/config/part-categories/{pattern:path}/confirm")
def confirm_part_category(pattern: str,
                          actor: dict = Depends(require_role(*APPROVE_ROLES))):
    ...
    if r["set_by"] == actor["user"]:
        raise HTTPException(403, "proposer cannot confirm their own rule")
```
Four invariants: `get_conn()` / `try` / `finally: conn.close()`; `audit()` before `commit()`;
**re-proposing resets `confirmed=0`**; **the proposer may not confirm their own rule**.

### ENGINE_CONFIG_LEVER
```python
# SOURCE: backend/app/engine_statistical.py:288-296
# Demand-model + policy levers (default = current stat-v1 behaviour).
estimator = str(cfg.get("demand_estimator", DEMAND_ESTIMATOR))
trend_on = bool(cfg.get("trend_adjust", TREND_ADJUST))
max_doi_days = float(cfg.get("policy_max_doi_days", POLICY_MAX_DOI_DAYS))
excess_netting_on = bool(cfg.get("policy_excess_netting", POLICY_EXCESS_NETTING))
```
Every lever: module default constant + `cfg.get(...)` override, and **a bare `run(df)` must
reproduce today's behaviour exactly**.

### DORMANT_BRANCH
```python
# SOURCE: backend/app/engine_statistical.py:362-372
elif route == "dormant":
    if _is_critical(criticality):
        new_min = KEEP_ALIVE
        new_rop = KEEP_ALIVE
        new_max = KEEP_ALIVE + moq
        review, dist, conf, risk = "Y", "insurance", 0.6, "Medium"
        reasons.append("DORMANT_CRITICAL_KEEPALIVE")
    else:
        new_min = new_rop = new_max = 0
        review, dist, conf, risk = "Y", "none", 0.4, "Low"
        reasons.append("DORMANT_NONCRITICAL_ZERO")
```
The exact block Task 4 wraps. Reason codes are SCREAMING_SNAKE and appended to `reasons`.

### TOOL_SIGNATURE
```python
# SOURCE: backend/app/agent/tools.py:164-176
def get_item_history(ctx: ToolContext, item_id: str) -> dict:
    """Across ALL batches -- the monthly roster rotates, so an item's history
    is not confined to the batch currently loaded."""
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT reviewer, decision, final_max, final_rop, final_min, "
        "reviewed_at, batch_id FROM review_history WHERE item_id=? "
        "ORDER BY review_id DESC LIMIT 20", (item_id,))]
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "review_history", "item_id": item_id,
                        "count": len(rows)})
    return {"item_id": item_id, "reviews": rows}
```
`ctx` first, plain `dict` return, `EMPTY` when nothing found, **always append to
`ctx.sources`** for provenance, always `LIMIT`.

### UPSERT_PERSIST
```python
# SOURCE: backend/app/agent/graph.py:90-115
state["conn"].execute(
    "INSERT INTO triage_result (batch_id, item_id, stockroom_id, "
    "triage_tier, priority_score, rationale, confidence, focus_question, ...) "
    "VALUES (?,?,?,?,?,?,?,?,...) "
    "ON CONFLICT(batch_id, item_id, stockroom_id) DO UPDATE SET "
    "triage_tier=excluded.triage_tier, ..., triaged_at=datetime('now')",
    (...))
```

### NARRATIVE_SANITISE
```python
# SOURCE: backend/app/agent/specialists.py:14-24, 37-53
MAX_NARRATIVE_CHARS = 320

def _plain(text: str, limit: int = MAX_NARRATIVE_CHARS) -> str:
    """Markdown source rendered as literal text is worse than no formatting.

    Strips what the specialists are told not to emit, then caps at a sentence
    boundary so a truncation does not read as a thought cut in half.
    """
```
"The prompt asks, this enforces: never rely on wording alone." Reuse `_plain`, do not re-write it.

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_part_category.py:1-18, conftest.py:34-37
"""The part-category lexicon: precedence, abbreviations, and the confirm gate.

Ordering is the contract here, the same way it is for echo.py's intent routing:
generic rules last, or "SENSOR BRACKET ASSY" resolves to a bracket.
"""
from conftest import ENG, SENIOR, VIEWER, upload
from app import part_category as PC
```
Module docstring states the *property* under test. Role headers: `ENG`, `SENIOR`, `ADMIN`,
`VIEWER` from `conftest`. `BOM_ENGINE=rules` is pinned in conftest — statistical-engine tests
live in `test_statistical_engine.py`.

### FRONTEND_PAGE
```tsx
// SOURCE: frontend/src/app/config/page.tsx:1-20
"use client";

import { useCallback, useEffect, useState } from "react";
import { useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { PartCategoryPage, RuleConfig } from "@/lib/types";
import { Banner, Spinner } from "@/components/ui";

const EDITABLE = [
  ["long_lead_time_threshold", "Long lead time (days)", "Workload/safety dial: 30 → 47% review & 7.6% miss"],
] as const;
```
`"use client"`, `useApi().call`, `can(role, ...)` for gating, `Banner`/`Spinner` from
`components/ui`, and field metadata as a `const` tuple array with a **calibration note per row**.

---

## Files to Change

### Layer 1 — dormant rule (Tasks 1-8)

| File | Action | Justification |
|---|---|---|
| `backend/app/dormant_rules.py` | CREATE | Rule resolution, seeding, `MODEL_VERSION` — the `part_category.py` analogue |
| `backend/app/db.py` | UPDATE | `dormant_rule_config` DDL in both dialects |
| `backend/app/engine_statistical.py` | UPDATE | Dormant branch consults `cfg["dormant_rules"]` |
| `backend/app/engine_adapter.py` | UPDATE | Resolve confirmed rules from DB into `cfg` |
| `backend/app/schemas.py` | UPDATE | `DormantRuleRequest` |
| `backend/app/routers/rules_config.py` | UPDATE | List / propose / confirm / delete / coverage endpoints |
| `frontend/src/lib/types.ts` | UPDATE | `DormantRule`, `DormantRulePage` |
| `frontend/src/app/config/dormant/page.tsx` | CREATE | The settings sub-page |
| `frontend/src/components/Sidebar.tsx` | UPDATE | Nav entry |
| `backend/tests/test_dormant_rules.py` | CREATE | Resolution order, confirm gate, engine override |
| `analysis/s22_dormant_rule_backtest.py` | CREATE | Replay over 8 cycles; stock-value impact (Q2 requirement) |

### Layer 2 — assist chain (Tasks 9-15)

| File | Action | Justification |
|---|---|---|
| `backend/app/agent/tools.py` | UPDATE | `get_agreement_history` + registration in `specs`/`dispatch` |
| `backend/app/assist/__init__.py` | CREATE | Package marker |
| `backend/app/assist/rules.py` | CREATE | `evaluate()` — the deterministic verdict |
| `backend/app/assist/chain.py` | CREATE | The four-step `langchain-core` chain |
| `backend/app/assist/prompts.py` | CREATE | Narration system prompt |
| `backend/app/routers/assist.py` | CREATE | `POST /assist/run`, `GET /assist/{batch_id}` |
| `backend/app/main.py` | UPDATE | Register the router |
| `backend/app/db.py` | UPDATE | `assist_result` DDL, both dialects |
| `backend/app/redact.py` | UPDATE | Allow `justification` through (Q4) |
| `backend/app/routers/review.py` | UPDATE | `assist_verdict` filter on `_bulk_targets` |
| `frontend/src/app/batches/[id]/page.tsx` | UPDATE | Verdict grouping + **pre-tick** (Q3) |
| `frontend/src/lib/types.ts` | UPDATE | `AssistResult` |
| `backend/tests/test_assist_rules.py` | CREATE | Verdict truth table, determinism |
| `backend/tests/test_assist_chain.py` | CREATE | Step order, echo provider, persistence |

## NOT Building

- **No replacement of the LangGraph triage.** `agent/graph.py` stays as-is and keeps running.
- **No `langchain` umbrella package**, no `langchain_openai`, no `langchain-community`, no agent
  executor. `langchain_core` only.
- **No auto-accept.** Pre-ticked ≠ submitted. A human still presses the button.
- **No re-scoring of historical batches.** `score_batch` protects reviewed rows by design.
- **No confidence-scoring model.** S21 is dropped; do not import `s21_confidence_features`.
- **No change to active/dying sizing maths.** Layer 2 never proposes quantities.
- **No dormant rule applied to the `no-data` route.** Different problem, different evidence.
- **No new LLM provider.** Use the existing `llm/provider.py` seam.
- **No free-form tool calling in the chain.** Fixed order is the requirement.

---

## Step-by-Step Tasks

### Task 1: `dormant_rule_config` schema, both dialects

- **ACTION**: Add the table to `backend/app/db.py` in `SQLITE_DDL` (after `part_category_config`,
  ~line 217) and `POSTGRES_DDL` (~line 486).
- **IMPLEMENT**:
  ```sql
  -- How much stock a DORMANT part keeps. Engineer-owned on the same terms as
  -- part_category_config: review rights may PROPOSE, an approver CONFIRMS, and
  -- the engine reads confirmed rows only. Lower priority wins; item beats
  -- category beats default. Seeded from what engineers actually decided --
  -- the engine's own zero is wrong on 1,344 of 8,343 dormant rows.
  CREATE TABLE IF NOT EXISTS dormant_rule_config (
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,                  -- 'item' | 'category' | 'default'
    match_key TEXT NOT NULL DEFAULT '',   -- item_id, category name, or ''
    criticality TEXT NOT NULL DEFAULT '', -- '' = any, else h|m|l|d
    policy TEXT NOT NULL,                 -- 'hold_current' | 'fixed_qty' | 'zero'
    fixed_qty INTEGER,
    priority INTEGER NOT NULL DEFAULT 500,
    set_by TEXT,
    confirmed_by TEXT,
    confirmed INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE (scope, match_key, criticality)
  );
  ```
  Postgres: `rule_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY` and `{PG_NOW}`.
- **MIRROR**: CONFIG_TABLE_DDL.
- **GOTCHA**: The two DDL blocks are separate strings — adding to one and not the other passes
  the SQLite suite and breaks production. `backend/tests/test_schema_parity.py` exists precisely
  to catch this; run it.
- **GOTCHA**: `UNIQUE (scope, match_key, criticality)` is what makes the `ON CONFLICT` upsert in
  Task 5 work. Without it the endpoint inserts duplicates instead of updating.
- **VALIDATE**: `python -m pytest backend/tests/test_schema_parity.py -q`

### Task 2: `dormant_rules.py` — resolution and seeding

- **ACTION**: Create `backend/app/dormant_rules.py`.
- **IMPLEMENT**:
  - `MODEL_VERSION = "dormant-v1"`
  - `POLICIES = ("hold_current", "fixed_qty", "zero")`
  - `DEFAULT_RULES: list[tuple[int, str, str, str, str, int | None]]` —
    `(priority, scope, match_key, criticality, policy, fixed_qty)`. Seed exactly one row:
    `(900, "default", "", "", "hold_current", None)`. Engineers add the rest; do not invent
    category rules that were never measured.
  - `load_rules(conn) -> list[dict]` — confirmed rows only, `ORDER BY priority, scope`.
  - `resolve(rules, item_id, category, criticality) -> dict | None` — **first match wins**;
    a rule matches when its scope key matches and its `criticality` is `''` or equal.
  - `apply(rule, current_max, moq) -> tuple[int, int, int] | None` — returns `(min, rop, max)`:
    `hold_current` → `(cur, cur, cur)`; `fixed_qty` → `(q, q, q)`; `zero` → `(0, 0, 0)`.
    Returns `None` when `hold_current` has no current value, so the engine keeps its own answer.
- **MIRROR**: NAMING_CONVENTION, and `part_category.load_rules` / `categorise`.
- **IMPORTS**: `from __future__ import annotations`; no DB import in `resolve`/`apply`.
- **GOTCHA**: `resolve` and `apply` take **plain data, never a connection**. `load_rules` is the
  only DB-touching function. This is what lets the engine stay DB-free.
- **GOTCHA**: `hold_current` with `current_max` NaN must return `None`, not 0. A missing current
  level is not a decision to stock nothing.
- **VALIDATE**: item rule beats category rule beats default, asserted in Task 8's tests.

### Task 3: Engine reads the rules from `cfg`

- **ACTION**: Update `backend/app/engine_statistical.py`.
- **IMPLEMENT**:
  - Module default beside the other policy levers (~line 96):
    ```python
    DORMANT_RULES: list[dict] = []   # engineer-owned dormant stocking rules (empty = engine's own zero)
    ```
  - In `run()` beside the other lever reads (~line 296):
    ```python
    dormant_rules = cfg.get("dormant_rules", DORMANT_RULES)
    ```
  - Read `item_desc` and resolve the part category in the row loop the way `crit`/`own` are read.
- **MIRROR**: ENGINE_CONFIG_LEVER.
- **IMPORTS**: `from . import dormant_rules as DR` — safe, `dormant_rules` imports nothing from
  the engine.
- **GOTCHA**: A bare `run(df)` must produce byte-identical output to today. Default `[]` means no
  rule matches and the existing branch runs untouched.
- **VALIDATE**: `python -m pytest backend/tests/test_statistical_engine.py -q`

### Task 4: Dormant branch applies the rule (Q2 — overrides the engine)

- **ACTION**: Wrap the dormant branch at `engine_statistical.py:362-372`.
- **IMPLEMENT**:
  ```python
  elif route == "dormant":
      # Engineer-owned rule wins over the engine here (owner decision, 2026-09-01).
      # These are wear-and-tear parts: the engineer keeps a constant quantity
      # regardless of consumption, and the engine's zero is wrong on 1,344 of
      # 8,343 dormant rows in the TCB history.
      rule = DR.resolve(dormant_rules, item.iloc[i], category, criticality)
      applied = DR.apply(rule, cur, moq) if rule else None
      if applied is not None:
          new_min, new_rop, new_max = applied
          review, dist, conf, risk = "Y", "rule", 0.5, "Low"
          reasons.append("DORMANT_RULE_APPLIED")
      elif _is_critical(criticality):
          ...existing KEEP_ALIVE branch unchanged...
      else:
          ...existing zero branch unchanged...
  ```
- **MIRROR**: DORMANT_BRANCH.
- **GOTCHA**: `review` stays `"Y"`. A rule-sized row is still shown to a human — it changes the
  *number*, not whether anyone looks. Setting `"N"` would silently auto-clear 8,343 rows.
- **GOTCHA**: Append `DORMANT_RULE_APPLIED` rather than replacing `reasons`; downstream
  `reason_code` filters use `LIKE %...%`.
- **VALIDATE**: A dormant row with a confirmed `fixed_qty=2` rule returns Max 2; the same row
  with no rules returns Max 0.

### Task 5: CRUD + confirm endpoints

- **ACTION**: Add to `backend/app/routers/rules_config.py` after the part-categories block
  (~line 243). Add `DormantRuleRequest` to `backend/app/schemas.py`.
- **IMPLEMENT**:
  - `GET  /config/dormant-rules` → `{rules, confirmed, pending}` (`any_role`)
  - `POST /config/dormant-rules` → propose (`REVIEW_ROLES`), upsert on
    `(scope, match_key, criticality)`, resets `confirmed=0`
  - `POST /config/dormant-rules/{rule_id}/confirm` → (`APPROVE_ROLES`), 403 if proposer confirms
  - `DELETE /config/dormant-rules/{rule_id}` → (`APPROVE_ROLES`)
  - `GET  /config/dormant-rules/coverage?batch_id=` → matched / total dormant rows plus the
    proposed stock-value delta vs the engine
  - `DormantRuleRequest`: `scope: Literal["item","category","default"]`,
    `match_key: str = ""`, `criticality: str = ""`, `policy: Literal[...]`,
    `fixed_qty: int | None = Field(None, ge=0, le=10_000)`, `priority: int = 500`
- **MIRROR**: PROPOSE_CONFIRM_ENDPOINTS.
- **IMPORTS**: `from ..schemas import DormantRuleRequest`; `from .. import dormant_rules`.
- **GOTCHA**: A `model_validator` must enforce that `policy == "fixed_qty"` implies
  `fixed_qty is not None`. A `fixed_qty` rule with a null quantity silently sizes to 0, which is
  the exact bug this layer exists to fix.
- **GOTCHA**: `audit()` before `conn.commit()`, inside the same `try`.
- **VALIDATE**: `POST` as `ENG` → `confirmed: false`; confirm as same user → 403; confirm as
  `SENIOR` → `confirmed: true`.

### Task 6: `engine_adapter` passes rules into `cfg`

- **ACTION**: Update `score_batch` in `backend/app/engine_adapter.py` (~line 142).
- **IMPLEMENT**:
  ```python
  cfg = active_config(conn)
  # Engineer-owned dormant stocking rules, resolved here rather than in the
  # engine: engine_statistical holds no database handle, by design.
  cfg = {**cfg, "dormant_rules": dormant_rules.load_rules(conn)}
  ```
  Keep the existing `cfg_hash` computation **after** this line so a rule change invalidates the
  config fingerprint.
- **MIRROR**: ENGINE_CONFIG_LEVER.
- **GOTCHA**: `cfg_hash` is `json.dumps(cfg, sort_keys=True)`; rule dicts must be
  JSON-serialisable. `load_rules` returns `updated_at` as TEXT already — keep it that way.
- **VALIDATE**: Score a batch with a confirmed rule; assert `recommendation_result.reason_code`
  contains `DORMANT_RULE_APPLIED`.

### Task 7: Dormant settings sub-page

- **ACTION**: Create `frontend/src/app/config/dormant/page.tsx`; add `DormantRule` /
  `DormantRulePage` to `frontend/src/lib/types.ts`; add a Sidebar entry.
- **IMPLEMENT**: Rule table (scope, key, criticality, policy, qty, priority, confirmed-by), an
  add-rule form, a confirm button gated on `can(role, "approve")`, and a coverage/impact banner
  from the coverage endpoint.
- **MIRROR**: FRONTEND_PAGE.
- **GOTCHA**: Rules take effect at **score time**, not review time. The page must say so, or an
  engineer will edit a rule and wonder why the open batch is unchanged.
- **VALIDATE**: `cd frontend && npm run build`; propose+confirm round-trips.

### Task 8: `analysis/s22_dormant_rule_backtest.py` (Q2 gate)

- **ACTION**: Create the backtest. **Must run and be reviewed before the rule is enabled.**
- **IMPLEMENT**: Load `s20_payloads.pkl`, run the engine with and without a candidate rule set,
  and report per cycle: dormant rows matched by a rule, agreement with the engineer's actual
  decision, and total stock value (`unitprice * max`). Also unit-test `resolve`/`apply` here via
  a `_selfcheck()`.
- **MIRROR**: `analysis/s20_diverge_rootcause.py` — `ROOT`/`sys.path` header,
  `from app import engine_statistical as E`, `_selfcheck()`, ablation-table `score()`.
- **GOTCHA**: Q2 makes this a stock-moving change. A rule that raises agreement while adding
  $400k of inventory is not obviously a win — report both and let the owner decide.
- **VALIDATE**: `python analysis/s22_dormant_rule_backtest.py` prints a per-cycle table with a
  stock-value delta column.

### Task 9: `get_agreement_history` tool

- **ACTION**: Add to `backend/app/agent/tools.py`; register in `specs()` and `dispatch()`.
- **IMPLEMENT**:
  ```python
  def get_agreement_history(ctx: ToolContext, item_id: str,
                            stockroom_id: str | None = None) -> dict:
      """This item's engine-vs-engineer verdict, one row per past cycle.

      Reads recommendation_result across ALL batches -- the monthly roster
      rotates, so an item's history is not confined to the batch currently
      loaded (same reasoning as get_item_history).
      """
  ```
  Join `recommendation_result` to `review_history` on `(batch_id, item_id, stockroom_id)`;
  return `cycles` (batch_id, agreement, agreement_source, engine_max, final_max, justification),
  plus derived `n_cycles`, `n_diverge`, `diverge_streak` (consecutive diverges from the most
  recent cycle backwards), and
  `gap_vs_prior_accepted = |engine_max_now - last final_max| / max(|last final_max|, 1)`.
- **MIRROR**: TOOL_SIGNATURE — `EMPTY` when no rows, `ctx.sources.append`, `LIMIT 20`.
- **GOTCHA**: Order by `batch_id DESC` and compute `diverge_streak` from the most recent cycle
  backwards. Ordering by `item_id` (what the s20 SQL does) gives a meaningless streak.
- **GOTCHA**: `stockroom_id` must be in the join. One item is reviewed twice in 2024-07 under
  two stockrooms with opposite verdicts; keying on `item_id` alone merges them.
- **VALIDATE**: Seed two batches for one item, match then diverge; assert `diverge_streak == 1`,
  `n_cycles == 2`.

### Task 10: `assist/rules.py` — the deterministic verdict

- **ACTION**: Create `backend/app/assist/__init__.py` and `backend/app/assist/rules.py`.
- **IMPLEMENT**:
  - `MODEL_VERSION = "assist-v1"`,
    `VERDICTS = ("flag_for_review", "bulk_accept_candidate", "needs_context")`
  - `DEFAULTS` dict of thresholds, surfaced through `rules_config` like `TRIAGE_DEFAULTS`
  - `evaluate(evidence: dict, cfg: dict) -> dict` returning
    `{"verdict": str, "reasons": [str], "inputs": {...}}`
  - Order: `needs_context` when `n_cycles == 0` → `flag_for_review` when any flag condition →
    `bulk_accept_candidate` when **all** accept conditions → else `flag_for_review`
  - Flag: `diverge_streak >= 2`, bulk-withdraw in prior justifications, `exposure_usd >=`
    threshold, criticality `h`, peer `historical_override_rate >= 0.5`
  - Accept: last ≥2 cycles `agreement == "match"`, `gap_vs_prior_accepted <= AGREE_TOL`,
    not critical, exposure below threshold, no open `item_note`
- **MIRROR**: NAMING_CONVENTION; `rules_config.TRIAGE_DEFAULTS` for the threshold-defaults shape.
- **IMPORTS**: `from ..engine_statistical import AGREE_TOL` — **import it, never write `0.10`**.
- **GOTCHA**: Pure function. No `conn`, no LLM, no clock, no randomness — that is what makes it
  replayable and testable.
- **GOTCHA**: The default must be `flag_for_review`, never `bulk_accept_candidate`. An unmatched
  case reaching a human costs a minute; the reverse ships an unreviewed stock change.
- **VALIDATE**: Truth-table test, every branch, both directions.

### Task 11: `assist_result` table

- **ACTION**: Add DDL to both dialects in `db.py`, mirroring `triage_result` (line 292 / 546).
- **IMPLEMENT**: `batch_id, item_id, stockroom_id, verdict, reasons_json, narrative,
  evidence_json, model_version, provider, model, assisted_at`; PK
  `(batch_id, item_id, stockroom_id)`; FK to `recommendation_result` `ON DELETE RESTRICT`
  (add to `FOREIGN_KEYS`).
- **MIRROR**: CONFIG_TABLE_DDL and `triage_result`'s FK declaration.
- **GOTCHA**: `ON DELETE RESTRICT` matches `triage_result`. Re-scoring deletes un-reviewed
  `recommendation_result` rows; without a matching cleanup the delete raises IntegrityError —
  the exact bug documented at `engine_adapter.py:150-155`. Delete `assist_result` rows for the
  batch inside `score_batch` before the existing `recommendation_result` delete.
- **VALIDATE**: `python -m pytest backend/tests/test_schema_parity.py backend/tests/test_foreign_keys.py -q`

### Task 12: The chain

- **ACTION**: Create `backend/app/assist/chain.py` and `backend/app/assist/prompts.py`.
- **IMPLEMENT**:
  ```python
  from langchain_core.runnables import RunnableLambda, RunnableParallel

  evidence = RunnableParallel(          # step 2 -- same sources on every row
      recommendation = RunnableLambda(_fetch_recommendation),
      agreement      = RunnableLambda(_fetch_agreement_history),
      notes          = RunnableLambda(_fetch_item_notes),
      peers          = RunnableLambda(_fetch_similar_parts),
      procurement    = RunnableLambda(_fetch_procurement_context),
  )

  assist_chain = (
      RunnableLambda(_resolve_row)      # step 1
      | evidence                        # step 2
      | RunnableLambda(_evaluate)       # step 3 -- DETERMINISTIC, no model
      | RunnableLambda(_narrate)        # step 4 -- the only LLM call
      | RunnableLambda(_persist)
  )
  ```
  `_narrate` calls `get_provider()` with the verdict **already decided**, then `_plain()`.
- **MIRROR**: UPSERT_PERSIST, NARRATIVE_SANITISE.
- **IMPORTS**: `from langchain_core.runnables import RunnableLambda, RunnableParallel`;
  `from ..agent.specialists import _plain`; `from ..llm import Message, get_provider`.
- **GOTCHA**: **Only `langchain_core`.** No `langchain`, no `langchain_openai`.
- **GOTCHA**: `_narrate` must not change the verdict. Return `{**state, "narrative": text}` and
  never let it write `state["verdict"]`.
- **GOTCHA**: `RunnableParallel` branches each receive the *same* input and run concurrently —
  they must not share a `ToolContext`. Give each its own, then merge `sources`.
- **VALIDATE**: Run with `llm/echo.py` (offline provider); five evidence keys present, step
  order fixed.

### Task 13: Determinism and boundary tests

- **ACTION**: Create `backend/tests/test_assist_rules.py` and `backend/tests/test_assist_chain.py`.
- **IMPLEMENT**:
  1. `test_verdict_is_deterministic` — same evidence 100×, one distinct verdict
  2. `test_llm_cannot_change_verdict` — echo provider returns "this should be accepted" on a
     flagged row; persisted verdict stays `flag_for_review`
  3. `test_cold_start_is_needs_context` — `n_cycles == 0`
  4. `test_unmatched_defaults_to_flag`
  5. `test_agree_tol_is_imported` — `assert "0.10" not in Path(rules.__file__).read_text()`
  6. `test_evidence_sources_always_five`
- **MIRROR**: TEST_STRUCTURE.
- **GOTCHA**: `conftest.py` pins `BOM_ENGINE=rules`. Anything asserting statistical-engine
  behaviour belongs in `test_statistical_engine.py` or must set the env explicitly.
- **VALIDATE**: `python -m pytest backend/tests/test_assist_rules.py backend/tests/test_assist_chain.py -q`

### Task 14: Router, redaction, bulk filter

- **ACTION**: Create `backend/app/routers/assist.py`; register in `main.py`; update `redact.py`
  and `review.py`.
- **IMPLEMENT**:
  - `POST /assist/run?batch_id=` (`REVIEW_ROLES`) — active/dying rows only, returns counts
  - `GET /assist/{batch_id}` (`any_role`) — verdicts + narratives
  - `redact.py`: allow `justification` through to prompts (Q4), with a comment recording that
    this is a deliberate PRD 5.1 exception for the assist chain
  - `_bulk_targets`: add `assist_verdict` to the filter set, joining `assist_result`
- **MIRROR**: PROPOSE_CONFIRM_ENDPOINTS (conn handling); `_bulk_targets` filter style.
- **GOTCHA**: Route the pre-tick through the **existing** `triage_guarded_assist_enabled` gate.
  Two independent preselection paths is how a part gets accepted twice under two rules.
- **GOTCHA**: Q4 widens what reaches the model. `justification` is free text an engineer typed —
  prompt-injection surface. Keep `LLM_REDACT_PROMPTS` applying to everything else.
- **VALIDATE**: `POST /assist/run`, then `POST /review/bulk` with
  `assist_verdict=bulk_accept_candidate`.

### Task 15: Review queue — grouping and pre-tick (Q3)

- **ACTION**: Update `frontend/src/app/batches/[id]/page.tsx` and `lib/types.ts`.
- **IMPLEMENT**: Group rows into Needs review / Bulk accept / Dormant (rule applied);
  **pre-tick** `bulk_accept_candidate` rows; show the narrative under each; a visible count
  ("55 pre-selected") and a one-click **Clear selection**.
- **MIRROR**: FRONTEND_PAGE.
- **GOTCHA**: Pre-ticking is only safe when the count is loud and clearing is one click. Show
  the number next to the submit button, never just the ticks.
- **GOTCHA**: Pre-tick only when `triage_guarded_assist_enabled` is on. Default off.
- **VALIDATE**: `npm run build`; ticks appear, count matches, clear works.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| rule resolution order | item + category + default all match | item rule wins | no |
| criticality filter | rule `criticality='h'`, row `m` | rule does not match | yes |
| `hold_current`, no current | `current_max = NaN` | returns `None`, engine keeps its own | yes |
| `fixed_qty` null | policy `fixed_qty`, qty `None` | rejected at Pydantic | yes |
| engine unchanged by default | `run(df)` with no rules | byte-identical to today | yes |
| engine override | dormant + confirmed `fixed_qty=2` | Max 2, `DORMANT_RULE_APPLIED` | no |
| unconfirmed rule ignored | `confirmed=0` | engine's own zero | yes |
| proposer self-confirm | same user | 403 | yes |
| `diverge_streak` | match, diverge, diverge (newest first) | 2 | no |
| two stockrooms | same item, two stockrooms | separate histories | yes |
| verdict determinism | same evidence ×100 | 1 distinct verdict | yes |
| LLM contradicts verdict | echo says "accept" on a flagged row | verdict unchanged | yes |
| cold start | `n_cycles = 0` | `needs_context` | yes |
| no branch matches | empty evidence | `flag_for_review` | yes |
| `AGREE_TOL` imported | source of `assist/rules.py` | no literal `0.10` | yes |

### Edge Cases Checklist
- [ ] Dormant row with no `max_qty` at all
- [ ] Item whose only prior cycle had `final_max = 0`
- [ ] Batch with zero active/dying rows (chain must no-op)
- [ ] Item appearing in one cycle only
- [ ] Rule table empty (must reproduce today's engine exactly)
- [ ] Rule deleted between score and review
- [ ] LLM provider unreachable — verdict still persists, narrative empty
- [ ] `justification` containing prompt-injection text
- [ ] Re-score a batch that already has `assist_result` rows (FK RESTRICT)
- [ ] 193-row cycle (peak) completes within the request timeout

---

## Validation Commands

### Dependencies already present (no install step)
```bash
python -c "import langchain_core, pydantic; print(langchain_core.__version__)"
```
EXPECT: `1.5.6` — if this fails, STOP. Do not `pip install langchain`.

### Schema parity (both dialects)
```bash
python -m pytest backend/tests/test_schema_parity.py backend/tests/test_foreign_keys.py -q
```
EXPECT: pass — catches a table added to one DDL block only

### Engine unchanged by default
```bash
python -m pytest backend/tests/test_statistical_engine.py -q
```
EXPECT: pass — a bare `run(df)` must be byte-identical to today

### New tests
```bash
python -m pytest backend/tests/test_dormant_rules.py backend/tests/test_assist_rules.py backend/tests/test_assist_chain.py -q
```
EXPECT: all pass

### Full backend suite
```bash
python -m pytest backend/tests -q
```
EXPECT: 271 existing tests still pass, plus the new ones

### Dormant backtest (Q2 gate — review before enabling)
```bash
python analysis/s22_dormant_rule_backtest.py
```
EXPECT: per-cycle agreement delta AND stock-value delta

### Frontend build
```bash
cd frontend && npm run build
```
EXPECT: zero type errors

### Manual Validation
- [ ] Propose a dormant rule as engineer → shows pending
- [ ] Confirm as the same user → 403
- [ ] Confirm as senior → engine applies it on the next `/run-recommendation`
- [ ] Dormant row shows `DORMANT_RULE_APPLIED` and the rule's quantity
- [ ] `POST /assist/run` on a batch → verdicts for active/dying only
- [ ] A flagged row's narrative names the actual reason
- [ ] Bulk-accept rows pre-ticked, count visible, Clear selection works
- [ ] Config page states rules apply at score time

---

## Acceptance Criteria
- [ ] All tasks completed
- [ ] All validation commands pass
- [ ] `backend/tests` still 271 passed, plus new tests
- [ ] A bare `engine_statistical.run(df)` is byte-identical to pre-change output
- [ ] No `langchain` umbrella import anywhere — `langchain_core` only
- [ ] Verdict deterministic over 100 identical runs
- [ ] The LLM cannot change a verdict
- [ ] `AGREE_TOL` imported; literal `0.10` absent from `assist/rules.py`
- [ ] Dormant backtest reports both agreement and stock-value impact
- [ ] Pre-tick gated behind `triage_guarded_assist_enabled`, default off

## Completion Checklist
- [ ] Both DDL dialects updated for every new table
- [ ] Every write `audit()`-logged before commit
- [ ] Propose→confirm gate on every rule mutation
- [ ] Engine holds no database handle
- [ ] Tools append to `ctx.sources`
- [ ] Narratives pass through `_plain()`
- [ ] No hardcoded thresholds — all via `rules_config`
- [ ] No scope additions beyond NOT Building

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Pre-tick (Q3) becomes bulk auto-accept in practice** | **Medium** | **High** | Loud count beside submit, one-click clear, gated behind `triage_guarded_assist_enabled` (default off), every accept still audit-logged per row |
| **Dormant rule (Q2) inflates the stock book** | **High** | **High** | Task 8 backtest reports stock-value delta; rule stays unconfirmed until the owner reviews it. It *will* raise inventory — the question is by how much |
| Rule table drifts from engineer intent | High | Medium | Q1 accepted this: rules change over time. Coverage panel + `updated_at` make staleness visible |
| Prompt injection via `justification` (Q4) | Medium | Medium | Narration is the only LLM step; output capped by `_plain()` and never feeds a decision |
| Table added to one DDL dialect only | Medium | High | `test_schema_parity.py` in the validation loop |
| Engine default behaviour changes accidentally | Medium | High | Empty rule list must reproduce today exactly; asserted |
| 193-row cycle times out | Low | Medium | `POST /assist/run` per batch; if slow, chunk by row — do not add background workers in this plan |
| `assist_result` FK blocks re-score | Medium | Medium | Delete `assist_result` rows for the batch inside `score_batch` before the existing delete |

## Notes

- **Why the engine gets rules through `cfg` and not a connection:** `engine_statistical.run()`
  is pure `(df, cfg) -> df`. That is what lets `analysis/` re-run it offline over eight cycles of
  history — the entire s19/s20/s22 workflow depends on it. Handing it a `conn` would end that.

- **Why the verdict is not an LLM output:** the owner asked for reproducibility. A fixed tool
  order gives reproducible *evidence*; only a deterministic step 3 gives a reproducible *verdict*.
  It is also the only version that can be backtested against eight cycles of real decisions.

- **Why the LangGraph triage stays:** it does a different job — conditional routing to save
  specialist calls on the expensive path. The assist chain runs every active/dying row through
  the same sources. Running both and comparing is cheaper than a migration.

- **The dormant rule is the biggest correctness win in the system.** 1,344 rows, no LLM, no new
  dependency. If Layer 2 slips, Tasks 1-8 stand alone and deliver most of the value.

- **`gap_vs_prior_accepted` should eventually be persisted at score time.**
  `engine_statistical._benchmark()` already computes that comparison and discards the distance.
  Task 9 recomputes it per call; persisting it on `recommendation_result` would make it free.
  Out of scope here.

- **Dropped:** the S21 confidence model. `analysis/s21_*` stays on disk as the record of a
  negative result (walk-forward AUC 0.72, 0% coverage at usable precision) but nothing in this
  plan imports it.
