# Project and architecture

## Purpose and scope

The application helps engineers review a monthly bill of materials and decide changes to minimum, reorder point and maximum stock quantities. It imports an existing BOM extract, validates it, calculates recommendations, provides historical evidence and chat assistance, records human decisions, and exports eligible changes for WINGS. WINGS is a file exchange boundary: the application does not apply changes directly to WINGS.

The current UI supports draft workspaces, BOM upload, review queues, item review, assistance, configuration, saved chat, and engineer reminders. Features in current source are not necessarily in the captured production image. The installed version is preserved under the private deployment snapshot.

## Runtime layout

```text
Engineer browser
  -> LAN Caddy gateway :13010, or local frontend :3010
  -> Next.js server: login cookie, account lookup, identity replacement
  -> FastAPI backend :8011 on loopback / internal Docker network
  -> Supabase gateway api-gw:8000 -> PostgREST exec_sql -> PostgreSQL 17
  -> Windows Ollama host.docker.internal:11434/v1

Optional external route:
  temporary Cloudflare URL -> Docker tunnel/proxy helper -> Caddy gateway

Windows scheduled monitor:
  local status :13011 -> checks tunnel -> optional recovery and Outlook notice
```


The main Compose project is `bom-supabase`. Its 13 containers are eleven Supabase services plus backend and frontend. The separate `bom-pilot` project contains Caddy and the tunnel. Ollama and the monitor are Windows processes, not members of the main Compose project.

A container is a replaceable running process. An image is its packaged application code and dependencies. A named volume holds persistent files independently of a container. Copying an image does not copy database rows. The runtime folder holds Compose definitions, generated keys, account hashes, proxy settings, and installed application source.

## Business flow

1. An authenticated engineer creates a workspace. `batches.uploaded_by` records its owner.
2. Upload accepts CSV/XLSX/XLS filenames, enforces a 100 MiB input limit, normalizes headers, applies the requested module filter, and quarantines invalid rows. Required normalized fields include item ID, Max, ROP, Min and unit price. Parser support for a particular legacy workbook must be verified with that file.
3. `bom_rows` stores source payloads. Duplicate item/stockroom keys, missing IDs and inconsistent demand histories are not silently accepted for scoring.
4. The default statistical sizing engine calculates quantities using cumulative demand, lead time, service assumptions and order constraints. `BOM_ENGINE=rules` selects the legacy rules engine.
5. Dormant policies handle no-consumption cases. Peer similarity and review assistance provide additional evidence. Assistance verdicts and suggested quantities come from code; model-generated explanations are advisory.
6. A human accepts, overrides or rejects. Review records preserve original, calculated and final quantities, actor, comment and version stamps.
7. Overrides and high-risk decisions require a separate senior/admin approval. The owner boundary and prohibition on self-approval can leave these decisions pending in the current pilot; see the acceptance register.
8. Export selects reviewed, eligible changes. Pending approvals and unchanged quantities do not become WINGS update rows. The human team performs the downstream WINGS import.

## Data and authority boundaries

| Data | Authority and maintenance rule |
|---|---|
| BOM source rows | Preserve original files and stored snapshots. Re-upload is not a replacement for historical decisions. |
| Recommendations | Derived calculations, versioned with engine/rules. Re-scoring preserves already reviewed results. |
| Review history | Authoritative human decision record. Do not modify to force an export. |
| Pending changes | Chat proposals awaiting explicit human confirmation; not an approved decision. |
| Audit and conversations | Traceability, including potentially sensitive free text. Apply the team's retention policy. |
| Per-user configuration | Private to its owner. Legacy shared configuration is a migration baseline. |
| Preference vectors | Advisory memory only; never authoritative stock quantities. |
| Reminder screenshots | Current source stores bounded image data in PostgreSQL, privately per engineer. Not Supabase Storage objects. |

The chat system uses LangGraph routing and bounded application tools. BOM facts require retrieved records. Proposed numbers must come from the engineer's input. Chat stages proposals rather than approving stock changes. Slash commands live in the explicit skill registry; copying a new instruction file alone does not register a new tool.

## Source map

| Location | Responsibility |
|---|---|
| [backend/app/main.py](../../backend/app/main.py) | Application startup, schema initialization and route registration |
| [backend/app/db.py](../../backend/app/db.py) | PostgreSQL/SQLite DDL, SQL translation, additive startup changes |
| [backend/app/security.py](../../backend/app/security.py) | Roles and workspace ownership |
| [backend/app/ingestion.py](../../backend/app/ingestion.py) | BOM parsing, normalization and quarantine |
| [backend/app/engine_statistical.py](../../backend/app/engine_statistical.py) | Default sizing calculations |
| [backend/app/engine_adapter.py](../../backend/app/engine_adapter.py) | Load inputs, select engine, save results |
| [analysis/engine](../../analysis/engine) | Legacy engine and seed rule configuration |
| [backend/app/services.py](../../backend/app/services.py) | Review status and export eligibility |
| [backend/app/routers/review.py](../../backend/app/routers/review.py) | Decisions, pending confirmation and approval |
| [backend/app/agent](../../backend/app/agent) | Chat routing, retrieval, tools, skills and grounding |
| [backend/app/assist](../../backend/app/assist) | Evidence gathering and deterministic review assistance |
| [backend/app/memory_rest.py](../../backend/app/memory_rest.py) | Advisory preference embeddings over Supabase REST |
| [backend/app/reminders.py](../../backend/app/reminders.py) | Personal reminder lifecycle and cycle matching |
| [frontend/src/lib/pilot-auth.ts](../../frontend/src/lib/pilot-auth.ts) | Login, bcrypt hashes, sessions and origin checks |
| [frontend/src/app/api/backend](../../frontend/src/app/api/backend) | Trusted browser-to-backend proxy |
| [frontend/src/components](../../frontend/src/components) | Console, review, configuration and chat components |
| [infra/supabase](../../infra/supabase) | Docker installation, backup, pilot gateway and monitor |
| [supabase/migrations](../../supabase/migrations) | Explicit preference-memory migration; not the complete app schema |
| [analysis](../../analysis) | Historical profiling, calibration and backtests |
| [docs](..) | PRDs, design history, data dictionary and analytical evidence |

The project has no automatically retrained production ML artifact. Historical analysis files explain earlier choices; they do not override current code or configuration. Current dependency versions are pinned in the backend requirements and frontend lockfile. Preserve both with every release.

