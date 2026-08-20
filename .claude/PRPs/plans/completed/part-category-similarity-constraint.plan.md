# Plan: Part-Category Similarity Constraint

## Summary

Derive a **part category** (cable, sensor, valve, fastener, …) from each row's
`item_desc` using an engineer-owned regex lexicon, and use it as a **hard
eligibility filter** in the KNN layer: two parts of different known categories
can never be peers, however well their machine family, criticality and lead time
line up. Uncategorised parts are treated as unknown — never blocked, never
claimed as a match.

Because the category is a constraint rather than a distance dimension,
`FEATURE_WEIGHTS` is untouched and still sums to 1.0. No re-tuning.

Measured on the live 3,005-part pool: distinct similarity buckets **637 → 1,241**,
largest bucket **510 → 126**.

## User Story

As a **BOM review engineer**,
I want **peer parts to be the same kind of thing I am looking at**,
So that **a cable's stocking precedent comes from cables, not from whatever screw
happens to share its machine and lead time.**

## Problem → Solution

**Current state**: similarity matches on 9 attributes — machine family,
criticality, route, consumable, lead-time band, replenishment policy, price band,
supplier, sharing status. None of them describes *what the part is*. On the live
pool this leaves 637 buckets for 3,005 parts, with the largest holding **510
parts all at distance 0.000 from each other**, and 37% of parts in buckets of
≥50. Retrieved "peers" are frequently unrelated components.

**Desired state**: a `part_category` resolved from `item_desc` gates peer
eligibility. A cable retrieves cables. The engineer sees "Peers restricted to:
cable" and can fix a miscategorisation through the Config page in seconds.

## Metadata

- **Complexity**: **Medium** (11 files, ~600 lines; no new dependencies)
- **Source PRD**: N/A — free-form request
- **PRD Phase**: standalone
- **Estimated Files**: 11 (2 CREATE, 9 UPDATE)
- **Decisions confirmed with the user**:
  1. **Curated regex lexicon, engineer-owned** — not LLM classification. A
     `part_category_config` table mirroring `machine_criticality_config`, seeded
     with the validated ~23-rule taxonomy, editable through the Config page.
  2. **Hard block** — a different *known* category is never a peer. Applied as a
     pre-distance filter, exactly like the existing same-item exclusion.
  3. **Uncategorised = unknown** — never blocks, never narrated as a match.
     Consistent with the one-sided-missing rule already in `feature_distances`.

---

## Measured Baseline

Run against the live Supabase pool (3,005 deduped stocking rows) before writing
this plan. These numbers are the acceptance target.

| Metric | Now | With category |
|---|---|---|
| `item_desc` populated | 3,005 / 3,005 (100%) | — |
| distinct `item_desc` | 2,903 | — |
| Lexicon coverage | — | **76%** (2,293 / 3,005) |
| Distinct similarity buckets | 637 | **1,241** |
| Largest bucket | **510** (17% of pool) | **126** |
| Buckets ≥50 members | 37% of rows | — |

Category distribution at 76% coverage:

```
(uncategorised) 712   holder 361   sensor 307   valve 172   assembly 171
cable 162   fastener 142   pcb 112   hose_tube ~102   motor ~86  ...
```

**Why naive approaches fail** (both tested and rejected):

- *Leading token of `item_desc`*: 942 distinct heads, top-30 cover only 29%. The
  frequent ones (`STA`, `(IES)`, `COS`, `MBH`, `DNL`, `(DPA)`) are site/system
  prefixes, not part nouns.
- *Keyword-anywhere without abbreviations*: 69%. The missing 7pp is almost
  entirely domain shorthand — `SNR` = sensor, `SOL` = solenoid — visible in
  descriptions like `POST HEAT STATION VAC SNR 1(L)` and `HT WC COL SOL(L)`.

---

## UX Design

### Before

```
┌ Similar parts historically ──────────────────────────────────┐
│ Part      Why similar                        Final  Reason   │
│ 500410590 same machine family, same          Max 0  Follow   │
│           criticality (Low), same demand…           SFM      │
│ 500186249 same machine family, same          Max 1  Follow   │
│           criticality (Low), same lead-tim…         SFM      │
└──────────────────────────────────────────────────────────────┘
Nothing tells the engineer these are even the same kind of component.
A cable and a mounting block can sit side by side at distance 0.037.
```

### After

```
┌ Similar parts historically ──────────────────────────────────┐
│ Peers restricted to: cable · 41 of 3,005 parts eligible       │
│                                                              │
│ Part      Why similar                        Final  Reason   │
│ 500410590 same part category (cable), same   Max 0  Follow   │
│           machine family, same criticality…         SFM      │
│ 500186249 same part category (cable), same   Max 1  Follow   │
│           machine family, same lead-time b…         SFM      │
└──────────────────────────────────────────────────────────────┘

Uncategorised target:
┌ Similar parts historically ──────────────────────────────────┐
│ ℹ No part category matched this description, so peers were   │
│   not restricted by category. Add a rule on the Config page  │
│   to sharpen this.                                           │
└──────────────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Item detail — peer card | peers of any component type | header line naming the category and eligible-peer count | Only when the target's category is known |
| Item detail — reasons | starts with machine family | starts with `same part category (cable)` | Category is the strongest signal, so it leads |
| Config page | Rules + Machine criticality | + **Part categories** section: list rules, propose, confirm | Mirrors the criticality block exactly |
| Config page | — | coverage line: "76% of the current batch categorised · 712 unmatched" with sample descriptions | The feedback loop that makes the lexicon maintainable |
| `/similarity/run` summary | — | `+ categorised`, `+ uncategorised` counts | Visible in the batch-page toast |
| Similarity results | — | `NO_RELIABLE_ANALOGUE` will rise | Expected and correct: the block removes false peers |

### Edge cases for UX

- Target uncategorised → info banner, no restriction, peers unchanged from today.
- Category known but zero eligible peers → `NO_RELIABLE_ANALOGUE`, and the card
  should say "no reviewed *cables* were close enough", not a bare outlier warning.
- A rule proposed but not confirmed must have **no** effect on retrieval.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/app/similarity.py` | 40-95 | `FEATURE_WEIGHTS`, `FEATURES`, `ORDINAL`, the reason-label maps. Category is **not** added to these |
| P0 | `backend/app/similarity.py` | `extract_features` | Where the category is resolved onto each row |
| P0 | `backend/app/similarity.py` | `run_similarity` eligibility line | `eligible = pool_items != target["item_id"]` — the block ANDs onto this exact expression |
| P0 | `backend/app/similarity.py` | `feature_distances` | The one-sided-missing rule (`XOR`) the uncategorised policy must mirror |
| P0 | `backend/app/routers/rules_config.py` | 133-175 | `propose_criticality` / `confirm_criticality` — the propose→confirm pattern to clone verbatim |
| P0 | `backend/app/db.py` | `machine_criticality_config` DDL | A config table with **no foreign keys** — the template |
| P0 | `backend/app/db.py` | `init_db()` rule_config seeding | How a table is seeded on first run only |
| P0 | `backend/app/db.py` | `_ensure_columns()` | **`similarity_result` already exists in production** — a new column needs a migration entry here |
| P1 | `backend/app/similarity.py` | `_load_pool`, `similarity_reasons`, `_known` | Pool dedup, and how a reason is suppressed when the value is unknown |
| P1 | `backend/app/schemas.py` | `CriticalityRequest` | Pydantic shape to mirror |
| P1 | `backend/tests/test_similarity.py` | all | Fixture helpers `_multi_peer_csv`, `scored_batch`, `review_everything`, `rows` |
| P2 | `frontend/src/app/config/page.tsx` | 85-115, 278-320 | Criticality propose/confirm UI to clone |
| P2 | `frontend/src/app/batches/[id]/items/[itemId]/page.tsx` | peer card | Where the restriction header goes |

## External Documentation

No external research needed — feature uses established internal patterns and the
Python standard library `re` module only.

---

## Patterns to Mirror

### CONFIG_TABLE_DDL

```sql
-- SOURCE: backend/app/db.py, machine_criticality_config (SQLITE_DDL)
CREATE TABLE IF NOT EXISTS machine_criticality_config (
  pattern TEXT PRIMARY KEY,
  criticality TEXT NOT NULL,
  service_level_target REAL,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now'))
);
```

Natural-key PK, no foreign keys, `confirmed`/`confirmed_by` two-person gate,
flags as `INTEGER` not `BOOLEAN`.

### PROPOSE_CONFIRM_ENDPOINTS

```python
# SOURCE: backend/app/routers/rules_config.py:133-152
@router.post("/config/criticality")
def propose_criticality(body: CriticalityRequest,
                        actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO machine_criticality_config (pattern, criticality, "
            "service_level_target, set_by, confirmed, updated_at) "
            "VALUES (?,?,?,?,0,datetime('now')) "
            "ON CONFLICT(pattern) DO UPDATE SET criticality=excluded.criticality, "
            "service_level_target=excluded.service_level_target, set_by=excluded.set_by, "
            "confirmed=0, confirmed_by=NULL, updated_at=datetime('now')",
            (body.pattern, body.criticality, body.service_level_target, actor["user"]))
        audit(conn, actor, "POST", "/config/criticality", "criticality", body.pattern,
              body.model_dump())
        conn.commit()
        return {"pattern": body.pattern, "criticality": body.criticality,
                "confirmed": False,
                "note": "proposal recorded; requires confirmation before the engine uses it"}
    finally:
        conn.close()
```

```python
# SOURCE: backend/app/routers/rules_config.py:156-175
@router.post("/config/criticality/{pattern}/confirm")
def confirm_criticality(pattern: str,
                        actor: dict = Depends(require_role(*APPROVE_ROLES))):
    ...
        if r["set_by"] == actor["user"]:
            raise HTTPException(403, "proposer cannot confirm their own proposal")
```

Re-proposing resets `confirmed=0`. The proposer may not confirm their own rule.
**Reproduce both.**

### CONFIRMED_ONLY_READ

```python
# SOURCE: backend/app/db.py, active_config()
    crit = {
        r["pattern"]: r["criticality"]
        for r in conn.execute(
            "SELECT pattern, criticality FROM machine_criticality_config WHERE confirmed=1"
        )
    }
```

`WHERE confirmed=1` is the whole safety property: an unconfirmed rule must not
influence a number.

### SEED_ON_FIRST_RUN

```python
# SOURCE: backend/app/db.py, init_db()
        n = conn.execute("SELECT COUNT(*) c FROM rule_config").fetchone()["c"]
        if n == 0:
            cfg = json.loads((ENGINE_DIR / "rule_config.json").read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO rule_config (rule_version, config_json, active, updated_by) "
                "VALUES (?,?,1,?)",
                (cfg["rule_version"], json.dumps(cfg), "seed"),
            )
```

Count-then-seed, never overwrite.

### ADDITIVE_MIGRATION

```python
# SOURCE: backend/app/db.py, _ensure_columns()
    if is_postgres() or use_rest():
        for col in wanted:
            conn.execute("ALTER TABLE recommendation_result "
                         f"ADD COLUMN IF NOT EXISTS {col} TEXT DEFAULT ''")
        conn.execute("ALTER TABLE triage_result ADD COLUMN IF NOT EXISTS "
                     "procurement_narrative TEXT")
        return
    existing = {r["name"] for r in conn.execute(
        "PRAGMA table_info(recommendation_result)")}
```

Postgres uses `ADD COLUMN IF NOT EXISTS`; SQLite needs a `PRAGMA table_info`
check first.

### ELIGIBILITY_FILTER

```python
# SOURCE: backend/app/similarity.py, run_similarity()
            # Spec section 7: a part is never its own peer. Match on item_id
            # alone -- the same part in a second stockroom is still the same part.
            eligible = pool_items != target["item_id"]
```

A boolean mask over the pool, ANDed before ranking. The category block joins
here — **not** into `FEATURE_WEIGHTS`.

### MISSING_VALUE_RULE

```python
# SOURCE: backend/app/similarity.py, feature_distances()
    one_missing = (pool < 0) ^ (target < 0)     # XOR: exactly one side unknown
```

One side unknown → maximally dissimilar. Both unknown → not dissimilar. The
category block mirrors the *spirit*: block only when both are known and differ.

### REASON_SUPPRESSION

```python
# SOURCE: backend/app/similarity.py
def _known(name: str, feats: dict) -> bool:
    value = feats.get(name)
    return value >= 0 if name in ORDINAL else bool(value)
```

Never narrate a match on a value nobody recorded.

### TEST_STRUCTURE

```python
# SOURCE: backend/tests/test_similarity.py
def scored_batch(client, csv_bytes, label="TEST"):
    b = upload(client, csv_bytes, label=label).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b

def rows(db_file, sql, params=()):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/app/part_category.py` | CREATE | Default lexicon, rule loading, `categorise()` |
| `backend/tests/test_part_category.py` | CREATE | Lexicon and precedence unit tests |
| `backend/app/db.py` | UPDATE | `part_category_config` × 2 dialects, seed, `_ensure_columns` for the new `similarity_result` column |
| `backend/app/similarity.py` | UPDATE | Resolve category, hard block, reason, persist |
| `backend/app/schemas.py` | UPDATE | `PartCategoryRequest` |
| `backend/app/routers/rules_config.py` | UPDATE | propose / confirm / list / coverage |
| `backend/tests/test_similarity.py` | UPDATE | Block behaviour + uncategorised policy |
| `backend/tests/test_schema_parity.py` | UPDATE | Table count 14 → 15 |
| `frontend/src/lib/types.ts` | UPDATE | `PartCategoryRule`, extend `SimilarityResult` |
| `frontend/src/app/config/page.tsx` | UPDATE | Lexicon editor + coverage |
| `frontend/src/app/batches/[id]/items/[itemId]/page.tsx` | UPDATE | Restriction header / uncategorised banner |

## NOT Building

- **LLM classification of descriptions.** Explicitly rejected. Runtime stays
  deterministic; nothing an LLM writes may reach a number.
- **`part_category` as a weighted distance feature.** It is a filter. Do **not**
  add it to `FEATURE_WEIGHTS`, `FEATURES`, `ORDINAL`, `CATEGORICAL`, or the
  encoded matrices. The weights stay exactly as they are and still sum to 1.0.
- **Sub-categories or a hierarchy** (`cable/power` vs `cable/signal`). One flat
  level. Revisit only if buckets are still too large after this lands.
- **Re-categorising `bom_rows.payload`.** The payload is immutable. Category is
  derived at scoring time from `item_desc`, never written back.
- **Backfilling `part_category` onto historical `similarity_result` rows.**
  Re-run `/similarity/run --refresh` instead.
- **Auto-proposing rules from uncovered descriptions.** The coverage endpoint
  reports what is unmatched; a human writes the rule.
- **Changing `MAX_DISTANCE` / `MIN_NEIGHBOURS`.** The block will raise
  `NO_RELIABLE_ANALOGUE` counts. That is the correct outcome, not a reason to
  loosen thresholds. Re-tune only after a measured backtest.

---

## Step-by-Step Tasks

### Task 1: `backend/app/part_category.py`

- **ACTION**: Create the module: default lexicon, DB rule loading, `categorise()`.
- **IMPLEMENT**:

```python
"""What KIND of part this is, derived from item_desc.

The 9 similarity features describe a part's context -- machine, criticality,
lead time, price -- but none of them says what the thing IS. On the Jan'26 pool
that left 510 parts in a single similarity bucket, all at distance 0.000, so a
cable could be retrieved as a mounting block's precedent.

Category is a CONSTRAINT, not a distance dimension: similarity.py uses it to
filter who is eligible to be a peer, so FEATURE_WEIGHTS is untouched.

Rules live in part_category_config and are engineer-owned. Only confirmed=1 rows
are ever read, exactly as machine_criticality_config works -- a proposal must
not move a number until a second person signs it off.
"""

MODEL_VERSION = "cat-v1"
UNCATEGORISED = ""

# Ordered: FIRST MATCH WINS, so specific before generic. `holder` and `assembly`
# are last on purpose -- 'BRACKET' and 'ASSY' appear inside descriptions of far
# more specific parts ('SENSOR BRACKET ASSY' is a sensor).
# Measured 76% coverage over 3,005 reviewed parts (Jan'26 + 5 archived months).
DEFAULT_RULES: list[tuple[int, str, str]] = [   # (priority, category, pattern)
    (10, "sensor",    r"\b(SENSOR|SNR|DETECTOR|ENCODER|THERMOCOUPLE|PROBE|FLOWMETER|SCALE)\b"),
    (20, "valve",     r"\b(VALVE|SOLENOID|SOL|REGULATOR|MANIFOLD)\b"),
    (30, "cable",     r"\b(CABLE|HARNESS|WIRE|CORD|FLEX)\b"),
    (40, "hose_tube", r"\b(HOSE|TUBING|TUBE|PIPE|FITTING|COUPLER)\b"),
    (50, "motor",     r"\b(MOTOR|SERVO|ACTUATOR|STEPPER)\b"),
    (60, "pump",      r"\b(PUMP|BLOWER|COMPRESSOR)\b"),
    (70, "belt",      r"\b(BELT)\b"),
    (80, "pulley",    r"\b(PULLEY|SPROCKET|GEAR|COUPLING)\b"),
    (90, "bearing",   r"\b(BEARING|BUSHING|GUIDE|RAIL|SLIDE)\b"),
    (100, "seal",     r"\b(SEAL|O.?RING|GASKET|PACKING|DIAPHRAGM)\b"),
    (110, "filter",   r"\b(FILTER|STRAINER|CARTRIDGE)\b"),
    (120, "nozzle",   r"\b(NOZZLE|TIP|JET|PICKER|COLLET)\b"),
    (130, "fastener", r"\b(SCREW|BOLT|NUT|WASHER|STUD|CLAMP|CLIP|PIN|SPRING)\b"),
    (140, "pcb",      r"\b(PCB|BOARD|CARD|MODULE|DRIVER|CONTROLLER|AMPLIFIER|CPU|ASIO)\b"),
    (150, "connector", r"\b(CONNECTOR|SOCKET|PLUG|TERMINAL|ADAPTER|ADAPTOR)\b"),
    (160, "switch",   r"\b(SWITCH|RELAY|BREAKER|FUSE)\b"),
    (170, "lamp",     r"\b(LAMP|LED|LIGHT|BULB)\b"),
    (180, "thermal",  r"\b(FAN|HEATER|CHILLER|COOLER|RADIATOR|PELTIER)\b"),
    (190, "optic",    r"\b(LENS|MIRROR|CAMERA|OPTIC|GLASS|WINDOW|PRISM|LASER|ZERODUR)\b"),
    (200, "power",    r"\b(POWER ?SUPPLY|BATTERY|TRANSFORMER|INVERTER|UPS|PSU)\b"),
    (210, "tool",     r"\b(BLADE|CUTTER|PUNCH|DIE|ANVIL|CHUCK|GRIPPER|TWEEZER)\b"),
    (220, "holder",   r"\b(HOLDER|BRACKET|MOUNT|PLATE|BLOCK|SHAFT|ARM|BASE|COVER|"
                      r"PLATEN|CARRIAGE|PEDESTAL|STAGE|TABLE|CHUTE|TRACK)\b"),
    (230, "assembly", r"\b(ASSY|ASSEMBLY|KIT|SET)\b"),
]
```

Then:

```python
def load_rules(conn) -> list[tuple[int, str, re.Pattern]]:
    """Confirmed rules only, in priority order. A proposal must not move a
    number until a second person confirms it (see rules_config.confirm_*)."""

def categorise(item_desc: str, rules) -> str:
    """First matching rule wins. '' when nothing matches -- which never blocks
    and is never narrated as a match."""
```

`load_rules` compiles each pattern with `re.IGNORECASE` and **skips** any that
fails to compile (a bad engineer-entered regex must not 500 the whole batch);
count the skips and return them for the caller to surface.

- **MIRROR**: `engine_statistical.py` module shape (constants, private helpers,
  small public surface); `active_config()`'s `WHERE confirmed=1` read.
- **IMPORTS**: `from __future__ import annotations`, `import re`. Nothing else.
- **GOTCHA**:
  - Ordering **is** the contract, exactly as `echo.py:92` documents for its own
    routing. `holder` and `assembly` must stay last or they swallow everything.
  - Uppercase the description once before matching; do not put `(?i)` in stored
    patterns where an engineer might delete it.
  - A user-supplied regex can be catastrophic (`(a+)+$`). Cap pattern length
    (e.g. 200 chars) at the Pydantic layer and compile once per run, never per row.
- **VALIDATE**:
  ```
  .venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'backend');from app.part_category import DEFAULT_RULES;print(len(DEFAULT_RULES))"
  ```
  EXPECT: 23.

---

### Task 2: Schema — `part_category_config` + `similarity_result.part_category`

- **ACTION**: Add the table to both DDL blocks, seed it in `init_db()`, and add
  the new `similarity_result` column to `_ensure_columns()`.
- **IMPLEMENT**: SQLite (Postgres identical except `datetime('now')` → `{PG_NOW}`):

```sql
-- What KIND of part this is. Engineer-owned, like machine_criticality_config:
-- anyone with review rights may PROPOSE a rule, only an approver confirms, and
-- similarity reads confirmed rows only.
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

Seed in `init_db()`, immediately after the `rule_config` seed:

```python
        n = conn.execute("SELECT COUNT(*) c FROM part_category_config").fetchone()["c"]
        if n == 0:
            from .part_category import DEFAULT_RULES
            conn.executemany(
                "INSERT INTO part_category_config (pattern, category, priority, "
                "set_by, confirmed, confirmed_by) VALUES (?,?,?,'seed',1,'seed')",
                [(pat, cat, pri) for pri, cat, pat in DEFAULT_RULES])
```

Seeded rules land **confirmed=1**: an empty lexicon means the feature silently
does nothing, which is worse than a default an engineer can correct. `rule_config`
seeds `active=1` for the same reason.

Add to `_ensure_columns()` — **`similarity_result` already exists in the live
Supabase project**, so `CREATE TABLE IF NOT EXISTS` will not add the column:

```python
        conn.execute("ALTER TABLE similarity_result ADD COLUMN IF NOT EXISTS "
                     "part_category TEXT DEFAULT ''")
```
plus the SQLite `PRAGMA table_info(similarity_result)` branch.

And in both DDL blocks, add `part_category TEXT NOT NULL DEFAULT ''` to
`similarity_result`.

- **MIRROR**: `machine_criticality_config` DDL; `rule_config` seeding;
  `_ensure_columns`'s two dialect branches.
- **IMPORTS**: local `from .part_category import DEFAULT_RULES` inside `init_db`
  to avoid a circular import at module load.
- **GOTCHA**:
  - `part_category_config` has **no foreign keys**, exactly like
    `machine_criticality_config`. Do **not** touch `FOREIGN_KEYS`, and do not add
    a `REFERENCES` clause — `test_foreign_keys.py` counts `REFERENCES`
    occurrences against `len(FOREIGN_KEYS)` and would fail.
  - `test_schema_parity.py` asserts exactly 14 tables → **15** (Task 7).
  - No `IDENTITY_PK` entry: the PK is the natural key `pattern`.
  - The seed runs inside the existing `init_db()` try block, before the single
    `conn.commit()`.
- **VALIDATE**: `make test` → `test_schema_parity.py`, `test_foreign_keys.py` pass.

---

### Task 3: `similarity.py` — resolve, block, narrate, persist

- **ACTION**: Wire the category into the similarity run as a filter.
- **IMPLEMENT**:

1. **Resolve per row.** `extract_features` gains a `part_category` key, but it is
   **not** added to `FEATURE_WEIGHTS`/`FEATURES`/`ORDINAL`, so `_encode` and the
   distance matrices are untouched:
   ```python
   def extract_features(payload, rec, crit_config, category_rules=()) -> dict:
       ...
       feats["part_category"] = categorise(payload.get("item_desc"), category_rules)
   ```
   Load the rules **once** per run in `run_similarity` and pass them into both
   `extract_features` call sites (targets and `_load_pool`).

2. **Block.** In `run_similarity`, extend the existing eligibility mask:
   ```python
   pool_cats = np.array([p["features"]["part_category"] for p in pool], dtype=object)
   ...
   eligible = pool_items != target["item_id"]
   tcat = target["features"]["part_category"]
   if tcat:
       # Hard constraint, not a weighted feature. A cable is never a screw's
       # precedent however well machine, lead time and price line up. Peers whose
       # own category is unknown stay eligible -- one-sided ignorance does not
       # prove a mismatch (same logic as the XOR rule in feature_distances).
       eligible &= (pool_cats == tcat) | (pool_cats == UNCATEGORISED)
   ```

3. **Narrate.** In `similarity_reasons`, prepend when both sides are known and
   equal: `"same part category (cable)"`. Pass the peer's category in. Because
   the block guarantees equality, this is informational, so it leads the list and
   does **not** consume one of the `MAX_REASONS` weighted slots — raise the cap
   to `MAX_REASONS + 1` for the combined string.

4. **Persist.** Add `part_category` to the `similarity_result` insert and to
   `_summarise`'s returned tuple, and add `eligible_peer_count` to the run
   summary. Extend the summary dict with `categorised` / `uncategorised`.

- **MIRROR**: the `eligible = pool_items != target["item_id"]` line; `_known()`
  for suppression; `_RESULT_INSERT` column ordering.
- **IMPORTS**: `from .part_category import UNCATEGORISED, categorise, load_rules`.
- **GOTCHA**:
  - `_RESULT_INSERT` is positional with 19 `?`. Adding a column means updating
    the column list, the placeholder count **and** the tuple built in
    `_summarise` — all three, or every row shifts by one silently.
  - The empty-pool early-return branch also builds a result tuple; it needs the
    new column too.
  - `pool_cats` must be built **once** outside the per-target loop.
  - Do not let the block touch `nearest_distance` semantics: it is computed over
    `eligible_idx`, so it now correctly means "nearest eligible peer".
  - `dtype=object` for the string array; `np.array` of Python strings otherwise
    becomes a fixed-width `<U` dtype and comparisons silently truncate.
- **VALIDATE**:
  ```
  make test
  ```
  then on live data, expecting the measured baseline:
  ```
  curl -s -X POST localhost:8011/similarity/run -H "X-User: a" -H "X-Role: engineer" \
       -H "Content-Type: application/json" -d '{"batch_id":7,"refresh":true}'
  ```
  EXPECT: `categorised` ≈ 76% of scored; `outliers` higher than the current 12.

---

### Task 4: Schemas + config endpoints

- **ACTION**: `PartCategoryRequest` and four endpoints on the existing router.
- **IMPLEMENT**:

```python
class PartCategoryRequest(BaseModel):
    pattern: str = Field(min_length=2, max_length=200,
                         description="regex matched against item_desc, case-insensitive")
    category: str = Field(min_length=2, max_length=40)
    priority: int = Field(default=500, ge=1, le=9999)

    @model_validator(mode="after")
    def pattern_must_compile(self):
        try:
            re.compile(self.pattern)
        except re.error as e:
            raise ValueError(f"pattern is not a valid regular expression: {e}")
        return self
```

Endpoints in `rules_config.py`:

| Route | Role | Behaviour |
|---|---|---|
| `GET /config/part-categories` | `any_role` | all rules, confirmed and pending, priority order |
| `POST /config/part-categories` | `REVIEW_ROLES` | upsert on `pattern`, resets `confirmed=0` |
| `POST /config/part-categories/{pattern}/confirm` | `APPROVE_ROLES` | 404 if absent, 403 if self-confirm |
| `GET /config/part-categories/coverage?batch_id=` | `REVIEW_ROLES` | `{categorised, uncategorised, pct, by_category, samples[]}` |

`coverage` reads `bom_rows` for the batch, applies confirmed rules, and returns up
to 20 sample uncategorised `item_desc` values — the feedback loop that tells an
engineer which rule to write next.

- **MIRROR**: `propose_criticality` / `confirm_criticality` verbatim, including
  the self-confirm 403 and the `note` in the response body.
- **IMPORTS**: `import re` in `schemas.py`; `from ..part_category import ...` in
  the router.
- **GOTCHA**:
  - `{pattern}` in a path is user text containing regex metacharacters —
    `encodeURIComponent` on the frontend, and rely on Starlette's decoding; do
    **not** hand-parse.
  - Route declaration order: `/config/part-categories/coverage` must be declared
    **before** `/config/part-categories/{pattern}/confirm` is irrelevant (different
    shapes), but keep `coverage` above any future `/{pattern}` GET. The hazard is
    documented at `review.py:132-134`.
  - Re-proposing an existing pattern must reset `confirmed=0` **and** null
    `confirmed_by`, or a silently-edited rule keeps its old approval.
- **VALIDATE**: `GET /config/part-categories` returns 23 seeded rules,
  all `confirmed=1`.

---

### Task 5: Tests — `backend/tests/test_part_category.py`

| Test | Input | Expected | Edge? |
|---|---|---|---|
| `test_default_rules_all_compile` | `DEFAULT_RULES` | every pattern compiles | no |
| `test_priorities_are_unique_and_ordered` | `DEFAULT_RULES` | strictly increasing, no dupes | no |
| `test_specific_beats_generic` | `"SENSOR BRACKET ASSY"` | `sensor`, not `holder`/`assembly` | **yes** |
| `test_abbreviations_match` | `"HT WC COL SOL(L)"`, `"CARRIAGE AB FLOW SNR(L)"` | `valve`, `sensor` | **yes** |
| `test_no_match_returns_uncategorised` | `"ASIO"`→pcb, `"ZZZ"`→`""` | `""` for unknown | **yes** |
| `test_empty_description` | `""`, `None` | `""`, no crash | **yes** |
| `test_case_insensitive` | `"cable assy"` | `cable` | no |
| `test_only_confirmed_rules_are_loaded` | insert `confirmed=0` rule | not returned by `load_rules` | **yes** — the safety property |
| `test_invalid_stored_regex_is_skipped_not_raised` | insert `"("` | skipped, others still work | **yes** |
| `test_seed_populates_on_first_init` | fresh db | 23 rules, `confirmed=1` | no |

### Tests — `backend/tests/test_similarity.py` (UPDATE)

| Test | Expected | Edge? |
|---|---|---|
| `test_different_category_is_never_a_peer` | cable target retrieves no fastener peers | **yes** — the whole feature |
| `test_uncategorised_target_is_not_restricted` | peers of mixed categories still returned | **yes** |
| `test_uncategorised_peer_stays_eligible` | known target may match unknown peer | **yes** |
| `test_category_appears_first_in_reasons` | reason string starts `same part category (` | no |
| `test_category_never_narrated_when_unknown` | uncategorised target → no category reason | **yes** |
| `test_unconfirmed_rule_does_not_affect_retrieval` | propose only → peers unchanged | **yes** |
| `test_part_category_persisted_on_result` | `similarity_result.part_category` populated | no |
| `test_feature_weights_still_sum_to_one` | unchanged at 1.0 | **yes** — guards against re-weighting by accident |

- **MIRROR**: `test_similarity.py`'s existing helpers. Build category-specific
  fixtures by varying `item_desc` in `make_row(item_desc="...")`.
- **GOTCHA**: `conftest.py` pins `BOM_ENGINE=rules`, so `route`/`consumable` are
  blank throughout — irrelevant here since the block is independent of distance,
  but do not assert on distance thresholds in these tests.
- **VALIDATE**: `make test` — all green, 201 + ~18 new.

---

### Task 6: Frontend

- **ACTION**: Config-page lexicon editor; item-page restriction header.
- **IMPLEMENT**:
  - `types.ts`: `PartCategoryRule { pattern; category; priority; set_by; confirmed; confirmed_by; updated_at }`,
    `PartCategoryCoverage { categorised; uncategorised; pct; by_category; samples }`;
    add `part_category: string` and `eligible_peer_count: number` to `SimilarityResult`.
  - `config/page.tsx`: a **Part categories** card below Machine criticality —
    table of rules (pattern, category, priority, status), a propose form, a
    Confirm button per pending rule, and the coverage line with sample
    uncategorised descriptions. Clone the criticality block's handlers.
  - Item page peer card: when `part_category` is set, a header line
    `Peers restricted to: {part_category} · {eligible_peer_count} eligible`.
    When empty, a `Banner kind="info"` explaining no restriction was applied and
    pointing at the Config page.
- **MIRROR**: `config/page.tsx:85-115` (propose/confirm handlers) and `:278-320`
  (criticality table); `Banner` already has a `warning`/`info` kind.
- **IMPORTS**: extend the existing `@/lib/types` import lists.
- **GOTCHA**: `encodeURIComponent(pattern)` in the confirm URL — patterns contain
  `\b`, `(`, `|`. Render patterns in `font-mono`.
- **VALIDATE**: `npx tsc --noEmit`, `npm run lint`, `npm run build`.

---

### Task 7: Schema guard test

- **ACTION**: `test_schema_parity.py` table count 14 → 15, comment updated to
  name `part_category_config`.
- **GOTCHA**: `test_foreign_keys.py` needs **no** change — the new table has no
  FKs. If it fails, a `REFERENCES` clause was added by mistake.
- **VALIDATE**: `make test`.

---

## Testing Strategy

### Edge Cases Checklist

- [ ] Empty / null `item_desc` → `""`, no crash
- [ ] Description matching multiple rules → highest-priority wins
- [ ] `"SENSOR BRACKET ASSY"` → `sensor`, not `holder` or `assembly`
- [ ] Abbreviations `SNR`, `SOL` resolve
- [ ] Unconfirmed rule has zero effect on retrieval
- [ ] Invalid stored regex is skipped, batch still completes
- [ ] Category known, zero eligible peers → `NO_RELIABLE_ANALOGUE`
- [ ] Category unknown → unrestricted, behaves exactly as today
- [ ] `FEATURE_WEIGHTS` still sums to 1.0
- [ ] Re-proposing a confirmed rule resets it to pending
- [ ] Proposer cannot confirm their own rule (403)
- [ ] Existing production `similarity_result` rows gain the column via `_ensure_columns`
- [ ] Concurrent access: N/A — single writer, one connection per request
- [ ] Network failure: N/A — no network call in this feature

---

## Validation Commands

### Static Analysis
```bash
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'backend'); import app.main, app.part_category, app.similarity; print('ok')"
cd frontend && npx tsc --noEmit && npm run lint
```
EXPECT: `ok`, zero errors.

### Unit Tests
```bash
$env:BOM_ALLOW_SQLITE="1"; .venv\Scripts\python.exe -m pytest backend/tests/test_part_category.py backend/tests/test_similarity.py -q
```
EXPECT: all pass.

### Full Suite
```bash
make test
```
EXPECT: 201 existing + ~18 new, no regressions.

### Build
```bash
cd frontend && npm run build
```

### Database Validation
```bash
.venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'backend');from app.db import get_conn,init_db;init_db();c=get_conn();print(c.execute('SELECT COUNT(*) n FROM part_category_config WHERE confirmed=1').fetchone()['n'])"
```
EXPECT: 23. On the live project this also runs the `_ensure_columns` migration
that adds `similarity_result.part_category`.

### Manual Validation
- [ ] Restart backend; `GET /health` 200 (proves the migration ran)
- [ ] `GET /config/part-categories` → 23 confirmed rules
- [ ] `GET /config/part-categories/coverage?batch_id=7` → ~76%
- [ ] `POST /similarity/run {"batch_id":7,"refresh":true}` → `categorised` ≈ 76%
- [ ] Item page for a cable → header "Peers restricted to: cable", every peer a cable
- [ ] Item page for an uncategorised part → info banner, peers unrestricted
- [ ] Propose a rule as engineer → does **not** change retrieval until confirmed
- [ ] Confirm as senior → re-run similarity → retrieval changes
- [ ] Proposer confirming own rule → 403

---

## Acceptance Criteria

- [ ] All 7 tasks complete
- [ ] Largest similarity bucket on the live pool drops from 510 to ≲130
- [ ] Lexicon coverage ≥ 70% on batch 7
- [ ] `FEATURE_WEIGHTS` unchanged and still sums to 1.0
- [ ] Two parts of different **known** categories never appear as peers
- [ ] An uncategorised part is never blocked and never claims a category match
- [ ] Unconfirmed rules provably do not affect retrieval
- [ ] `make test` green; `tsc --noEmit` and `npm run lint` clean

## Completion Checklist

- [ ] Raw SQL through `Conn`, `?` placeholders, `datetime('now')`
- [ ] Both DDL dialects updated; parity test proves agreement
- [ ] `_ensure_columns` handles the existing production `similarity_result`
- [ ] `ValueError` in the domain layer, `HTTPException` only in routers
- [ ] `conn.close()` in `finally` on every endpoint
- [ ] Every write endpoint calls `audit()`
- [ ] Only `confirmed=1` rules are read
- [ ] No new dependency
- [ ] Deliberate simplifications carry a `# ponytail:` comment
- [ ] Nothing from NOT Building

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The block shrinks pools so far that `NO_RELIABLE_ANALOGUE` floods the queue | **High** | Medium | Expected and correct — false peers were never evidence. Measure before/after on batch 7; if coverage is unusable, the lever is a better lexicon, not a looser threshold |
| A wrong rule silently miscategorises a whole family (e.g. `\bSET\b` catching "OFFSET") | Medium | **High** | First-match-wins with generic rules last; `\b` anchors; the coverage endpoint surfaces the distribution; engineers can fix a rule in seconds and re-run |
| 24% uncategorised parts get no benefit | **High** | Low | By design they behave exactly as today. Coverage endpoint drives lexicon growth |
| Positional `_RESULT_INSERT` drift when adding the column | Medium | **High** | Column list, placeholder count and `_summarise` tuple must change together; a test asserts `part_category` is populated |
| Engineer-entered catastrophic regex stalls a run | Low | Medium | 200-char cap, validated at the Pydantic layer, compiled once per run not per row |
| `_ensure_columns` missed → 500s on live Supabase | Medium | **High** | `similarity_result` already exists in production; explicitly called out in Task 2 and in Manual Validation |
| Category and machine family are correlated, so the block adds less than the bucket count suggests | Medium | Low | Bucket split 637→1,241 and 510→126 was measured on real data, not assumed |

## Notes

**Why a filter and not a weighted feature.** The user chose the hard block, and
that choice removes work rather than adding it: a constraint needs no weight, so
`FEATURE_WEIGHTS` stays exactly as specified and still sums to 1.0, and no
existing distance behaviour is re-tuned. The alternative — `part_category` at
0.10 taken from `machine_family` — was rejected because a mismatch would cost
0.10 against a 0.35 threshold, leaving cross-category peers reachable, which is
the complaint.

**Why `holder` and `assembly` are last.** They are the two largest buckets (361
and 171) and their keywords appear inside descriptions of far more specific
parts: `SENSOR BRACKET ASSY` is a sensor, not a bracket and not an assembly.
First-match-wins with generics last is what produces the measured 76%.

**Why seeded rules are `confirmed=1`** while `machine_criticality_config` starts
empty: an empty criticality table means "fall back to the source column", a safe
default. An empty lexicon means the entire feature does nothing. `rule_config`
seeds `active=1` for the same reason. Engineers correct the seed rather than
build from nothing.

**Ordering is load-bearing** in three places now — `echo.py`'s intent routing,
`similarity.py`'s reason ranking, and this lexicon. All three say so in comments.

**Follow-up, out of scope**: once the lexicon matures, re-run the bucket
measurement. If the largest bucket is still >150, the next lever is a
sub-category level (`cable/power` vs `cable/signal`) rather than more weight
tuning.
