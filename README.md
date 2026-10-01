# BOM Review Assistant

An agentic workflow that helps engineers review a monthly bill of materials (BOM)
and decide changes to **minimum**, **reorder point (ROP)** and **maximum** stock
quantities. It imports an existing BOM extract, validates it, computes
recommendations, surfaces historical evidence and chat assistance, records human
decisions, and exports eligible changes for WINGS.

> WINGS is a file-exchange boundary. This application **does not** apply changes
> directly to WINGS — the human team performs the downstream import.

---

## Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Quick start (shared Docker host)](#quick-start-shared-docker-host)
- [Local development](#local-development)
- [Testing](#testing)
- [Documentation](#documentation)
- [Security and confidentiality](#security-and-confidentiality)

---

## Overview

The review flow is:

1. An authenticated engineer creates a workspace (draft batch).
2. A BOM extract is uploaded (CSV/XLSX). Headers are normalized, a module filter
   is applied, and invalid rows are quarantined.
3. The **statistical sizing engine** computes Min/ROP/Max from cumulative demand,
   lead time, service-level assumptions and order constraints. A legacy
   rule-based engine is available via `BOM_ENGINE=rules`.
4. Dormant-item policies, peer similarity and chat assistance provide additional
   evidence. Proposed numbers come from code or engineer input — model text is
   advisory only.
5. A human accepts, overrides or rejects each item. High-risk and override
   decisions require separate senior approval.
6. Reviewed, eligible changes are exported for WINGS.

## Architecture

```text
Engineer browser
  -> Next.js console (login, account lookup, identity)
  -> FastAPI backend (ingestion, engine, review, export, chat)
  -> Supabase gateway -> PostgREST -> PostgreSQL
  -> Ollama (local chat + preference embeddings)
```

| Component  | Stack                                  |
| ---------- | -------------------------------------- |
| Frontend   | Next.js (`frontend/`)                  |
| Backend    | FastAPI + SQLAlchemy (`backend/`)      |
| Database   | Supabase PostgreSQL (SQLite in dev)    |
| LLM / chat | Ollama via LangGraph routing           |
| Analysis   | Python sizing + backtest (`analysis/`) |

## Repository layout

| Path        | Purpose                                                       |
| ----------- | ------------------------------------------------------------ |
| `backend/`  | FastAPI app: ingestion, engines, review, export, chat, memory |
| `frontend/` | Next.js review console                                        |
| `analysis/` | Sizing research, calibration and backtest scripts            |
| `infra/`    | Docker/Supabase deployment and handover tooling              |
| `docs/`     | Technical PRDs, data dictionary, and handover guides         |
| `supabase/` | Database configuration                                       |

## Quick start (shared Docker host)

On the shared host, the frontend, backend and Supabase run together in Docker.
Start Docker Desktop and Ollama, then run:

```powershell
cd C:\ProgramData\BOM-Supabase
.\Compose.ps1 start
```

Open <http://localhost:3010>. See the
[Docker operations guide](infra/supabase/START_HERE.md) for status, logs,
stopping, updating and handover.

## Local development

Run from the repository root. Docker can keep running — development uses separate
ports (frontend `:3011`, API `:8012`).

```powershell
make dev
```

Open <http://localhost:3011>; the development API is at
<http://127.0.0.1:8012/docs>. Keep Ollama running for local chat and preference
embeddings.

To enable the local login with the existing pilot accounts, run once:

```powershell
npm --prefix frontend run setup:auth
```

Then open <http://localhost:3011/login>.

### Useful commands

| Command         | Action                                               |
| --------------- | ---------------------------------------------------- |
| `make dev`      | Start backend (`:8012`) + frontend (`:3011`)         |
| `make backend`  | Start the FastAPI backend only                       |
| `make frontend` | Start the Next.js console only                       |
| `make install`  | Install backend (venv) + frontend (npm) dependencies |
| `make test`     | Run the backend test suite                           |

Override ports with `make dev FRONTEND_PORT=3020 BACKEND_PORT=8020`; the
frontend's `BACKEND_URL` follows `BACKEND_PORT` automatically. The Makefile uses
`.venv-ollama` when present, otherwise `.venv` (override with
`make dev VENV_DIR=your-environment`).

On Windows, install GNU Make if it is unavailable, then open a new terminal to
reload `PATH`:

```powershell
winget install --id ezwinports.make --exact --scope user
```

Backend configuration and local model setup are documented in
[backend/README.md](backend/README.md).

## Testing

```powershell
make test
```

This runs the backend `pytest` suite against a local SQLite database.

## Documentation

- [Technical PRD (v3)](docs/PRD_Technical_BOM_Review_Assistant_v3.md)
- [Data dictionary](docs/Data_Dictionary_TCB_Jan26.md)
- [End-to-end codebase overview](docs/CURRENT_CODEBASE_END_TO_END.md)
- [Maintenance handover](docs/handover/README.md)

## Security and confidentiality

- BOM source data (unit price, supplier and engineer names) is classified as
  sensitive and is **excluded from version control** (see [.gitignore](.gitignore)).
- Secrets live in `.env` files that are never committed; `backend/.env.example`
  is the template.
- The confidential export package (private configuration, data backups, Docker
  images and model assets) is excluded from Git. For ownership transfer, see the
  [maintenance handover](docs/handover/README.md).
