# BOM Review Assistant — Backend

FastAPI implementation of PRD §7, wired to the backtested rule engine
(`analysis/engine/engine.py`, `rule_version 0.2.0-tcb`).

> The engine calculates. NYRA explains. The engineer approves.
> WINGS updates only after approval.

## Run

The application runs on **Supabase Postgres**. There is no SQLite fallback —
without `DATABASE_URL` the server refuses to start rather than silently writing
engineers' decisions to a local file nobody else can see.

```powershell
# 1. configure (once)
copy backend\.env.example backend\.env   # then paste the DB password in

# 2. this machine only: open the tunnel, because all egress must traverse
#    proxy-png.intel.com:912 and libpq has no HTTP-proxy support. The proxy
#    does honour CONNECT to any port, so we terminate the tunnel locally.
.venv\Scripts\python.exe backend\scripts\proxy_tunnel.py --listen-port 5432

# 3. run (separate terminal)
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8011
# docs: http://127.0.0.1:8011/docs
# GET /health reports which database and which LLM this process is wired to
```

A deployed backend with normal egress skips step 2 and points `DATABASE_URL`
straight at the pooler. Details in Backend_Scaffold_Notes.md §F6.

## Test

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q      # 66 tests, fully offline
..\.venv\Scripts\python.exe scripts\e2e_smoke.py    # live HTTP workflow (server up)
```

**SQLite survives only as the test backend.** `tests/conftest.py` opts in via
`BOM_ALLOW_SQLITE=1` and clears `DATABASE_URL`, so the suite stays offline and
secret-free — and a developer with `backend/.env` populated never has the suite
run against the real Supabase project. Do not set `BOM_ALLOW_SQLITE` by hand to
work around a config error.

## Configuration

`backend/.env` is read automatically (existing environment variables win).

| Variable | Unset | Set |
|---|---|---|
| `DATABASE_URL` | **server refuses to start** | Supabase Postgres |
| `LLM_BASE_URL` | `EchoProvider` — deterministic, no network | any OpenAI-compatible endpoint |
| `MEM0_ENABLED` | off; `mem0ai` never imported | pgvector recall layer |
| `LLM_REDACT_PROMPTS` | off (endpoint is internal) | mask PRD §5.1 sensitive fields |
| `BOM_ALLOW_SQLITE` | — | test-only escape hatch, set by conftest |

## Endpoints (PRD §7)

| Endpoint | Role(s) | Purpose |
|---|---|---|
| `POST /upload-bom-file` | engineer/senior/admin/it | CSV → normalize → quarantine → persist |
| `POST /run-recommendation?batch_id=` | engineer/senior/admin/it | Score batch with the rule engine |
| `GET /recommendations?batch_id=` | any | Filter + paginate; `status=` derives workflow state |
| `GET /recommendations/{item_id}?batch_id=` | any | One item: result, status, context, latest review |
| `POST /review/{item_id}?batch_id=` | engineer/senior/admin | accept / override / reject + comment |
| `POST /review/{item_id}/approve?batch_id=` | senior/admin | Senior sign-off (overrides + High risk) |
| `GET /history/{item_id}` | any | Review history across batches (memory layer) |
| `GET /config/rules` · `POST /config/rules` | any · admin | Versioned thresholds; version must change |
| `POST /config/criticality` · `.../confirm` | reviewer · senior | Two-person criticality config |
| `GET /export/wings?batch_id=` | engineer/senior/planner/admin/it | Approved-changes-only WINGS CSV |
| `POST /chat` | any | NYRA agent: explain, retrieve, prioritise, stage |
| `GET /pending-changes` | any | The confirmation tray — what chat staged |
| `POST /review/{item_id}/confirm-pending` | engineer/senior/admin | Turn a staged proposal into a decision |
| `POST /review/{item_id}/discard-pending` | engineer/senior/admin | Drop a staged proposal |

Identity is a **stub**: `X-User` / `X-Role` headers. See Backend_Scaffold_Notes.md —
this must become Intel SSO / Entra ID before any real deployment.

## The agent, and what it is not allowed to do

`POST /chat` runs a bounded tool-calling loop (`app/agent/`): nine read tools
over data the engine already produced, plus exactly one write tool. Capped at
five tool calls per turn.

Four properties are enforced in code, not by prompt wording, and each has a test
in `tests/test_agent_boundary.py`:

1. **The agent stages; it never decides.** `propose_change` writes only to
   `pending_change`. There is no path from it to `review_history`, so nothing it
   does can reach a WINGS export. A human calls `confirm-pending`, and that goes
   through the *same* insert path as a console decision — same approval gate,
   same audit trail.
2. **It never invents a quantity.** Every proposed number must appear literally
   in the engineer's own message; "bump it a bit" stages nothing. PRD §8.
3. **No source, no answer.** An empty tool-result set returns "I don't know"
   regardless of what the model wanted to say.
4. **Read-only roles are never offered the write tool** — it is absent from the
   tool list sent to the model, not merely rejected afterwards.

Every turn is written verbatim to `conversation_turn`. That is an audit record
and also the ML label corpus: Feature_Selection §12.6 calls for mining engineer
free text as *labels* (as features they leak — OOF AUC 0.987 / 0.923), and the
Jan'26 slice is one month with 97.5% of decisions from a single reviewer.

## Memory layer

Two stores, one boundary:

| | Goes to | Why |
|---|---|---|
| "increase 500123456 max to 5" | Postgres — `pending_change` → `review_history` | A decision: exact, replayable, exportable, auditable |
| "I don't stock Phoenix consumables under $50" | mem0 (optional, off) | A preference: fuzzy recall, losing it costs nothing |

`item_note` is keyed on `item_id`, not `batch_id`, deliberately: the monthly
roster is a rotating sample, so an item discussed in January may be absent in
February and return in March. Batch-keyed context would be lost; item-keyed
context carries forward.

Nothing from mem0 is authoritative — `recall_context` labels every hit
`authoritative: false` and the tool layer refuses to base a proposal on one.

## Workflow states

`auto_cleared` → no change proposed, no risk flag — never exported.
`pending_review` → awaiting engineer (includes engine-unflagged rows whose values
would change: `REQUIRE_REVIEW_FOR_ALL_CHANGES` safety switch).
`awaiting_senior` → reviewed; override or High-risk needs a second person.
`reviewed` → final; exported only if values actually differ from current.

## Layout

```
backend/
  app/
    main.py            app factory + routers
    config.py          .env loading, DATABASE_URL guard, safety switches
    db.py              per-dialect DDL + SQL translation shim
    security.py        RBAC stub (X-Role headers)
    audit.py           audit_log writes (every write endpoint)
    redact.py          PRD 5.1 masking (unconditional on the memory path)
    ingestion.py       normalize + quarantine (analysis §7.4 fixes)
    engine_adapter.py  bridge to analysis/engine — the single source of rules
    services.py        status derivation + export assembly
    scoring.py         ML serving seam (no model trained yet)
    memory.py          mem0 wrapper, null by default
    schemas.py         pydantic request models
    llm/               provider seam: protocol, nyra, echo (offline stub)
    agent/             tools, bounded loop, system prompt
    routers/           upload, recommend, review, rules_config, export, chat
  tests/               66 tests: workflow, boundary, schema parity, SQL dialect
  scripts/
    e2e_smoke.py       live HTTP workflow driver, real Jan'26 file
    proxy_tunnel.py    CONNECT tunnel so psycopg can reach Supabase here
  data/                SQLite DB — test artifacts only (gitignored)
```
