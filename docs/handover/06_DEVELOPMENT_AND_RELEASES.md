# Development and releases

## Set up a maintained checkout

Use the package source directory as the exact handover working snapshot. The Git bundle preserves committed history and branches; the new handover files and any working-tree additions are in source and the working-tree inventory. A clone of the bundle alone does not include uncommitted handover documentation.

~~~powershell
git clone 'D:\BOM-Handover\PRIVATE\repository.bundle' 'C:\BOM-Development'
~~~

Alternatively, use the existing team repository after confirming access and the intended revision. Do not continue developing under the departing engineer's personal OneDrive path. Preserve the original working snapshot for comparison before merging any newer upstream changes.

The source Dockerfiles use Python 3.13 and Node 22. Use those versions as the starting development baseline. Some older infrastructure notes say Python 3.11+/Node 16+; that is not an application dependency guarantee. The installer currently calls shutil.rmtree(onexc=...), which also requires a newer Python than the old 3.11 note suggests. Python 3.13 matches the captured working environment.

The old .venv points at a missing Python312 installation. It is intentionally not copied. Recreate environments rather than transferring absolute-path virtual environments.

~~~powershell
Set-Location 'C:\BOM-Development'
& 'C:\Program Files\Python313\python.exe' -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r backend\requirements.txt
npm --prefix frontend ci
~~~

Set up an isolated development database or restore rehearsal. Using separate app ports does not isolate data: pointing development backend/.env at production still lets development modify production rows and run startup DDL.

For local Supabase, deliberately use a generated local URL and server key with DATABASE_URL empty. Never place the server key in frontend settings. Enable local pilot auth with the existing setup script only if that machine has the intended account file and access; it may otherwise depend on the old ProgramData path.

~~~powershell
npm --prefix frontend run setup:auth
make dev VENV_DIR=.venv
~~~

Development frontend is :3011 and backend :8012. If GNU Make is unavailable, run two terminals from the checkout:

~~~powershell
# Terminal 1
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8012
~~~

~~~powershell
# Terminal 2
$env:BACKEND_URL = 'http://127.0.0.1:8012'
npm --prefix frontend run dev -- --webpack --port 3011
~~~

Keep Ollama running for local model functionality. An empty LLM_BASE_URL selects the deterministic offline provider. Do not assume this simulates a real model's routing quality.

## Tests and what they establish

~~~powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests -q
& 'C:\Program Files\Python313\python.exe' -m unittest discover -s infra/supabase -p 'test_*.py' -v
node --test frontend/scripts/pilot_auth.test.mjs
npm --prefix frontend run lint
npm --prefix frontend run build -- --webpack
~~~

Backend conftest establishes an offline boundary before imports: it opts into temporary SQLite, clears both database transports and model configuration, and uses deterministic test behavior. Preserve that import-time isolation. The dedicated statistical-engine tests cover production calculations even though the general workflow fixture selects the legacy rules engine.

At handover, 614 backend tests, 30 infrastructure tests and 8 frontend authentication tests passed. See the package validation record for actual commands and scope. A fresh frontend production build and full browser scenario suite were not part of this documentation/package change.

Browser scripts under frontend/scripts require their expected server and test context. Some create workspaces/settings/reminders. Read each script before execution and use a rehearsal environment. Live access-check scripts can depend on the original named pilot accounts; account lifecycle changes require updating those fixtures.

Useful focused regressions include workspace and settings isolation, shared-workspace boundaries, foreign keys/schema parity, REST transport, statistical engine, agent boundary, chat recovery, memory, export, bulk review, reminders and pilot authentication.

## Schema and feature changes

When adding data, update PostgreSQL and SQLite definitions, startup compatibility for existing databases, serializers, authorization predicates and relevant tests. Restoring the only explicit Supabase migration is not a substitute for startup schema initialization.

Changes to stock calculation require representative regression/backtest evidence, parameter/version records and human review of business implications. Do not treat archived experimental models or a successful chat answer as a validated quantity change. Existing analysis may depend on original workbooks and additional analytical libraries; backend requirements are not a complete lockfile for every research notebook.

When changing chat tools, preserve retrieved-evidence requirements, the tool budget, role allowlists, exact user-specified quantities, human confirmation, and source reporting. Chat instructions cannot override code authorization.

When changing frontend code, read frontend/AGENTS.md and the installed Next.js documentation. This project uses Next.js 16.2.12, React 19.2.4, server-side route handlers and standalone Docker output; older examples can have incompatible APIs.

## Release checklist

1. Choose a revision; record intended scope and rollback compatibility.
2. Run focused tests and required build checks. Review migrations and business behavior.
3. Take and verify a database backup; preserve running image IDs with rollback tags.
4. Stage the reviewed checkout using install_app.py. It replaces generated app-source; archive the previous staged source if needed.
5. Build both images and check exit status before updating running services.
6. Use Compose.ps1 update-app in a maintenance window if startup changes schema or interrupts active reviews.
7. Verify health, login, owner isolation, representative reads, approved exports, and any new feature. Check backend logs for DDL/permission errors.
8. Record deployed source revision, image IDs, database changes, backup and validation evidence. Keep this record beside the next handover inventory.

The captured installed source differs from the checkout in 86 common installed files. The difference list records byte comparisons and is not a complete semantic deployment diff; it does not count new files absent from installed app-source. Do not use it as an automatic merge list.

## Rollback

Application rollback is appropriate only when the old binaries are compatible with the current schema/data. Retag the recorded previous image IDs as the local app tags and recreate the two app services through the existing wrapper. Do not assume an older global-settings build is safe against newer private-settings data.

Database recovery belongs on a clean replacement environment. After local writes, pointing at the old cloud database or an older backup loses those writes unless they are reconciled. Preserve the failing state and latest backup for investigation. A code rollback does not undo WINGS changes already applied outside the application.

