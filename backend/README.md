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
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8012
# docs: http://127.0.0.1:8012/docs
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
Invoke-RestMethod http://127.0.0.1:8012/health
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
Invoke-RestMethod 'http://127.0.0.1:8012/memory/status?probe=true' -Headers @{'X-User'='alice'; 'X-Role'='engineer'}
```

Expect `enabled: true`, `vector_store: supabase_rest`, and `ready: true`.
Chat and embedding inference run locally; preference text, vectors, and review
history remain in Supabase.

This workspace has a separate Python 3.13 environment because the older `.venv`
references a missing Python 3.12 installation. Start with:

```powershell
.venv-ollama\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8012
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
| `POST /config/criticality` · `.../confirm` | reviewer · senior | Personal criticality proposals and confirmation |
| `GET /export/wings?batch_id=` | engineer/senior/planner/admin/it | Approved-changes-only WINGS CSV |
| `POST /chat` | any | NYRA agent: explain, retrieve, prioritise, stage |
| `GET /pending-changes` | any | The confirmation tray — what chat staged |
| `POST /review/{item_id}/confirm-pending` | engineer/senior/admin | Turn a staged proposal into a decision |
| `POST /review/{item_id}/discard-pending` | engineer/senior/admin | Drop a staged proposal |

The private backend trusts `X-User` / `X-Role` from the frontend, which verifies
the pilot login session and replaces browser-supplied identity headers. Keep the
backend bound to loopback or an internal container network; these headers are not
authentication by themselves. Intel SSO / Entra ID remains a separate integration.

Workspace access requires `batches.uploaded_by` to equal the signed-in username,
for every role, including admin. This applies to lists, direct IDs, uploads,
reviews, exports, chat tools, history and peer evidence. Ownership is set at
creation and cannot be changed by an upload. Imported workspaces keep their
recorded uploader. Old shared assistant/peer caches are discarded on startup
and rebuilt on demand using owner-scoped history; BOMs and decisions are retained.
Cross-account approvals are denied, while the existing self-approval prohibition
still applies, so decisions requiring a second approver remain pending.

All Settings sections are private to the signed-in account: thresholds,
auto-clear, review assistance, machine criticality, part categories, and dormant
rules. Scoring, similarity, coverage, bulk-review gates, and chat use the same
owner's configuration. Admin rights do not bypass this ownership boundary.
Personal proposals still require an explicit confirmation by the owner with
senior/admin rights; they do not require a different account. This changes only
configuration confirmation, not the approval rules for BOM review decisions.

Startup creates additive `user_*` settings tables for SQLite and Postgres. Each
account receives a one-time copy of the previous shared settings on first use;
the old tables remain migration templates and existing scored results are kept.
Later changes and deletions affect only that account and survive restarts.
New PostgreSQL tables enable RLS with no browser policies: only the private
backend's database role accesses them, with account filtering at every entry point.
Restart/redeploy both app services to apply the backend schema and updated UI.
`tests/test_settings_isolation.py` covers two-account reads/writes, confirmations,
deletions, migration persistence, and engine/chat behavior.

Run `tests/test_workspace_isolation.py` for the access matrix, or
`infra/supabase/check_workspace_access.py` from the repository root to verify the
two real pilot accounts through the login gateway without changing BOM data.

## The agent, and what it is not allowed to do

`POST /chat` routes questions to a bounded tool-calling loop (`app/agent/`).
Its read tools retrieve uploaded items, scored recommendations, history and
advisory evidence; write tools stage proposals or browser actions. The loop
executes at most five tools per turn, including parallel tool requests.

Four properties are enforced in code, not by prompt wording, and each has a test
in `tests/test_agent_boundary.py`:

1. **The agent stages; it never decides.** `propose_change` writes only to
   `pending_change`. There is no path from it to `review_history`, so nothing it
   does can reach a WINGS export. A human calls `confirm-pending`, and that goes
   through the *same* insert path as a console decision — same approval gate,
   same audit trail.
2. **It never invents a quantity.** Every proposed number must appear literally
   in the engineer's own message; "bump it a bit" stages nothing. PRD §8.
3. **Factual answers require retrieved records.** Source-free model assertions
   are rejected. Code-owned replies can explain missing input, empty results,
   permissions or unavailable capabilities. If narration fails after retrieval,
   the assistant displays the retrieved records.
4. **Read-only roles are never offered the write tool** — it is absent from the
   tool list sent to the model, not merely rejected afterwards.

Every turn is written verbatim to `conversation_turn`. That is an audit record
and also the ML label corpus: Feature_Selection §12.6 calls for mining engineer
free text as *labels* (as features they leak — OOF AUC 0.987 / 0.923), and the
Jan'26 slice is one month with 97.5% of decisions from a single reviewer.

### Item discovery and response outcomes

`search_items` searches item IDs/descriptions and confirmed part categories,
including before scoring. `list_review_queue` uses the same filters over scored
rows and supports `uncovered_dormant`, engine route and review status. Searches
exclude quarantined uploads, preserve item/stockroom identity, and return
`total_count`, `returned_count`, `offset`, `limit` and `has_more`. Category rules
are evaluated at read time, so peer-analysis caches are not a prerequisite.

Responses include `response_status`, `response_reason` and `fallback`. These
outcomes are also saved in the existing audit detail and restored by the history
endpoint. Tool logs retain empty/error status and summaries. No schema migration
is needed. One failed tool-selection attempt can retry with read tools; it cannot
start an analysis job or stage a write.

Offline regressions: `python -m pytest tests/test_chat_recovery.py -q`.
Read-only live-provider checks against an existing workspace:
`python scripts/check_chat_retrieval.py --batch-id 13`.
See `docs/CHAT_FALLBACK_INVESTIGATION.md` at the repository root for the failure matrix.

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
Invoke-RestMethod http://127.0.0.1:8012/memory `
  -Method Post `
  -Headers @{ "X-User"="alice"; "X-Role"="engineer" } `
  -ContentType "application/json" `
  -Body '{"text":"I don''t stock Phoenix consumables under $50"}'

# search
Invoke-RestMethod "http://127.0.0.1:8012/memory/search?q=Phoenix" `
  -Headers @{ "X-User"="alice"; "X-Role"="engineer" }

# status + active probe
Invoke-RestMethod "http://127.0.0.1:8012/memory/status?probe=1" `
  -Headers @{ "X-User"="alice"; "X-Role"="engineer" }
```

## Chat skills

`GET /chat/skills` returns the authenticated user's skill catalog, descriptions,
usage, and role availability. Both `POST /chat` and `POST /chat/stream` recognize
a leading slash command. The full chat composer supplies the chosen workspace
through `batch_id` and `page_context.batch_id`; they must match.

Eight skills are included: `/brief`, `/triage [count]`, `/explain <item>`,
`/history <item>`, `/review-note <item>`, `/propose <item> [max <value>]
[rop <value>] [min <value>]`, `/dormant-check`, and `/peers <item>`. Item commands
except history support `stockroom <id>`. Propose accepts at least one named whole
number and an optional trailing `reason <text>`. Triage accepts 1–25 (default 10).
The frontend README includes copyable examples.

`app/agent/skills/<name>/SKILL.md` holds each description and synthesis guidance.
The allowlisted registry and argument parser in `app/agent/skills/__init__.py`
bind each command to existing tools. Retrieved records pass through existing
redaction before optional model synthesis, which receives no executable tools.
Echo and model failures use a deterministic record summary. Proposal replies
always come from the actual staged record, and skills cannot approve reviews.
Review-role restrictions are enforced before tool execution. All skills except
brief/history require a scored workspace; history and notes span review cycles.

Skill and tool activity is streamed, returned by the normal endpoint, audited,
and restored from saved chat turns. No database migration is needed. Restart the
backend after editing the cached instruction files. Adding another skill also
requires an explicit registry entry, validated tool plan, and behavior tests;
arbitrary client-supplied skill paths are never loaded.

`tests/test_chat_skills.py` covers both endpoints, workspace isolation, command
validation, role boundaries, model fallback, history, and proposal staging with
an isolated SQLite database.

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


## Screenshot capture and engineer reminders

Nyra's chat composer has a **Capture image reminder** button. Engineers can
also press Ctrl+V in either the full chat input or the floating Ask NYRA box.
The image appears as a removable thumbnail in the typing area. Add an optional
note and press Send (or Enter) to open the editable reminder preview; pasting
alone does not upload the image. Cancelling keeps the attachment and text, and
saving clears the draft. Normal text paste continues to work. Engineers can
also upload/paste/drop an image directly in the reminder editor.
**Engineer reminders** in the sidebar provides a
persistent personal list outside individual review workspaces.

A PNG, JPEG, or WebP image (maximum 5 MB and 20 million pixels) is validated
and sent to the configured vision-capable model using the existing
OpenAI-compatible provider. The installed qwen3.8:latest supports this.
Keep NO_PROXY=localhost,127.0.0.1,::1 when using local Ollama behind the
corporate proxy. If image reading is unavailable or its response is invalid,
the editor offers manual entry. Remove an unreadable file before saving a
reminder without evidence.

Extraction never writes a reminder or invokes agent tools. The engineer
corrects the editable draft, acknowledges checking the screenshot, and saves.
The original image is then stored with the reminder. A screenshot's ambiguous
2:1 remains text for clarification; replenishment dates are not used as
reminder dates. Reminder content never changes Min/Max/ROP or WINGS exports.

Each reminder belongs to the authenticated engineer. Other users, including
administrators and shared-workspace readers, cannot read or edit it or its
screenshot. Postgres RLS prevents browser access to the table; the trusted
backend enforces ownership. The engineer_reminder table is created through the
existing idempotent startup DDL for SQLite, Postgres, and Supabase REST. Both
workspace references use ON DELETE SET NULL, so reminders survive workspace
deletion. Screenshots are stored as bounded base64 text and excluded from list
queries.

Choose a date (evaluated on the Asia/Kuala_Lumpur calendar day) or **next
matching review cycle**. A successful later BOM upload for the same engineer
brings forward open cycle reminders with an exact part + stockroom match;
quarantined rows and incomplete identifiers do not match. This includes an
upload into an older draft workspace. The first matching workspace remains
attached until the reminder is resolved. Editing the part, stockroom, or timing
clears that match and waits for another future upload. Existing uploaded
workspaces do not trigger a newly created reminder.

Matching reminders appear in workspace and item review views. The sidebar
shows a due count, refreshed every minute while the app is visible and on
window focus. Delivery is in-app; no email, Teams, or background push is sent.
Engineers can edit dates to postpone, complete, dismiss, and reopen reminders.

API routes: POST /reminders/extract (multipart image), POST /reminders
(multipart JSON payload plus optional image), GET /reminders,
POST /reminders/{id}/edit, POST /reminders/{id}/status, and
GET /reminders/{id}/image. The create payload's UUID request_id makes save
retries idempotent. List routes support status, part, stockroom, workspace,
due-only filtering, and pagination.

Validation: python -m pytest backend/tests/test_reminders.py from the
repository root. With the frontend dev server running, run
npm --prefix frontend run check:reminders for isolated browser checks.
