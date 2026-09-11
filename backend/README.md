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

## Local Ollama chat

The chat router, agent tool loop, suggestions, and assist explanations all use
`app/llm/nyra.py`. Its OpenAI-compatible client can call Ollama without adding
another SDK. The `nyra` provider name identifies this adapter; `/health` also
reports the configured model.

Install and start Ollama, then download the model once:

```powershell
ollama pull qwen3.8:latest
ollama pull qwen3-embedding:0.6b
ollama list
```

Update these entries in your existing `backend/.env`:

```dotenv
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen3.8:latest
LLM_API_KEY=ollama
LLM_TIMEOUT_S=180
NO_PROXY=localhost,127.0.0.1,::1
MEM0_ENABLED=1
MEM0_VECTOR_STORE=supabase_rest
MEM0_COLLECTION_NAME=bom_engineer_memory_qwen3_1024
MEM0_EMBEDDING_MODEL=qwen3-embedding:0.6b
MEM0_EMBEDDING_DIMS=1024
MEM0_LLM_MODEL=qwen3.8:latest
MEM0_TELEMETRY=false
```

Keep `/v1` in the URL. `ollama` is a placeholder key for the client. Append the
loopback hosts to any existing `NO_PROXY` exclusions so local requests bypass
the corporate HTTP proxy. Existing shell variables override `.env`; update
them too if they still select DeepSeek or omit the loopback exclusions.

Restart the backend after editing `.env`, then check:

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
Invoke-RestMethod http://127.0.0.1:8011/health
```

The health response should show `llm_model: qwen3.8:latest`. It reports
configuration only; use a chat question to check inference. Ollama must run on
the same machine as the backend for this localhost URL to work. Initial model
loading can take longer than later requests; adjust `LLM_TIMEOUT_S` if needed.

Preference recall uses Qwen3 embeddings through the same local endpoint.
`supabase_rest` stores explicit, redacted preferences and searches them by
cosine similarity through the application's existing Supabase HTTPS connection.
Set `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (or the legacy service-role key).
This path needs no database password, tunnel, or mem0 OSS package. It preserves
the existing `infer=False` behavior: preferences are saved explicitly, without
an LLM extraction step. The direct `pgvector` path remains available for mem0
OSS deployments.

Apply the `ollama_preference_memory` migration in `supabase/migrations` before
enabling recall on another Supabase project. It creates a server-only table for
1024-dimensional Qwen vectors, with RLS enabled. Queries filter by engineer and
embedding model before ranking. The original 1536-dimensional memory table is
preserved. If it contains preferences, re-embed their text into the new table
before switching; never copy the old vectors. The current project's original
table was empty when this migration was performed.

Check storage and local embeddings together with:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8011/memory/status?probe=true' -Headers @{'X-User'='alice'; 'X-Role'='engineer'}
```

Expect `enabled: true`, `vector_store: supabase_rest`, and `ready: true`.
Chat and embedding inference run locally; preference text, vectors, and review
history remain in Supabase.

This workspace has a separate Python 3.13 environment because the older `.venv`
references a missing Python 3.12 installation. Start with:

```powershell
.venv-ollama\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8011
```

To recreate that environment elsewhere, run `py -3.13 -m venv .venv-ollama`, then
`.venv-ollama\Scripts\python.exe -m pip install -r backend/requirements.txt`.

References: [Ollama API compatibility](https://docs.ollama.com/api/openai-compatibility)
and [Qwen3.8 model](https://ollama.com/library/qwen3.8),
[Qwen3 embedding model](https://ollama.com/library/qwen3-embedding:0.6b).

Validation on 2026-09-10: the chat, assist, agent-boundary, and memory suites
passed (107 tests). Live checks verified a Qwen tool-call round trip, local
1024-dimensional embeddings, persisted preference recall, and engineer
isolation; synthetic preferences were removed after verification. Supabase
confirmed RLS and no browser-role access on the new table. Its advisor reports
the expected no-policy notice for server-only tables and an existing
[`exec_sql` search-path warning](https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable).

## Test

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q      # 73 tests, fully offline
..\.venv\Scripts\python.exe scripts\e2e_smoke.py    # live HTTP workflow (server up)

# before adding or changing any foreign key, prove it holds on real data first:
..\.venv\Scripts\python.exe scripts\check_referential_integrity.py
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
| `MEM0_ENABLED` | off | preference recall using the selected storage transport |
| `MEM0_VECTOR_STORE` | `pgvector` (mem0 OSS) | `supabase_rest` for HTTPS preference recall |
| `MEM0_COLLECTION_NAME` | `bom_engineer_memory` | collection matching the embedding model |
| `LLM_REDACT_PROMPTS` | off (endpoint is internal) | mask PRD §5.1 sensitive fields |
| `BOM_ALLOW_SQLITE` | — | test-only escape hatch, set by conftest |

For the direct Postgres mem0 OSS path, install `backend/requirements-mem0.txt`,
set `MEM0_ENABLED=1`, and provide a **direct Postgres** URL via `MEM0_DATABASE_URL`
(or `DATABASE_URL`). Supabase REST keys (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`)
are not enough for mem0. Ensure pgvector is enabled on the target database
(`CREATE EXTENSION IF NOT EXISTS vector;`).

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

## Referential integrity

Eleven relations are declared foreign keys, enforced on both backends. `ON DELETE`
is chosen per relation because each choice encodes a rule:

- **`CASCADE`** — `bom_rows`, `recommendation_result`, `model_prediction_log`.
  Derived data, reproducible by re-running ingest and score.
- **`RESTRICT`** — `review_history`, `pending_change`. Deleting a batch that
  carries decisions fails loudly rather than shedding them.
- **`SET NULL`** — `conversation_turn.batch_id`, `item_note.origin_*`. Provenance
  only. An `item_note` must outlive its batch — surviving roster rotation is the
  whole reason that table is keyed on `item_id`.

Three links stay soft on purpose and should not be "fixed": `rule_version` is an
immutable stamp (a FK would invite an update and break the audit trail),
`machine_criticality_config.pattern` is a substring match, and `audit_log` is
polymorphic across six tables.

Before changing any constraint, run `scripts/check_referential_integrity.py` —
it drives the real Jan'26 workbook through ingest, score, review and chat, then
counts orphans for every relation. A constraint added blind can turn a silent
key mismatch into a failed batch run.

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

### mem0 API

The memory store is per-user and opt-in. These endpoints never affect WINGS
exports or review history — they only support advisory recall.

```powershell
# store a preference
Invoke-RestMethod http://127.0.0.1:8011/memory `
  -Method Post `
  -Headers @{ "X-User"="alice"; "X-Role"="engineer" } `
  -ContentType "application/json" `
  -Body '{"text":"I don''t stock Phoenix consumables under $50"}'

# search
Invoke-RestMethod "http://127.0.0.1:8011/memory/search?q=Phoenix" `
  -Headers @{ "X-User"="alice"; "X-Role"="engineer" }

# status + active probe
Invoke-RestMethod "http://127.0.0.1:8011/memory/status?probe=1" `
  -Headers @{ "X-User"="alice"; "X-Role"="engineer" }
```

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
  tests/               73 tests: workflow, boundary, foreign keys, schema
                       parity, SQL dialect
  scripts/
    e2e_smoke.py       live HTTP workflow driver, real Jan'26 file
    proxy_tunnel.py    CONNECT tunnel so psycopg can reach Supabase here
    check_referential_integrity.py  orphan check before touching constraints
  data/                SQLite DB — test artifacts only (gitignored)
```
