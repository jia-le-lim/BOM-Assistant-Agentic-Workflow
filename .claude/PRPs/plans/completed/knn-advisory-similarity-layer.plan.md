# Plan: KNN Advisory Similarity Layer

## Summary

Add a deterministic k-nearest-neighbour layer that runs **after** the statistical
engine scores a batch and **before** the engineer reviews it. It retrieves 3–7
historical peer parts (different `item_id`, comparable attributes) from
`review_history` joined to that batch's frozen `bom_rows` snapshot, then persists
neighbour evidence, an outlier score, distance-weighted analogue Max/ROP/Min
ranges, and historical override/high-risk rates into two new advisory tables.
The layer never writes `recommendation_result`, never lowers a risk level, and
never produces an exported number. It can only *add* evidence and *raise* review
priority.

## User Story

As a **BOM review engineer**,
I want **to see what was decided on comparable parts before, next to the engine's
recommendation**,
So that **I can judge a proposal against precedent instead of the statistical
number alone — and be told loudly when no comparable precedent exists.**

## Problem → Solution

**Current state**: the item detail page shows three numbers — current WINGS
values, engine proposal, engineer benchmark — plus prior decisions *on the same
item*. For a part with no history (roster rotates monthly; `route='no-data'`
rows keep current values at confidence 0.2), the engineer has nothing to
calibrate against and no signal that the row is unusual.

**Desired state**: a fourth evidence row — "Historical analogues: median Max 1,
typical range 1–2, from 6 similar parts" — with the closest cases listed in
business language, an explicit `NO_RELIABLE_ANALOGUE` flag when the part has no
peers, and a triage queue that surfaces outliers and high-override-rate
neighbourhoods first.

## Metadata

- **Complexity**: **Large** (17 files, ~1,100 lines; no new dependencies)
- **Source PRD**: N/A — free-form specification supplied in the request
- **PRD Phase**: standalone
- **Estimated Files**: 17 (5 CREATE, 12 UPDATE)
- **Scope decisions confirmed with the user**:
  1. Stages **1, 3, 4, 5** of the supplied roadmap (structured neighbours,
     outlier detection, cold-start analogue ranges, neighbour-derived triage
     signal). Stage 2 (comment/reason retrieval) is delivered through the
     **already-built, currently-unqueried Postgres FTS indexes**, not embeddings.
     Stage 6 (risk-model experimentation) is out.
  2. Comment search uses `PG_ONLY_INDEX_DDL`'s existing GIN `to_tsvector`
     indexes; SQLite falls back to `LIKE`. Zero new dependencies.
  3. Enrichment runs behind an explicit **`POST /similarity/run`** endpoint,
     mirroring `POST /triage/run` — not inline in `score_batch`.

---

## UX Design

### Before

```
┌─ Item 500396010 ─────────────────────────────────────────────┐
│ [Pending review] [▲ High] [↑ Increase] [Sporadic] [≠ Diverges]│
│                                                              │
│ ┌ Advisory triage ─────────────────────────────────────────┐ │
│ │ Escalate · priority 90 · confidence 60%                   │ │
│ │ History / Demand / Procurement narratives                 │ │
│ └───────────────────────────────────────────────────────────┘ │
│ ┌ Engine recommendation ───────────────────────────────────┐ │
│ │                     Max   ROP   Min                       │ │
│ │ Current (WINGS)       2     1     0                       │ │
│ │ Engine proposes       9     8     2                       │ │
│ │ Engineer benchmark    9     8     2                       │ │
│ └───────────────────────────────────────────────────────────┘ │
│ ┌ Review history (THIS item only) ─────────────────────────┐ │
│ │ No prior reviews recorded for this item.                  │ │
│ └───────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
Engineer has no peer precedent, and no signal that none exists.
```

### After

```
┌─ Item 500396010 ─────────────────────────────────────────────┐
│ [Pending review] [▲ High] [↑ Increase] [Sporadic] [≠ Diverges]│
│                                                              │
│ ┌ Advisory triage ─────────────────────────────────────────┐ │
│ │ Escalate · priority 100 · confidence 60%                  │ │
│ │ (priority raised: 71% of peers were overridden upward)    │ │
│ └───────────────────────────────────────────────────────────┘ │
│ ┌ Engine recommendation ───────────────────────────────────┐ │
│ │                     Max   ROP   Min                       │ │
│ │ Current (WINGS)       2     1     0                       │ │
│ │ Engine proposes       9     8     2                       │ │
│ │ Engineer benchmark    9     8     2                       │ │
│ │ Historical analogues  6     5     1   ← NEW, advisory     │ │
│ │   typical Max range 4–8 · 6 peers · confidence 0.72       │ │
│ └───────────────────────────────────────────────────────────┘ │
│ ┌ Similar parts historically ───────────────── NEW ────────┐ │
│ │ ⚠ ANALOGUE_DIVERGENCE — engine is 50% above peer median.  │ │
│ │                                                           │ │
│ │ Part      Why similar               Final    Reason       │ │
│ │ 5001262…  Same machine family,      Max 8    Critical     │ │
│ │           High criticality,                  insurance    │ │
│ │           61–120d lead time                  spare        │ │
│ │ 5003021…  Same policy, dormant,     Max 4    Avoid        │ │
│ │           same supplier                      downtime     │ │
│ │ …3 more                                                   │ │
│ │ Advisory only — never applied to Min/ROP/Max.             │ │
│ └───────────────────────────────────────────────────────────┘ │
│ ┌ Review history (THIS item previously) ───────────────────┐ │
│ │ No prior reviews recorded for this item.                  │ │
│ └───────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘

When there are NO peers within threshold:
┌ Similar parts historically ──────────────────────────────────┐
│ ⚠ NO_RELIABLE_ANALOGUE — 1 peer found, 5 required.           │
│ This part is unusual against 412 reviewed peers. Manual       │
│ review required; no analogue range is shown.                  │
└───────────────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Batch page controls | Run engine → Run advisory triage → Export | Run engine → **Run similarity** → Run advisory triage → Export | Similarity before triage: triage reads `similarity_result` for its priority bump. Order is advisory, not enforced. |
| Item detail — value table | 3–4 rows | +1 row "Historical analogues" with p25–p75 range | Rendered only when `neighbour_count >= similarity_min_neighbours`. |
| Item detail — new card | — | "Similar parts historically" listing up to 5 peers | Separate card from "Review history", per spec §7. Peer list never contains the same `item_id`. |
| Triage priority | LLM `priority_score` clamped 0–100 | Same, then `+15` (capped 100) when peer override/high-risk rate ≥ 0.5 | Deterministic, promote-only. Never lowers. |
| Triage tier | `clear_candidate` allowed when safe | `clear_candidate` additionally blocked when the row is an outlier | Asymmetric by design. |
| Chat | 11 tools | 13 tools: `get_similar_parts`, `search_similar_reviews` | Read-only, return stored source records only. |
| Batch page — queue table | — | No change | Deliberately no new column; the queue is already dense. |

### Edge cases for UX

- Empty neighbour pool (first ever run, zero `review_history` rows): every row
  is `NO_RELIABLE_ANALOGUE`. The run summary returns `neighbour_pool: 0`; the
  batch page must say "no reviewed history yet — run similarity again after the
  first month is reviewed" rather than showing 2,800 scary outlier warnings.
- Similarity not yet run for a batch: `GET /similarity/{batch}/{item}` 404s.
  The item page must treat 404 as "no card", exactly as it already does for
  triage (see `page.tsx:51-54`).
- `ANALOGUE_DIVERGENCE` and `NO_RELIABLE_ANALOGUE` are mutually exclusive:
  divergence requires enough neighbours to compute a median.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/app/db.py` | 40-105 | `FOREIGN_KEYS`, `FK_INDEX_DDL`, `PG_ONLY_INDEX_DDL` — the three lists a new table must be registered in |
| P0 | `backend/app/db.py` | 258-284, 446-475 | `triage_result` in both DDL dialects — the exact template for the two new tables |
| P0 | `backend/app/db.py` | 594-618, 700-735 | `Conn` surface (`execute`/`executemany`/`insert_returning`/`is_postgres`) and `init_db()` |
| P0 | `backend/app/agent/triage.py` | 1-73 | Batch-driver shape: status guard, `refresh` delete, resume-by-LEFT-JOIN, summary dict |
| P0 | `backend/app/routers/triage.py` | 1-73 | Router shape: RBAC dep, `ValueError`→400, audit, commit/close, 404/409 handling |
| P0 | `backend/app/agent/tools.py` | 160-190, 355-460 | Read-tool shape (`ctx.sources.append`, `EMPTY`) and the `REGISTRY`/`ToolSpec` format |
| P1 | `backend/app/engine_statistical.py` | 218-300 | Field names actually read off `bom_rows.payload`, and how `route`/`consumable` are derived |
| P1 | `backend/app/agent/graph.py` | 37-52, 75-121 | `triage_features()` and `synthesis()` — where the promote-only rule is inserted |
| P1 | `backend/app/services.py` | 10-17 | `latest_reviews()` — the "later rows overwrite, latest wins" idiom to reuse for the neighbour pool |
| P1 | `backend/app/redact.py` | 26-30, 65-71 | `SENSITIVE_COLS` and the key-name-only walk — why `similarity_reasons` must not embed supplier/machine values |
| P1 | `backend/tests/test_schema_parity.py` | 71-96 | `test_expected_table_count` asserts exactly 12 tables — must become 14 |
| P1 | `backend/tests/test_foreign_keys.py` | 141-187 | Counts `REFERENCES` occurrences against `len(FOREIGN_KEYS)`; `pk_covered` set must gain both new tables |
| P1 | `backend/app/routers/rules_config.py` | 22-62 | `TRIAGE_DEFAULTS` + `EDITABLE` — the pattern for admin-configurable thresholds |
| P2 | `frontend/src/app/batches/[id]/items/[itemId]/page.tsx` | 42-66, 145-235 | Parallel load with 404-tolerance, and the value table to extend |
| P2 | `frontend/src/app/batches/[id]/page.tsx` | 190-220, 392-418 | `runEngine`/`runTriage` handlers and the triage control card to clone |
| P2 | `backend/app/llm/echo.py` | 54-130 | Offline provider routing — new tools need a branch or the suite cannot exercise them |
| P2 | `backend/tests/test_triage_graph.py` | all | The test file to mirror for `test_similarity.py` |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Gower distance | Gower, J.C. (1971), *A General Coefficient of Similarity* | Per-feature distance in [0,1], weighted sum, weights normalised to 1 → composite distance also in [0,1]. Handles mixed categorical/ordinal without scaling artefacts. |
| Weighted percentile | Standard order-statistic definition | Sort by value, accumulate weights, return the value at cumulative weight ≥ q·Σw. Correct for skewed stocking values where the mean is misleading. |
| Postgres FTS | `to_tsvector`/`plainto_tsquery` | `plainto_tsquery` AND-combines the input words and is injection-safe as a bound parameter. Ranking with `ts_rank` is optional; recency ordering is sufficient here. |

**KEY_INSIGHT**: no new Python dependency is needed. `numpy` 2.5.1 is already
pinned (`backend/requirements.txt`), and Gower over 9 features is ~40 lines.
`scikit-learn` would add ~90 MB for a `KNeighborsClassifier` that cannot express
per-feature weights over mixed types anyway.
**APPLIES_TO**: Task 2.
**GOTCHA**: do not reach for `scipy.spatial.distance` — it has no mixed-type
metric, and `cdist(metric=...)` with a Python callable is slower than the
vectorised numpy loop below.

---

## Patterns to Mirror

Every snippet below is copied verbatim from this repository. Follow them exactly.

### NAMING_CONVENTION

```python
# SOURCE: backend/app/agent/triage.py:8-13
MAX_LLM_CALLS_PER_BATCH = 2000


def run_triage(conn, batch_id: int, actor: dict | None = None,
               llm_call_budget: int = MAX_LLM_CALLS_PER_BATCH,
               refresh: bool = False) -> dict:
```

Module-level `UPPER_SNAKE` constants at the top with a comment explaining the
number; `run_<thing>(conn, batch_id, ...)` batch drivers returning a plain dict
summary; snake_case everywhere; no classes unless there is state.

```python
# SOURCE: backend/app/engine_statistical.py:36-45
T_REVIEW = 30                  # review / protection period, days
DEFAULT_LT = 30                # fallback lead time when the row has none
PHI_ACTIVE = 1.5               # conservative variance-to-mean floor, active parts
KEEP_ALIVE = 1                 # insurance stock for critical dormant parts
```

Trailing inline comments on tuning constants — units and rationale, never a bare
number.

### ERROR_HANDLING

```python
# SOURCE: backend/app/agent/triage.py:14-19
    batch = conn.execute("SELECT status FROM batches WHERE batch_id=?",
                         (batch_id,)).fetchone()
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    if batch["status"] != "scored":
        raise ValueError(f"batch {batch_id} has not been scored")
```

```python
# SOURCE: backend/app/routers/triage.py:26-37
    conn = get_conn()
    try:
        try:
            summary = run_triage(conn, body.batch_id, actor,
                                 body.llm_call_budget, body.refresh)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        audit(conn, actor, "POST", "/triage/run", "triage", body.batch_id,
              summary)
        conn.commit()
        return summary
    finally:
        conn.close()
```

Domain layer raises `ValueError`; the router converts to `HTTPException(400)`
with `from e`. `conn.close()` always in `finally`. Never let a domain module
import `fastapi`.

```python
# SOURCE: backend/app/routers/triage.py:66-71
        rows = list(conn.execute(sql, params))
        if not rows:
            raise HTTPException(404, f"no triage result for item {item_id}")
        if len(rows) > 1:
            raise HTTPException(409, "item is present in more than one stockroom")
```

404 for missing, 409 for stockroom ambiguity. Both are load-bearing in the
frontend.

### LOGGING_PATTERN

There is **no logging framework in this codebase**. Do not add one. Observability
is: (a) the returned summary dict, (b) `audit()` rows, (c) `ctx.sources` on chat
tools.

```python
# SOURCE: backend/app/audit.py:8-15
def audit(conn: Conn, actor: dict, method: str, path: str,
          entity: str, entity_id: str, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (user, role, method, path, entity, entity_id, detail) "
        "VALUES (?,?,?,?,?,?,?)",
        (actor.get("user"), actor.get("role"), method, path, entity, str(entity_id),
         json.dumps(detail or {}, default=str)),
    )
```

### REPOSITORY_PATTERN

Raw SQL through the thin `Conn` shim. No ORM. `?` placeholders always — `Conn`
translates to `%s` on Postgres. `datetime('now')` always — `Conn` translates it.

```python
# SOURCE: backend/app/agent/graph.py:68-89
    state["conn"].execute(
        "INSERT INTO triage_result (batch_id, item_id, stockroom_id, "
        "triage_tier, priority_score, rationale, confidence, focus_question, "
        "history_narrative, demand_narrative, procurement_narrative, "
        "sources_json, provider, model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(batch_id, item_id, stockroom_id) DO UPDATE SET "
        "triage_tier=excluded.triage_tier, "
        "priority_score=excluded.priority_score, rationale=excluded.rationale, "
        "... "
        "model=excluded.model, triaged_at=datetime('now')",
        (rec["batch_id"], rec["item_id"], rec["stockroom_id"], ...))
```

`ON CONFLICT ... DO UPDATE SET x=excluded.x` parses identically on both
dialects — use it for idempotent re-runs.

```python
# SOURCE: backend/app/engine_adapter.py:86-90
    conn.executemany(
        "INSERT INTO recommendation_result (batch_id, item_id, stockroom_id, new_max, "
        "new_rop, new_min, review_required, action, reason_code, risk_level, confidence, "
        "explanation, exposure_usd, model_version, rule_version, route, consumable, agreement) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
```

Bulk writes go through `executemany` with a pre-built list of plain tuples
(the REST transport batches these in groups of 500).

```python
# SOURCE: backend/app/services.py:10-17
def latest_reviews(conn: Conn, batch_id: int) -> dict:
    """Latest review per (item_id, stockroom_id) for a batch."""
    out: dict[tuple, Any] = {}
    for r in conn.execute(
        "SELECT * FROM review_history WHERE batch_id=? ORDER BY review_id", (batch_id,)
    ):
        out[(r["item_id"], r["stockroom_id"])] = r  # later rows overwrite: latest wins
    return out
```

Reuse this exact "ORDER BY id, later overwrites" idiom for the neighbour pool.

### SERVICE_PATTERN

Numeric/domain work lives in a **flat module with module-level functions**, not a
class. `engine_statistical.py` is the reference: constants, private `_helpers`,
one public entry point returning a DataFrame/dict. `similarity.py` follows the
same shape.

```python
# SOURCE: backend/app/engine_statistical.py:78-83
def _mu_day(w: dict[int, float]) -> float:
    for win in (365, 547, 180, 90, 30):
        v = w[win]
        if pd.notna(v):
            return max(v / win, 0.0)
    return float("nan")
```

### CONFIG_PATTERN

```python
# SOURCE: backend/app/agent/triage.py:20-23
    cfg = active_config(conn)
    exposure_threshold = float(cfg.get("value_gate_usd", 1000))
    clear_confidence_threshold = float(
        cfg.get("triage_clear_min_confidence", 0.8))
```

Thresholds come from `active_config(conn)` with a module constant as the
fallback — never hard-coded at the use site.

```python
# SOURCE: backend/app/routers/rules_config.py:30-35, 57-61
TRIAGE_DEFAULTS = {
    "triage_clear_min_confidence": 0.8,
    ...
}
EDITABLE = {
    ...
    "triage_clear_min_confidence": (int, float),
    "triage_guarded_assist_enabled": (bool,),
}
```

### TEST_STRUCTURE

```python
# SOURCE: backend/tests/test_triage_graph.py:33-51
def test_triage_graph_runs_offline_and_obeys_budget(client, synth_csv, db_file):
    batch_id = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG)

    run = client.post("/triage/run", json={"batch_id": batch_id},
                      headers=ENG)
    assert run.status_code == 200, run.text
    summary = run.json()
    assert summary["triaged"] == summary["candidates"] > 0
    ...
    assert client.post("/triage/run", json={"batch_id": batch_id},
                       headers=VIEWER).status_code == 403
```

Fixtures `client`, `synth_csv`, `db_file` from `conftest.py`; header constants
`ENG`/`SENIOR`/`VIEWER`; `assert r.status_code == 200, r.text` so the failure
message carries the body; a raw `sqlite3.connect(db_file)` when asserting on
tables the API does not expose.

### FRONTEND_PATTERN

```tsx
// SOURCE: frontend/src/app/batches/[id]/items/[itemId]/page.tsx:47-56
      const [h, templatePage, triageResult] = await Promise.all([
        call<{ reviews: Review[] }>(`history/${itemId}`),
        call<JustificationTemplatePage>("review/justification-templates"),
        can.review(role)
          ? call<TriageResult>(triagePath).catch((e) => {
              if (e instanceof ApiError && e.status === 404) return null;
              throw e;
            })
          : Promise.resolve(null),
      ]);
```

Optional advisory panels are fetched in the same `Promise.all`, role-gated, and
404 is swallowed to `null` — never an error banner.

```tsx
// SOURCE: frontend/src/components/ui.tsx:56-64
export function TriageChip({ tier }: { tier: TriageTier }) {
  const m = TRIAGE_META[tier];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      <span aria-hidden style={{ color: m.color }}>{m.icon}</span>
      <span style={{ color: "var(--text-secondary)" }}>{m.label}</span>
    </span>
  );
}
```

Icon **plus** label, never colour alone (accessibility rule already established
in that file's header comment). CSS custom properties for colour, Tailwind
utilities for layout, `tnum` class on numeric cells.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/app/similarity.py` | CREATE | Feature extraction, Gower distance, weighted percentiles, `run_similarity()` batch driver |
| `backend/app/routers/similarity.py` | CREATE | `POST /similarity/run`, `GET /similarity/{batch_id}`, `GET /similarity/{batch_id}/{item_id}` |
| `backend/tests/test_similarity.py` | CREATE | Distance unit tests, end-to-end run, safety assertions |
| `backend/app/db.py` | UPDATE | Two tables × two dialects, 2 `FOREIGN_KEYS` entries, no new `IDENTITY_PK` |
| `backend/app/main.py` | UPDATE | Register the router |
| `backend/app/schemas.py` | UPDATE | `SimilarityRunRequest` |
| `backend/app/routers/rules_config.py` | UPDATE | `SIMILARITY_DEFAULTS` + 4 `EDITABLE` keys |
| `analysis/engine/rule_config.json` | UPDATE | Seed the four thresholds |
| `backend/app/agent/tools.py` | UPDATE | `get_similar_parts`, `search_similar_reviews` + registry entries |
| `backend/app/agent/prompts.py` | UPDATE | Two tool-routing lines in `SYSTEM` |
| `backend/app/llm/echo.py` | UPDATE | Offline routing for the two new tools |
| `backend/app/agent/graph.py` | UPDATE | Load `similarity_result` in `intake`; promote-only rule in `synthesis` |
| `backend/app/agent/triage.py` | UPDATE | Order outliers first in the candidate query |
| `backend/tests/test_schema_parity.py` | UPDATE | Table count 12 → 14 |
| `backend/tests/test_foreign_keys.py` | UPDATE | Add both tables to `pk_covered` |
| `frontend/src/lib/types.ts` | UPDATE | 3 new interfaces |
| `frontend/src/app/batches/[id]/items/[itemId]/page.tsx` | UPDATE | Analogue row + "Similar parts historically" card |
| `frontend/src/app/batches/[id]/page.tsx` | UPDATE | "Run similarity" control card |

## NOT Building

Explicitly out of scope. Do not add these; if they seem necessary, stop and ask.

- **Comment/reason embeddings, pgvector, or any vector index.** Stage 2 is
  satisfied by the existing `to_tsvector` GIN indexes. No `mem0`, no embedding
  model, no new table.
- **A `similarity` LLM specialist node in the triage graph.** The spec asked for
  one; it is deliberately not built. `similarity_result` is already structured
  evidence, an LLM node would cost 2 model calls per item against a budget that
  already stops at ~285 items per 2,000-call run, and the promote-only safety
  rule must be code, not a prompt. The numbers are instead injected into the
  existing `synthesis` evidence dict. **Add the specialist when** engineers ask
  for a prose narrative of the peer set that the templated `similarity_reasons`
  strings cannot give them.
- **`historical_emergency_rate`, `historical_stockout_rate`, `neighbour_outcome`.**
  The spec lists these; **the data does not exist**. There is no emergency-order
  or stockout-outcome column anywhere in `bom_rows.payload` (verified against the
  116-column `BOM table/BOM REVIEW_Jan'26 .csv` header), in `review_history`, or
  in `recommendation_result`. Persisting always-NULL columns would fake a
  capability. **Add when** an outcome feed exists.
- **Any write to `recommendation_result`, `review_history`, or `pending_change`.**
  The layer is read-plus-own-tables only. `tests/test_agent_boundary.py` must
  keep passing untouched.
- **Lowering `risk_level` or a triage tier.** Promote-only, asymmetric, by design.
- **Consumption-rate numeric features.** `route` (active/dormant/dying/no-data)
  and `consumable` (constant/sporadic/dying/none) already encode demand activity,
  and the spec's own weight table allocates no separate slot for raw rate bands.
  Adding them would silently rebalance the 15% demand group.
- **Backfilling similarity for historical batches.** `POST /similarity/run` is
  per batch and idempotent; run it manually if wanted.
- **A queue-table column or new filter for outliers.** `BulkReviewFilters` is
  untouched — similarity must not be able to select rows for bulk accept.
- **Incremental/online index updates on review.** Spec §8 step 3. The full run is
  a few seconds; re-run the endpoint. **Add when** the pool passes ~100k rows.

---

## Step-by-Step Tasks

### Task 1: Schema — two advisory tables in both dialects

- **ACTION**: Add `similarity_result` and `similarity_neighbour` to `SQLITE_DDL`
  and `POSTGRES_DDL` in `backend/app/db.py`, plus two `FOREIGN_KEYS` entries.
  Insert both tables immediately **after** the `triage_result` block in each DDL
  string, and before the trailing `CREATE INDEX` lines.
- **IMPLEMENT**: SQLite version (the Postgres version differs only in
  `REAL`→`DOUBLE PRECISION`, `INTEGER`→`BIGINT` for the two batch-id columns, and
  `datetime('now')`→`{PG_NOW}`):

```sql
-- Advisory peer evidence. Never an input to sizing, never exported.
-- The engine calculates; these are historical analogues for the engineer.
CREATE TABLE IF NOT EXISTS similarity_result (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  similarity_model_version TEXT NOT NULL,
  neighbour_count INTEGER NOT NULL DEFAULT 0,
  pool_size INTEGER NOT NULL DEFAULT 0,
  nearest_distance REAL,
  outlier_score REAL NOT NULL DEFAULT 1,
  is_outlier INTEGER NOT NULL DEFAULT 1,
  historical_override_rate REAL,
  historical_upward_override_rate REAL,
  historical_high_risk_rate REAL,
  analogue_max_median INTEGER,
  analogue_max_p25 INTEGER,
  analogue_max_p75 INTEGER,
  analogue_rop_median INTEGER,
  analogue_min_median INTEGER,
  advisory_codes TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  generated_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

-- One row per retrieved peer. similarity_reasons is business language only --
-- it must never embed a supplier or machine_type VALUE (redact.py masks by key
-- name, so a value inside a free-text column would bypass LLM_REDACT_PROMPTS).
CREATE TABLE IF NOT EXISTS similarity_neighbour (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  neighbour_rank INTEGER NOT NULL,
  neighbour_item_id TEXT NOT NULL,
  neighbour_stockroom_id TEXT NOT NULL DEFAULT '',
  neighbour_batch_id INTEGER NOT NULL,
  distance REAL NOT NULL,
  similarity_reasons TEXT NOT NULL DEFAULT '',
  neighbour_decision TEXT,
  neighbour_final_max INTEGER,
  neighbour_final_rop INTEGER,
  neighbour_final_min INTEGER,
  neighbour_engine_max INTEGER,
  neighbour_risk_level TEXT,
  neighbour_reason_code TEXT,
  neighbour_justification TEXT,
  neighbour_comment TEXT,
  PRIMARY KEY (batch_id, item_id, stockroom_id, neighbour_rank),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES similarity_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);
```

  And in the index block of **both** DDLs:

```sql
CREATE INDEX IF NOT EXISTS ix_similarity_result_outlier
  ON similarity_result(batch_id, is_outlier, outlier_score);
```

  Two new `FOREIGN_KEYS` entries, appended to the list:

```python
    ("similarity_result_recommendation_fk", "similarity_result",
     ("batch_id", "item_id", "stockroom_id"),
     "recommendation_result", ("batch_id", "item_id", "stockroom_id"),
     "CASCADE"),
    ("similarity_neighbour_result_fk", "similarity_neighbour",
     ("batch_id", "item_id", "stockroom_id"),
     "similarity_result", ("batch_id", "item_id", "stockroom_id"),
     "CASCADE"),
```

- **MIRROR**: the `triage_result` block, `db.py:258-284` (SQLite) and
  `db.py:446-475` (Postgres); the `triage_result_recommendation_fk` entry at
  `db.py:71-75`.
- **IMPORTS**: none.
- **GOTCHA** (five, each of which fails the suite if missed):
  1. `test_foreign_keys.py:165` asserts
     `len(re.findall(r"REFERENCES\s+\w+", ddl)) == len(FOREIGN_KEYS)`. The two
     tables introduce exactly **two** `REFERENCES` occurrences. Do **not** add an
     inline `batch_id ... REFERENCES batches(batch_id)` — `triage_result`
     deliberately omits it too.
  2. `test_schema_parity.py:78` asserts exactly 12 tables. Bump to 14 and update
     the comment (Task 12).
  3. `test_foreign_keys.py:180-184` `pk_covered` must gain both
     `("similarity_result", ("batch_id","item_id","stockroom_id"))` and
     `("similarity_neighbour", ("batch_id","item_id","stockroom_id"))` — both PKs
     lead with the FK columns, so no `FK_INDEX_DDL` entry is needed (Task 12).
  4. `test_schema_parity.py`'s `TABLE_RE` requires the table to close with a
     newline followed by `);` — keep the closing paren on its own line.
  5. Neither table gets an `IDENTITY_PK` entry — both PKs are composite and
     natural, so `insert_returning()` is never used on them.
- **VALIDATE**: `make test` → `test_schema_parity.py` and `test_foreign_keys.py`
  pass (after Task 12).

---

### Task 2: `backend/app/similarity.py` — features, distance, aggregation

- **ACTION**: Create the module. Public surface: `FEATURE_WEIGHTS`,
  `MODEL_VERSION`, `extract_features(payload, rec, crit_config)`,
  `gower_distances(...)`, `weighted_percentile(...)`,
  `run_similarity(conn, batch_id, refresh=False)`.
- **IMPLEMENT**:

**Constants** (mirror `engine_statistical.py:36-45` commenting style):

```python
MODEL_VERSION = "knn-v1"

# Weights sum to 1.0, so the composite Gower distance is also in [0, 1].
# Criticality and machine identity dominate: without that a cheap consumable
# looks "similar" to a critical insurance spare whenever their consumption
# numbers happen to match.
FEATURE_WEIGHTS = {
    "machine_family":       0.25,   # first token of machine_type
    "criticality":          0.20,   # sfm_criticality, confirmed config wins
    "route":                0.10,   # engine demand route
    "consumable":           0.05,   #   ... + demand class = 15% demand group
    "lead_time_band":       0.15,   # ordinal, 5 bands
    "replenishment_policy": 0.10,
    "price_band":           0.05,   # ordinal, 4 bands
    "supplier":             0.05,
    "sharing":              0.05,   # shared vs local-only
}

CATEGORICAL = ("machine_family", "criticality", "route", "consumable",
               "replenishment_policy", "supplier", "sharing")
ORDINAL = {"lead_time_band": 5, "price_band": 4}   # feature -> band count

K_NEIGHBOURS = 7            # retrieved per part (spec: find_similar_parts k=7)
MAX_DISTANCE = 0.35         # beyond this a peer is not "sufficiently similar"
MIN_NEIGHBOURS = 5          # fewer close peers than this -> NO_RELIABLE_ANALOGUE
DIVERGENCE_FRAC = 0.5       # |engine - analogue| beyond this share -> flag
KEEP_ALIVE = 1              # critical parts never get a zero analogue
EPS = 1e-6                  # distance-weight guard, w = 1/(d + EPS)

LEAD_TIME_EDGES = (14, 30, 60, 120)     # -> bands 0..4, days
PRICE_EDGES = (100.0, 1000.0, 10000.0)  # -> bands 0..3, USD
```

**Feature extraction** — every field name below was verified against the live
`BOM table/BOM REVIEW_Jan'26 .csv` header and `engine_statistical.py:238-252`:

```python
def extract_features(payload: dict, rec: dict, crit_config: dict) -> dict:
    """Decision-time attributes only. `payload` is the frozen bom_rows snapshot
    for the row's OWN batch, so a peer is compared on what was known when it was
    decided -- never on today's corrected values (spec section 8)."""
```

| Feature | Source | Derivation |
|---|---|---|
| `machine_family` | `payload["machine_type"]` | first segment before `,` or `;`, `.strip().upper()`; `""` → missing |
| `criticality` | `crit_config` substring match on `machine_type`, else `payload["sfm_criticality"]` | `.strip().lower()[:1]` → `h`/`m`/`l`/`d`; `""` → missing. Mirrors `engine_statistical._is_critical` and the confirmed-config merge in `tools.get_procurement_context:126-129` |
| `route` | `rec["route"]` | already `active`/`dormant`/`dying`/`no-data`; `""` → missing |
| `consumable` | `rec["consumable"]` | `constant`/`sporadic`/`dying`/`none` |
| `lead_time_band` | `payload["contractual_lead_time"]` | `float()`, then `bisect.bisect_right(LEAD_TIME_EDGES, v)`; non-numeric → `-1` |
| `replenishment_policy` | `payload["replenishment_policy"]` | `.strip().lower()` |
| `price_band` | `payload["unitprice"]` | `float()`, then `bisect_right(PRICE_EDGES, v)`; non-numeric → `-1` |
| `supplier` | `payload["supplier_name"]` | `.strip().lower()` |
| `sharing` | `payload["shareable_indicator"]`, falling back to `payload["shared_parts"]` | `"shared"` if either starts with `Y`, else `"local"`; both blank → missing |

**Encoding**: build one `dict[str, int]` code map per categorical feature across
pool **and** targets together, so codes are comparable. Missing → `-1`.

**Distance** — vectorised across the pool, one target at a time:

```python
def gower_distances(target: np.ndarray, pool: np.ndarray,
                    weights: np.ndarray, band_counts: np.ndarray,
                    is_ordinal: np.ndarray) -> np.ndarray:
    """Weighted Gower distance from one target row to every pool row.

    target       shape (F,)   integer codes / band indices, -1 = missing
    pool         shape (N, F) same encoding
    returns      shape (N,)   distance in [0, 1]

    Missing on EITHER side scores 1.0 for that feature rather than being
    dropped from the denominator: a peer we know nothing about is not similar.
    """
    missing = (pool < 0) | (target < 0)
    cat = (pool != target).astype(float)
    ordv = np.abs(pool - target) / np.maximum(band_counts - 1, 1)
    d = np.where(is_ordinal, np.minimum(ordv, 1.0), cat)
    d = np.where(missing, 1.0, d)
    return d @ weights
```

**Weighted percentile**:

```python
def weighted_percentile(values: np.ndarray, weights: np.ndarray,
                        q: float) -> float:
    """Order-statistic percentile. Medians, not means: stocking values are
    heavily skewed and one big peer would drag an average off the mode."""
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    return float(v[np.searchsorted(cum, q * cum[-1])])
```

**Neighbour pool** — one query per run:

```sql
SELECT h.batch_id, h.item_id, h.stockroom_id, h.review_id, h.decision,
       h.final_max, h.final_rop, h.final_min, h.engine_max,
       h.comment, h.justification,
       r.risk_level, r.reason_code, r.route, r.consumable,
       b.payload
FROM review_history h
JOIN recommendation_result r ON r.batch_id=h.batch_id AND r.item_id=h.item_id
                            AND r.stockroom_id=h.stockroom_id
JOIN bom_rows b ON b.batch_id=h.batch_id AND b.item_id=h.item_id
               AND b.stockroom_id=h.stockroom_id
ORDER BY h.review_id
```

Collapse to the latest review per `(batch_id, item_id, stockroom_id)` with the
overwrite-a-dict idiom from `services.latest_reviews`.

**Per-target aggregation**:

```
mask      = distance <= MAX_DISTANCE  AND  pool_item_id != target_item_id
near      = the K_NEIGHBOURS smallest distances within mask
w_i       = 1 / (d_i + EPS)

neighbour_count               = len(near)
nearest_distance              = min(d) over the whole pool excluding same item
outlier_score                 = min(1.0, mean(near distances) / MAX_DISTANCE)
                                (1.0 when near is empty)
is_outlier                    = neighbour_count < MIN_NEIGHBOURS
historical_override_rate      = Σw·[decision == "override"] / Σw
historical_upward_override_rate
                              = Σw·[decision == "override" and
                                    final_max > engine_max] / Σw
historical_high_risk_rate     = Σw·[risk_level == "High"] / Σw
analogue_max_median/p25/p75   = weighted_percentile(final_max, w, .5/.25/.75)
analogue_rop_median           = weighted_percentile(final_rop, w, .5)
analogue_min_median           = weighted_percentile(final_min, w, .5)
confidence                    = 0.5·min(1, neighbour_count/MIN_NEIGHBOURS)
                              + 0.5·max(0, 1 - nearest_distance/MAX_DISTANCE)
```

**Safeguards, applied in this order** (spec §4):

1. If `neighbour_count < MIN_NEIGHBOURS`: write `NO_RELIABLE_ANALOGUE` and leave
   every `analogue_*` column `NULL`. Never emit a range from thin evidence.
2. Round `analogue_max_median/p25/p75` **up** to the row's `order_qty_multiple`
   (`math.ceil(v / moq) * moq`, `moq >= 1`), mirroring
   `engine_statistical.py:319`.
3. Critical keep-alive floor: when the target's criticality is `h`, clamp
   `analogue_max_median = max(analogue_max_median, KEEP_ALIVE)`. An analogue may
   never suggest zeroing a critical spare.
4. Enforce `analogue_max >= analogue_rop >= analogue_min` after rounding —
   independent percentiles can invert.
5. If `abs(analogue_max_median - rec["new_max"]) > max(1, DIVERGENCE_FRAC *
   rec["new_max"])`: append `ANALOGUE_DIVERGENCE`.

**Similarity reasons** (per neighbour, business language, no sensitive values):

```python
_REASON_LABEL = {
    "machine_family": "same machine family",
    "criticality": "same criticality",
    "route": "same demand route",
    "consumable": "same demand class",
    "lead_time_band": "same lead-time band",
    "replenishment_policy": "same replenishment policy",
    "price_band": "same price band",
    "supplier": "same supplier",
    "sharing": "same sharing status",
}
```

Join the labels of matching features (per-feature distance `== 0`), heaviest
weight first, cap at 4. For `criticality`, `route`, `lead_time_band` and
`price_band` — none of which are in `SENSITIVE_COLS` — append the value in
parentheses: `"same criticality (High)"`, `"same lead-time band (61–120d)"`. For
`machine_family` and `supplier` (both **are** in `SENSITIVE_COLS`) emit the label
alone.

**Batch driver**:

```python
def run_similarity(conn, batch_id: int, refresh: bool = False) -> dict:
    # same guards as run_triage: batch exists, status == 'scored'
    # refresh -> DELETE FROM similarity_result WHERE batch_id=?
    #            (similarity_neighbour cascades)
    # resume  -> LEFT JOIN similarity_result ... WHERE s.batch_id IS NULL
    ...
    return {"batch_id": batch_id, "candidates": len(rows), "scored": n,
            "neighbour_pool": len(pool), "outliers": n_outlier,
            "diverging": n_diverge, "no_analogue": n_no_analogue,
            "similarity_model_version": MODEL_VERSION}
```

Two `executemany` writes per run (results, then neighbours), then one
`conn.commit()` in the router.

- **MIRROR**: module shape → `engine_statistical.py`; batch driver →
  `agent/triage.py`; bulk write → `engine_adapter.py:86-90`; pool dict →
  `services.latest_reviews`.
- **IMPORTS**:
  ```python
  from __future__ import annotations
  import bisect, json, math
  import numpy as np
  from .db import Conn, active_config
  ```
  **Not** pandas — this is row-wise dict work and a DataFrame buys nothing here.
  **Not** `..db` — `similarity.py` sits in `backend/app/`, the same level as
  `engine_statistical.py`.
- **GOTCHA**:
  - `bom_rows.payload` values are **all strings** (`ingestion._read_table` uses
    `dtype=str`). Every numeric read needs `try/except (TypeError, ValueError)` →
    missing. Do not assume floats.
  - `conn.execute(...)` yields `sqlite3.Row` on SQLite and a dict on Postgres.
    Both support `r["col"]`. Do **not** call `.get()` on the row; use the
    `dict(row)` + `pop` idiom from `agent/triage.py:43` when you need to mutate.
  - Exclude the target's own `item_id` from its neighbours (spec §7), matching on
    `item_id` alone, **not** `(item_id, stockroom_id)`. The same part in a second
    stockroom is still the same part.
  - Empty pool: return early with every row written as `neighbour_count=0,
    is_outlier=1, NO_RELIABLE_ANALOGUE` and `neighbour_pool: 0` in the summary.
    Do not raise.
  - Put a `# ponytail:` comment on the per-target Python loop naming the ceiling:
    vectorised across the pool, chunked broadcast needed only past ~100k peers.
- **VALIDATE**:
  ```
  .venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'backend'); from app import similarity; print(sum(similarity.FEATURE_WEIGHTS.values()))"
  ```
  EXPECT: `1.0` (assert this in a test too — a mistyped weight silently
  denormalises every distance).

---

### Task 3: Config thresholds

- **ACTION**: Make the four tuning knobs admin-editable.
- **IMPLEMENT**:
  - `analysis/engine/rule_config.json`, after the `_triage` block:
    ```json
    "_similarity": "Advisory KNN peer-evidence layer. Never alters Min/ROP/Max or lowers a risk level.",
    "similarity_max_distance": 0.35,
    "similarity_min_neighbours": 5,
    "similarity_k": 7,
    "similarity_divergence_frac": 0.5,
    ```
  - `backend/app/routers/rules_config.py`: add a `SIMILARITY_DEFAULTS` dict
    sourced from the `similarity` module constants (mirroring how
    `AUTOCLEAR_DEFAULTS` reads from `engine_statistical`), merge it in
    `get_rules` as `merged = {**AUTOCLEAR_DEFAULTS, **TRIAGE_DEFAULTS,
    **SIMILARITY_DEFAULTS, **cfg}`, and add all four keys to `EDITABLE` as
    `(int, float)`.
  - `similarity.py` reads them via `active_config(conn)` with the module
    constants as fallbacks.
- **MIRROR**: `rules_config.py:22-35` and `:57-61`.
- **IMPORTS**: `from .. import similarity` in `rules_config.py` (mirrors
  `from .. import engine_statistical` at line 13).
- **GOTCHA**: `POST /config/rules` requires a **changed** `rule_version`
  (`rules_config.py:82-84`). Seeding new keys in `rule_config.json` only affects
  a **fresh** database — `init_db` seeds `rule_config` only when the table is
  empty. On an existing database the `.get(key, DEFAULT)` fallbacks are what
  actually apply. Do not write a migration for this.
- **VALIDATE**: `GET /config/rules` returns the four keys; `make test` green.

---

### Task 4: Router

- **ACTION**: Create `backend/app/routers/similarity.py` and register it.
- **IMPLEMENT**: three endpoints, all `require_role(*REVIEW_ROLES)` (matching
  `triage.py` — peer evidence names other engineers' decisions, so it is not
  `any_role`):
  - `POST /similarity/run` — body `SimilarityRunRequest`; `ValueError` → 400;
    `audit(conn, actor, "POST", "/similarity/run", "similarity", batch_id,
    summary)`; commit; return the summary.
  - `GET /similarity/{batch_id}` — 404 if the batch does not exist; returns
    `{"batch_id", "total", "items": [...]}` ordered
    `ORDER BY is_outlier DESC, outlier_score DESC, item_id`.
  - `GET /similarity/{batch_id}/{item_id}?stockroom_id=` — 404 when absent, 409
    when >1 row; returns the `similarity_result` row with a `neighbours` list
    from `similarity_neighbour ORDER BY neighbour_rank`.
- Add to `schemas.py`:
  ```python
  class SimilarityRunRequest(BaseModel):
      batch_id: int = Field(ge=1)
      refresh: bool = False
  ```
- Register in `main.py`: add `similarity` to the `from .routers import (...)`
  tuple and `app.include_router(similarity.router, tags=["similarity"])` after
  the triage line.
- **MIRROR**: `backend/app/routers/triage.py` in full; `TriageRunRequest` in
  `schemas.py:38-41`.
- **IMPORTS**:
  ```python
  from fastapi import APIRouter, Depends, HTTPException
  from ..audit import audit
  from ..db import get_conn
  from ..schemas import SimilarityRunRequest
  from ..security import REVIEW_ROLES, require_role
  from ..similarity import run_similarity
  ```
- **GOTCHA**: keep the `triage.py` declaration order for symmetry. The genuine
  route-ordering hazard (a literal path captured as a path parameter) is
  documented at `review.py:132-134`; it does not arise here because
  `/similarity/run` is POST-only, but do not introduce a
  `GET /similarity/{something}` literal later without re-reading that note.
- **VALIDATE**: `GET /health` still 200; `GET /docs` lists the three routes.

---

### Task 5: Chat tools

- **ACTION**: Add `get_similar_parts` and `search_similar_reviews` to
  `backend/app/agent/tools.py` and its `REGISTRY`.
- **IMPLEMENT**:

```python
def get_similar_parts(ctx: ToolContext, item_id: str,
                      batch_id: int | None = None,
                      stockroom_id: str | None = None,
                      limit: int = 5) -> dict:
    """Historical PEER parts -- different items with comparable attributes.
    Not this item's own history; that is get_item_history (spec section 7)."""
```

Resolve via `_resolve(ctx, bid, item_id, stockroom_id)`; return `EMPTY` when
there is no `similarity_result` row; read the result row plus up to
`max(1, min(int(limit), 10))` neighbours; append
`{"type": "similarity_result", "batch_id": bid, "item_id": item_id,
"similarity_model_version": ..., "neighbour_count": ...}` to `ctx.sources`.
Return `neighbour_item_id`, `distance` rounded to 3, `similarity_reasons`,
`neighbour_decision`, `neighbour_final_max/rop/min`, `neighbour_justification` —
and **not** `neighbour_stockroom_id` (sensitive, and the model has no use for it).

```python
def search_similar_reviews(ctx: ToolContext, query: str,
                           item_id: str | None = None,
                           limit: int = 5) -> dict:
    """Free-text search over prior engineer comments and justifications.

    Uses the GIN to_tsvector indexes db.PG_ONLY_INDEX_DDL already creates on
    review_history.comment -- until now nothing queried them. SQLite (the test
    backend) has no FTS5 table here, so it falls back to LIKE.
    """
    if ctx.conn.is_postgres:
        where = ("to_tsvector('english', coalesce(h.comment,'') || ' ' || "
                 "coalesce(h.justification,'')) @@ plainto_tsquery('english', ?)")
        params = [query]
    else:
        where = ("LOWER(COALESCE(h.comment,'') || ' ' || "
                 "COALESCE(h.justification,'')) LIKE ?")
        params = [f"%{query.strip().lower()}%"]
```

Query `review_history h` for `item_id, decision, final_max, final_rop,
final_min, comment, justification, reviewed_at, batch_id`, optional
`AND h.item_id=?`, `ORDER BY h.review_id DESC LIMIT ?` (clamp 1–10). Append
`{"type": "review_history", "query": query, "count": n}` to `ctx.sources`.
Return `EMPTY` on no hits.

Registry entries follow the disambiguating-description convention exactly (note
how every existing description says what the tool is **NOT** for):

```python
    "get_similar_parts": (ToolSpec(
        "get_similar_parts",
        "Historical PEER parts with comparable machine family, criticality, "
        "lead time and demand route, with what was finally decided on each. "
        "Use for 'show me similar parts', 'is this item unusual', 'what did we "
        "do for comparable parts'. NOT this item's own past decisions -- that "
        "is get_item_history. Advisory evidence only; it never sets a level.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH,
                                          "stockroom_id": _STOCK,
                                          "limit": {"type": "integer",
                                                    "description": "1-10"}},
         "required": ["item_id"]}), get_similar_parts),

    "search_similar_reviews": (ToolSpec(
        "search_similar_reviews",
        "Free-text search of what engineers WROTE on past reviews -- comments "
        "and justifications across all items and batches. Use for 'why do "
        "dormant parts keep Max 1', 'what did we say about long lead times'. "
        "NOT for structured peer attributes -- that is get_similar_parts.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "item_id": _ITEM,
            "limit": {"type": "integer", "description": "1-10"}},
         "required": ["query"]}), search_similar_reviews),
```

- **MIRROR**: `get_item_history` (`tools.py:160-172`) for the read-tool body;
  `get_procurement_context` (`tools.py:110-141`) for resolve-then-payload; the
  `REGISTRY` block at `tools.py:355-460`.
- **IMPORTS**: none new — `ToolContext`, `EMPTY`, `_resolve` and `ToolSpec` are
  already in the module.
- **GOTCHA**:
  - `READ_ONLY_TOOLS = frozenset(REGISTRY) - {"propose_change"}` (line 462) picks
    both up automatically. `test_agent_boundary.py:288-291` asserts only that
    `specs(allow_writes=True) - specs(allow_writes=False) == {"propose_change"}`,
    so adding read tools does not break it — **verify, don't assume**.
  - `dispatch()` JSON-serialises through `redact_for_prompt`, which masks by key
    **name**. `similarity_reasons` is free text — Task 2's business-language
    phrasing is what keeps supplier and machine values out of it. Do not
    "improve" those strings by inlining the real values.
  - Do not build `LIMIT` with an f-string; pass the clamped int as a bound
    parameter, as `top_exposure` does (`tools.py:186`).
  - `_translate` protects single-quoted literals, so `'english'` inside the FTS
    SQL survives the `?`→`%s` pass. Verified against `db.py:635-648`.
- **VALIDATE**: `make test` — `test_agent_boundary.py` and `test_chat_routing.py`
  stay green.

---

### Task 6: Prompt + offline provider routing

- **ACTION**: Teach `SYSTEM` and `EchoProvider` about the two tools.
- **IMPLEMENT**:
  - `prompts.py`, in "Choosing a tool" after the `get_item_notes` line:
    ```
    - "similar parts / comparable items / is this unusual" -> get_similar_parts
    - "what did we say / past comments about <topic>" -> search_similar_reviews
    ```
    In "What you do": `- Retrieve peer evidence: what was decided on comparable
    parts, and what engineers wrote about them.`
    Add hard rule 6: `Peer analogues are advisory evidence, never a
    recommendation. Never present an analogue median as the value an item should
    be set to.`
  - `echo.py`, inside `_route`, **after** the `get_procurement_context` branch
    and **before** the `WRITE_RE` branch (an item id plus "similar" must not be
    read as a write):
    ```python
    if (item and "get_similar_parts" in available
            and any(w in ql for w in ("similar", "comparable", "peer",
                                      "unusual", "analogue"))):
        return ToolCall("c1", "get_similar_parts", {"item_id": item.group(1)})
    if ("search_similar_reviews" in available
            and any(w in ql for w in ("what did we say", "past comment",
                                      "previously discussed"))):
        return ToolCall("c1", "search_similar_reviews", {"query": q[:200]})
    ```
- **MIRROR**: `echo.py:105-110` (the `get_procurement_context` branch) for shape,
  and the ordering rationale documented at `echo.py:92-101`.
- **IMPORTS**: none.
- **GOTCHA**: `echo.py`'s branch ordering **is** the contract — the file says so
  at line 92. `batch_summary` must stay last. Place the new branches exactly as
  specified; if `search_similar_reviews` sits above the item-scoped branches,
  "similar parts for 500396010" routes to the wrong tool. `prompts.DONT_KNOW` is
  asserted verbatim in tests — **do not edit it**.
- **VALIDATE**: `make test` → `test_chat_routing.py` green.

---

### Task 7: Triage integration — promote only

- **ACTION**: Feed `similarity_result` into the triage graph as a deterministic,
  promote-only signal.
- **IMPLEMENT**:
  - `graph.py`, `intake()` — the node already has `conn`:
    ```python
    def intake(state: TriageState) -> dict:
        rec = state["recommendation"]
        sim = state["conn"].execute(
            "SELECT neighbour_count, is_outlier, historical_override_rate, "
            "historical_upward_override_rate, historical_high_risk_rate, "
            "analogue_max_median, advisory_codes FROM similarity_result "
            "WHERE batch_id=? AND item_id=? AND stockroom_id=?",
            (rec["batch_id"], rec["item_id"], rec["stockroom_id"])).fetchone()
        sim = dict(sim) if sim is not None else {}
        return {"features": triage_features(rec, state["exposure_threshold"], sim),
                "similarity": sim}
    ```
    Add `similarity: dict` to `TriageState`.
  - `triage_features()` — change the signature to
    `triage_features(rec, exposure_threshold, sim=None)` and add two derived
    booleans so both routing and the evidence dict see them:
    ```python
    "similarity_outlier": bool(sim.get("is_outlier")) if sim else False,
    "similarity_elevated": (
        max(float(sim.get("historical_override_rate") or 0),
            float(sim.get("historical_high_risk_rate") or 0)) >= 0.5
        if sim else False),
    ```
    Update both call sites: `graph.intake` and `triage.run_triage:46`.
  - `synthesis()` — include the similarity numbers in `evidence`, then after the
    existing `safe_clear` block:
    ```python
    # Asymmetric by design: peer evidence may add risk, never cancel a rule.
    # An outlier has no comparable precedent, so it cannot be a clear candidate.
    if (state["features"].get("similarity_outlier")
            and tier == "clear_candidate"):
        tier = "review"
    priority = _number(verdict.get("priority_score"), 0, 100,
                       90 if fallback_tier == "escalate" else 50)
    if state["features"].get("similarity_elevated"):
        priority = min(100.0, priority + 15)
        if tier == "clear_candidate":
            tier = "review"
    ```
    and return `priority` in place of the inline `_number(...)` call.
  - `triage.py`, the candidate query: add
    ```sql
    LEFT JOIN similarity_result s ON s.batch_id=r.batch_id
      AND s.item_id=r.item_id AND s.stockroom_id=r.stockroom_id
    ```
    and prepend `COALESCE(s.is_outlier, 0) DESC,` to the `ORDER BY`.
- **MIRROR**: `graph.py:99-109` for the conservative-fallback style; the LEFT JOIN
  resume pattern already in `triage.py:31-34`.
- **IMPORTS**: none.
- **GOTCHA**:
  - `test_triage_graph.py:24-29` calls `synthesis()` with a hand-built state that
    has **no** `"similarity"` key and a `features` dict holding only
    `critical`/`high_exposure`. Use `state["features"].get(...)`, never `[...]`,
    or that test breaks.
  - Similarity may not have been run — `intake` must tolerate a missing row and
    `run_triage` must still work end to end. The `LEFT JOIN` and the `{}` default
    cover this.
  - `sources` in `TriageState` is `Annotated[list, operator.add]`; do **not** add
    `similarity` as an annotated-reducer field — it is written once by `intake`
    and only read afterwards.
  - **Never** touch `rec["risk_level"]` or write `recommendation_result` here.
- **VALIDATE**: `make test` → `test_triage_graph.py` and `test_bulk_review.py`
  green (bulk review reads `triage_result.confidence`, which is untouched).

---

### Task 8: Tests — `backend/tests/test_similarity.py`

- **ACTION**: Create the test file.
- **IMPLEMENT**: at minimum these cases.

| Test | Input | Expected | Edge case? |
|---|---|---|---|
| `test_weights_sum_to_one` | `FEATURE_WEIGHTS` | `sum == 1.0` (`pytest.approx`) | no |
| `test_identical_rows_have_zero_distance` | same feature vector both sides | `0.0` | no |
| `test_missing_feature_scores_full_distance` | one side `-1` on machine_family | `>= 0.25` | **yes** |
| `test_criticality_dominates_consumption_match` | same route/consumable, different criticality + machine family | distance `> MAX_DISTANCE` | **yes** — the spec's core safety property |
| `test_ordinal_band_distance_is_proportional` | lead-time bands 0 vs 1, and 0 vs 4 | second is 4× the first | no |
| `test_weighted_percentile_is_distance_weighted` | values `[1,10]`, weights `[100,1]` | median `== 1` | **yes** |
| `test_run_on_empty_pool_flags_everything` | scored batch, zero reviews | all rows `is_outlier=1`, `NO_RELIABLE_ANALOGUE`, `neighbour_pool == 0`, no crash | **yes** |
| `test_run_persists_results_and_neighbours` | scored batch + several reviews | `similarity_result` rows == candidates; `similarity_neighbour` rows > 0 | no |
| `test_item_is_never_its_own_neighbour` | item reviewed in a prior batch | no `similarity_neighbour` row where `neighbour_item_id == item_id` | **yes** — spec §7 |
| `test_similarity_never_writes_recommendation_or_review` | snapshot both tables before/after a run | identical | **yes** — the boundary |
| `test_analogue_range_is_null_below_min_neighbours` | 2 peers only | `analogue_max_median IS NULL`, code `NO_RELIABLE_ANALOGUE` | **yes** |
| `test_analogue_max_respects_order_multiple` | peers with `order_qty_multiple=5` | median is a multiple of 5 | **yes** |
| `test_critical_analogue_never_zero` | critical target, peers that all decided 0 | `analogue_max_median >= 1` | **yes** — keep-alive floor |
| `test_outlier_blocks_clear_candidate` | `synthesis()` with `features["similarity_outlier"]=True` and an LLM stub returning `clear_candidate` | tier `== "review"` | **yes** |
| `test_elevated_peers_raise_priority_never_risk` | `similarity_elevated=True` | `priority_score` +15 capped 100; `recommendation_result.risk_level` unchanged | **yes** |
| `test_refresh_rebuilds_and_resume_skips` | run twice, then `refresh=True` | second run `scored == 0`; refresh run `scored == candidates` | no |
| `test_run_requires_scored_batch` | uploaded but unscored batch | 400 | **yes** |
| `test_viewer_cannot_run_similarity` | `VIEWER` headers | 403 | **yes** |
| `test_get_similar_parts_excludes_stockroom` | dispatch the tool | payload has no `neighbour_stockroom_id` key | **yes** — redaction |
| `test_search_similar_reviews_finds_a_comment` | review with comment "long lead time insurance"; query "lead time" | ≥1 hit (SQLite `LIKE` path) | no |

- **MIRROR**: `test_triage_graph.py` in full — fixture use,
  `assert r.status_code == 200, r.text`, and the raw `sqlite3.connect(db_file)`
  block for table-level assertions.
- **IMPORTS**: `from conftest import ENG, VIEWER, upload` (plus `make_row`,
  `rows_to_csv` for the bespoke criticality cases).
- **GOTCHA**:
  - `conftest.py:30` pins `BOM_ENGINE=rules` for the whole suite. The **rule**
    engine does not emit `route`/`consumable` — `engine_adapter.py:69-71`
    defaults them to `""`. So in this suite `route` and `consumable` are always
    missing and each contributes its full weight (0.15 combined) to every
    distance. Budget for that when picking test thresholds, or
    `monkeypatch.setenv("BOM_ENGINE", "statistical")` in tests that need real
    routes. **This silently skews any threshold assertion if ignored.**
  - Reviews must be created through `POST /review/{item_id}` — `review_history`
    has a RESTRICT FK to `recommendation_result`, and `_record_review` is
    documented as the only path in (`review.py:37-45`).
  - Overrides and High-risk rows land in `awaiting_senior`; that does not exclude
    them from the neighbour pool (it reads `review_history` directly), but do not
    assert on `derive_status` in these tests.
- **VALIDATE**: `make test` — all pass.

---

### Task 9: Frontend types

- **ACTION**: Add to `frontend/src/lib/types.ts`, after the triage block.
- **IMPLEMENT**:

```ts
/**
 * Advisory peer evidence. Never applied to Min/ROP/Max and never lowers a risk
 * level — the layer can only add evidence and raise review priority.
 */
export interface SimilarityNeighbour {
  neighbour_rank: number;
  neighbour_item_id: string;
  neighbour_batch_id: number;
  distance: number;
  similarity_reasons: string;
  neighbour_decision: string | null;
  neighbour_final_max: number | null;
  neighbour_final_rop: number | null;
  neighbour_final_min: number | null;
  neighbour_risk_level: string | null;
  neighbour_reason_code: string | null;
  neighbour_justification: string | null;
  neighbour_comment: string | null;
}

export interface SimilarityResult {
  batch_id: number;
  item_id: string;
  stockroom_id: string;
  similarity_model_version: string;
  neighbour_count: number;
  pool_size: number;
  nearest_distance: number | null;
  outlier_score: number;
  is_outlier: number;
  historical_override_rate: number | null;
  historical_upward_override_rate: number | null;
  historical_high_risk_rate: number | null;
  analogue_max_median: number | null;
  analogue_max_p25: number | null;
  analogue_max_p75: number | null;
  analogue_rop_median: number | null;
  analogue_min_median: number | null;
  advisory_codes: string;
  confidence: number;
  generated_at: string;
  neighbours: SimilarityNeighbour[];
}

export interface SimilarityRunSummary {
  batch_id: number;
  candidates: number;
  scored: number;
  neighbour_pool: number;
  outliers: number;
  diverging: number;
  no_analogue: number;
  similarity_model_version: string;
}
```

- **MIRROR**: `types.ts:140-174` (`TriageResult`/`TriageRunSummary`). Note
  `is_outlier` is `number`, not `boolean`, matching how `Review` types
  `requires_senior_approval: number` (`types.ts:112`) — the DDL stores flags as
  `INTEGER` on both dialects (`db.py:246-248` comment explains why).
- **IMPORTS**: none.
- **GOTCHA**: keep `advisory_codes` a comma-joined `string`, matching
  `reason_code` — `ReasonCodes` in `ui.tsx:102` already splits on commas and can
  be reused directly.
- **VALIDATE**: `cd frontend && npx tsc --noEmit`.

---

### Task 10: Item detail page

- **ACTION**: Add the analogue row and the "Similar parts historically" card to
  `frontend/src/app/batches/[id]/items/[itemId]/page.tsx`.
- **IMPLEMENT**:
  - State: `const [similar, setSimilar] = useState<SimilarityResult | null>(null);`
  - In `load()`'s `Promise.all`, alongside the triage fetch, with the same
    404-tolerant, role-gated shape:
    ```tsx
    can.review(role)
      ? call<SimilarityResult>(
          `similarity/${batchId}/${itemId}?stockroom_id=${encodeURIComponent(
            detail.recommendation.stockroom_id)}`
        ).catch((e) => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        })
      : Promise.resolve(null),
    ```
  - In the "Engine recommendation" table, after the "Engineer benchmark" row,
    rendered only when `similar && similar.analogue_max_median !== null`:
    ```tsx
    <tr>
      <td style={{ color: "var(--text-secondary)" }}>Historical analogues</td>
      <td className="text-right tnum">{similar.analogue_max_median}</td>
      <td className="text-right tnum">{similar.analogue_rop_median}</td>
      <td className="text-right tnum">{similar.analogue_min_median}</td>
    </tr>
    ```
    with a caption line below the table: `typical Max range {p25}–{p75} ·
    {neighbour_count} peers · confidence {confidence.toFixed(2)} · advisory only`.
  - A new card in the right-hand column, **above** "Review history", headed
    "Similar parts historically" with the subtitle "Different items with
    comparable characteristics — not this item's own history." (spec §7's two
    labelled evidence paths). Rename the existing card's heading to "This item
    previously" and keep its existing subtitle.
  - Inside the card: a `Banner kind="warning"` when
    `advisory_codes.includes("NO_RELIABLE_ANALOGUE")` reading `This part is
    unusual against {pool_size} reviewed peers — {neighbour_count} close
    match(es) found, {min} required. Manual review.`; a `Banner kind="info"` when
    `ANALOGUE_DIVERGENCE`; then a table with columns **Similar part / Why similar
    / Final / Historical reason** mapping `neighbours` to `neighbour_item_id`
    (font-mono), `similarity_reasons`, `Max {neighbour_final_max}` (tnum, right),
    `neighbour_justification ?? neighbour_comment ?? "—"`.
  - Do **not** show the raw `distance` in the table body — spec §3 is explicit
    that the UI explains similarity in business terms. Put it in a `<details>`
    disclosure if wanted, mirroring the triage sources block.
- **MIRROR**: the triage card (`page.tsx:147-185`) for card/`details` structure;
  the value table (`page.tsx:189-226`) for the row shape; the review-history card
  (`page.tsx:366-403`) for the empty state.
- **IMPORTS**: add `SimilarityResult` to the existing `@/lib/types` import.
- **GOTCHA**:
  - `similar` must be re-fetched after `submit()`; adding it to the same
    `Promise.all` inside `load()` covers it, since `submit()` already calls
    `await load()`.
  - `neighbour_final_max` is nullable — render `—` for `null`.
  - Never render the analogue row when `analogue_max_median` is `null`; a blank
    row reads as "the analogue is zero".
- **VALIDATE**: `npx tsc --noEmit`, `npm run lint`, then `make dev` and open
  `http://localhost:3010/batches/<id>/items/<item>`.

---

### Task 11: Batch page control

- **ACTION**: Add a "Run similarity" card to
  `frontend/src/app/batches/[id]/page.tsx`.
- **IMPLEMENT**: clone the triage control card (`page.tsx:393-418`) and place it
  **above** the triage one. State: `const [similarityRefresh,
  setSimilarityRefresh] = useState(false);` — no budget field (there are no model
  calls). Handler:

```tsx
async function runSimilarity() {
  setBusy(true); setErr(null); setNote(null);
  try {
    const s = await call<SimilarityRunSummary>("similarity/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_id: batchId, refresh: similarityRefresh }),
    });
    setNote(s.neighbour_pool === 0
      ? "No reviewed history yet — every row is flagged as having no reliable "
        + "analogue. Run again once this month's reviews are recorded."
      : `Matched ${s.scored.toLocaleString()} rows against `
        + `${s.neighbour_pool.toLocaleString()} reviewed peers — `
        + `${s.outliers.toLocaleString()} unusual, `
        + `${s.diverging.toLocaleString()} diverging from peer median.`);
    setSimilarityRefresh(false);
    await refresh();
  } catch (e) { setErr((e as Error).message); }
  finally { setBusy(false); }
}
```

- **MIRROR**: `runTriage` (`page.tsx:202-219`) exactly — same busy/err/note
  discipline, same `setXRefresh(false)` after success.
- **IMPORTS**: add `SimilarityRunSummary` to the `@/lib/types` import.
- **GOTCHA**: the empty-pool message is not optional. Without it the first run on
  a fresh install reports 2,800 outliers and looks broken.
- **VALIDATE**: `npx tsc --noEmit`; click through upload → score → similarity →
  triage.

---

### Task 12: Update the two schema guard tests

- **ACTION**: The only edits to existing tests.
- **IMPLEMENT**:
  - `backend/tests/test_schema_parity.py:76-78`:
    ```python
    # 7 original + pending_change, conversation_turn, item_note,
    # model_prediction_log, triage_result, similarity_result,
    # similarity_neighbour
    assert len(SQLITE) == 14, sorted(SQLITE)
    ```
  - `backend/tests/test_foreign_keys.py:180-184`, add to `pk_covered`:
    ```python
                  ("similarity_result",
                   ("batch_id", "item_id", "stockroom_id")),
                  ("similarity_neighbour",
                   ("batch_id", "item_id", "stockroom_id")),
    ```
- **MIRROR**: how the `triage_result` entry was added in commit `6c96b01`
  (`test_foreign_keys.py | 8 +`, `test_schema_parity.py | 4 +`).
- **IMPORTS**: none.
- **GOTCHA**: these are **guard** tests. Changing a count is legitimate;
  weakening an assertion is not. If
  `test_every_declared_relation_is_in_both_ddl_blocks` fails, the DDL is wrong —
  do not touch that test.
- **VALIDATE**: `make test` fully green.

---

## Testing Strategy

### Unit Tests

Covered in Task 8's table. The distance function and the weighted percentile are
the two pieces of non-trivial pure logic and get direct unit tests with
hand-built arrays — no fixtures, no database.

### Edge Cases Checklist

- [ ] Empty neighbour pool (zero reviews) → all `NO_RELIABLE_ANALOGUE`, no crash
- [ ] Pool of exactly 1 peer → below `MIN_NEIGHBOURS`, analogue columns NULL
- [ ] All peers beyond `MAX_DISTANCE` → outlier, analogue columns NULL
- [ ] Every feature missing on the target (blank payload row) → distance 1.0 to
      everything → outlier
- [ ] Non-numeric `contractual_lead_time` / `unitprice` strings → band `-1`
- [ ] `order_qty_multiple` blank or `"0"` → treat as 1, never divide by zero
- [ ] Item stocked in two stockrooms → both rows scored, neither is the other's
      neighbour (same `item_id`)
- [ ] Re-run without `refresh` → resumes, `scored == 0`
- [ ] Re-run with `refresh` → `similarity_neighbour` cascades away, no orphans
- [ ] Batch not scored → 400
- [ ] Batch does not exist → 400 from `run`, 404 from the `GET` list
- [ ] `VIEWER` role → 403 on run and on read
- [ ] Triage run **before** similarity run → works, no promotion applied
- [ ] `LLM_REDACT_PROMPTS=1` → tool output contains no supplier or machine value
- [ ] Concurrent access: not applicable — single-process uvicorn, one connection
      per request, and `run_similarity` is the only writer of these tables
- [ ] Network failure: not applicable — no network call in this feature

---

## Validation Commands

### Static Analysis

```bash
# Backend: no type checker is configured in this repo. Import-check instead.
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'backend'); import app.main, app.similarity, app.routers.similarity; print('ok')"
```
EXPECT: `ok`, no traceback.

```bash
cd frontend && npx tsc --noEmit
```
EXPECT: zero errors.

```bash
cd frontend && npm run lint
```
EXPECT: zero errors.

### Unit Tests

```powershell
$env:BOM_ALLOW_SQLITE="1"; .venv\Scripts\python.exe -m pytest backend/tests/test_similarity.py -q
```
EXPECT: all pass.

### Full Test Suite

```bash
make test
```
EXPECT: no regressions. `test_schema_parity.py`, `test_foreign_keys.py`,
`test_agent_boundary.py`, `test_triage_graph.py`, `test_chat_routing.py` and
`test_bulk_review.py` are the six most likely to break.

### Database Validation

```bash
.venv/Scripts/python.exe backend/scripts/check_referential_integrity.py
```
EXPECT: no violations. `init_db()` adds the two new tables via
`CREATE TABLE IF NOT EXISTS` and the two constraints via `_ensure_foreign_keys()`
on next startup; both are no-ops on a fresh database.

### Browser Validation

```bash
make dev   # backend :8011 + frontend :3010
```
EXPECT: upload → Run engine → **Run similarity** → Run advisory triage → item
page shows the analogue row and the peer card.

### Manual Validation

- [ ] `GET /health` returns 200 after startup (proves `init_db` ran the new DDL)
- [ ] Upload `BOM table/DEMO_TCB_showcase.csv`, score it, run similarity on a
      **fresh** database → summary reports `neighbour_pool: 0`, the batch page
      shows the "no reviewed history yet" note, and no error banner
- [ ] Review ~10 items with a mix of accept/override, then re-run similarity with
      `refresh` → `neighbour_pool` ≥ 10, some rows gain analogue ranges
- [ ] Open an item with peers → analogue row present, peer card lists ≤5 rows, and
      the item itself is **not** among them
- [ ] Open an item without peers → `NO_RELIABLE_ANALOGUE` banner, no analogue row
- [ ] Compare `recommendation_result` before and after a similarity run
      (`SELECT * ... ORDER BY item_id`) → identical
- [ ] Chat: "show me similar parts to 500396010" → `get_similar_parts` fires, the
      answer cites stored neighbours only
- [ ] Chat: "what did we say about long lead times" → `search_similar_reviews`
      fires
- [ ] Run triage after similarity → an outlier row is never `clear_candidate`;
      `recommendation_result.risk_level` is unchanged

---

## Acceptance Criteria

- [ ] All 12 tasks completed
- [ ] All validation commands pass
- [ ] `backend/tests/test_similarity.py` written and passing
- [ ] `npx tsc --noEmit` clean
- [ ] `npm run lint` clean
- [ ] Matches the After UX above
- [ ] `recommendation_result`, `review_history` and `pending_change` are provably
      unwritten by this feature (asserted by test, not by inspection)
- [ ] No risk level or triage tier is ever lowered by similarity evidence
- [ ] `FEATURE_WEIGHTS` sums to exactly 1.0

## Completion Checklist

- [ ] Raw SQL through `Conn`, `?` placeholders, `datetime('now')` — no ORM
- [ ] Both DDL dialects updated; `test_schema_parity` proves they agree
- [ ] `ValueError` in the domain layer, `HTTPException` only in the router
- [ ] `conn.close()` in `finally` on every endpoint
- [ ] Every write endpoint calls `audit()`
- [ ] Thresholds come from `active_config()` with module-constant fallbacks
- [ ] No new dependency in `backend/requirements.txt`
- [ ] No logging framework added
- [ ] Icon-plus-label chips, CSS custom properties, `tnum` on numbers
- [ ] Deliberate simplifications carry a `# ponytail:` comment naming the ceiling
- [ ] Nothing built from the NOT Building list

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cold-start: the pool is empty until a month of reviews exists, so the first run flags everything as an outlier and reads as broken | **High** | Medium | `neighbour_pool` in the summary; a dedicated empty-pool message on the batch page; `pool_size` persisted per row so the UI can say "unusual against N peers" honestly |
| `FEATURE_WEIGHTS` is guesswork until backtested — a badly weighted distance produces confident nonsense | High | **High** | Weights are the spec's own table; `MAX_DISTANCE`/`MIN_NEIGHBOURS` are admin-configurable; the layer is advisory-only and cannot reach an exported number; validate against Jan'26 before trusting the numbers |
| `BOM_ENGINE=rules` in the test suite leaves `route`/`consumable` empty, so tests exercise a 15%-degraded distance and can pass while production behaves differently | **High** | Medium | Called out in Task 8; use `monkeypatch.setenv("BOM_ENGINE","statistical")` for threshold-sensitive tests |
| Redaction bypass — `similarity_reasons` is free text and `redact.py` masks by key name only | Medium | **High** | Business-language reasons never embed supplier or machine values (Task 2); asserted by test; `neighbour_stockroom_id` withheld from the chat tool |
| Run cost grows as `targets × pool`; today ~2.8k × small, but the pool grows every month forever | Medium | Medium | Vectorised across the pool; `# ponytail:` marker on the loop with the chunked-broadcast upgrade path; the pool grows ~2.8k/month |
| REST transport partial failure — two `executemany` calls are two transactions, so results could persist without neighbours | Low | Medium | Documented limitation of `RestConn` (`db.py` header, F3); `refresh=True` re-runs cleanly, and the CASCADE FK means a re-run replaces both consistently |
| Peer evidence pulls the engineer toward past behaviour, entrenching a bad historical habit | Medium | Medium | Engine value stays primary and visually first; analogues are labelled advisory; `ANALOGUE_DIVERGENCE` surfaces disagreement rather than hiding it; promote-only into triage |
| Scope creep into letting analogues set Min/ROP/Max | Medium | **High** | Explicit NOT Building entry; the boundary test asserts `recommendation_result` is unwritten |

## Notes

**Three deviations from the supplied specification, each deliberate:**

1. **No `similarity` LLM specialist node.** The spec asked for one beside
   history/demand/procurement. It is replaced by a deterministic read of
   `similarity_result` in `intake` plus a promote-only rule in `synthesis`. Two
   reasons: the safety asymmetry ("KNN can surface additional risk, but it cannot
   cancel a critical safety rule") must be enforced in code rather than a prompt,
   and an LLM narration would cost 2 calls per item against a budget that already
   stops at ~285 items per 2,000-call run.

2. **`historical_emergency_rate`, `historical_stockout_rate` and
   `neighbour_outcome` are not implemented.** No emergency-order or
   stockout-outcome data exists anywhere in the system — verified against the
   116-column source workbook header, `review_history`, and
   `recommendation_result`. Persisting always-NULL columns would advertise a
   capability the data cannot support.

3. **Comment similarity uses Postgres FTS, not embeddings.** `db.py`'s
   `PG_ONLY_INDEX_DDL` already builds GIN `to_tsvector` indexes on
   `review_history.comment` and `item_note.note`, and its own comment notes
   "nothing queries it there". `search_similar_reviews` is the first consumer.
   This is exact-term rather than semantic matching; **add embeddings when**
   engineers report that paraphrase misses matter more than the operational cost
   of a vector store.

**Two additions beyond the spec's column list**, both because computing them at
read time would duplicate a threshold across the API and two frontend files:
`is_outlier` (derived from `neighbour_count < similarity_min_neighbours`) and
`advisory_codes` (comma-joined, mirroring `reason_code`, so `ui.tsx`'s existing
`ReasonCodes` component renders it unchanged).

**Leakage safety.** Neighbour features come from the peer's **own batch's**
`bom_rows.payload`, which is written once at ingestion and never updated —
`ingestion.ingest` inserts it and nothing in the codebase updates `payload`. That
satisfies spec §8's requirement that similarity use what was known at decision
time. The peer's *labels* (`decision`, `final_*`) are the retrieved evidence, not
features, so there is no circularity. This mirrors the existing treatment of
`factory_recommended_new_*` as a post-hoc scoring signal only
(`engine_statistical.py:200-206`).

**Follow-up (out of this plan):** an `analysis/s17_similarity_backtest.py`
temporally-split evaluation — do peer medians predict the engineer's final value
better than the engine alone? — following the `s16_triage_backtest.py` pattern.
That is the gate the spec sets before stage 6, and the gate before any of this is
allowed to influence a number rather than a priority.
