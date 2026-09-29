# BOM Assistant: self-hosted Supabase

For everyday full-stack startup, use [START_HERE.md](START_HERE.md):
`C:\ProgramData\BOM-Supabase\Compose.ps1 start` starts the frontend, backend and
Supabase together. The sections below cover database administration and recovery.

For this PC's completed migration and remaining handover work, see
[DEPLOYMENT_STATUS.md](DEPLOYMENT_STATUS.md). The backend now uses local Supabase;
the export/restore instructions below are for recovery or a new deployment.

This deployment uses the official Supabase Docker stack, pinned to
`self-hosted/v0.8.1`, commit `8c7a4d9dbbaf8b552893822e89d7bf06f33f9220`.
It includes PostgreSQL `17.6.1.136`, pgvector, Studio, REST, Auth, Storage,
Realtime, and the upstream gateway and supporting services.

The gateway is **http://127.0.0.1:18000**. PostgreSQL and the pooler have no
host-published ports. Only the backend should use the server API key.
Colleagues access the BOM web application, not the database administration API.
Remote Studio access can use Remote Desktop on this host.

## Current migration scope

Read-only Cloud inspection on 2026-09-11 found PostgreSQL 17.6, 19 public
application tables, zero Auth users, and zero Storage objects. The two memory
collections use pgvector in `extensions`. The `exec_sql` RPC is SECURITY INVOKER.

The provided export and backup commands cover **the public application schema
only**, including tables, data, sequences, indexes, constraints, functions,
and policies. This intentionally preserves the destination's Supabase internals.
They are NOT full-platform backups: Auth users, Storage files, custom roles,
Vault secrets and other schemas are outside their scope. Recheck that scope
before cutover. If those features acquire data, use Supabase's full migration
procedure and add separate Storage/configuration/key backups.

## Requirements and ownership

- Python 3.11+ (system installation), Git, Node 16+, Docker with Linux containers.
- Docker Compose supporting `!override` (2.24.4+); current PC has Compose 5.5.1.
- Allow room for image downloads and database growth; upstream recommends 8 GB+
  RAM, 4+ CPU cores and 80 GB+ SSD for its stack. Leave additional resources for Ollama.
- Use a machine-level deployment folder, such as `C:\ProgramData\BOM-Supabase`.
  Keep the live configuration and backups outside personal profiles and OneDrive.
- Restrict its ACL to administrators, SYSTEM and designated maintainers. `.gitignore`
  prevents Git commits; it is not an access-control or encryption mechanism.
- Use the company's Docker Desktop licensing arrangement. No personal Docker Hub
  login is required for public image pulls, subject to organization policy/rate limits.

**Windows startup limitation:** container restart policies work only after the
Docker engine starts. Docker Desktop's normal autostart is tied to Windows sign-in.
This package does not bypass that limitation or configure automatic Windows login.
Before unattended handover, IT must provide a supported engine/startup arrangement
(for example an automatically booted Linux VM), or explicitly operate Desktop under
a team-managed Windows account. Test a cold reboot with your account signed out.
The BOM backend, frontend and Ollama need their own startup arrangements too.

## Prepare and start

From the repository root, using a working Python installation:

```powershell
$pythonExe = 'C:\Program Files\Python313\python.exe'
$manager = '.\infra\supabase\manage.py'
$deployRoot = 'C:\ProgramData\BOM-Supabase\runtime'
& $pythonExe $manager --root $deployRoot setup
& $pythonExe $manager --root $deployRoot up
& $pythonExe $manager --root $deployRoot status
& $pythonExe $manager --root $deployRoot smoke
```

For the machine-level installed copy, set `$manager` to
`C:\ProgramData\BOM-Supabase\manage.py` instead. Setup clones the pinned official
source, normalizes Linux script line endings, generates fresh passwords and JWT
keys, applies the local override, and validates the merged Compose configuration.
It generates asymmetric keys using the Node implementation from the pinned
upstream key-generation script. Keys are never printed.

Studio credentials: `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` in
`$deployRoot\stack\.env`. Do not publish this file. A repeat setup refuses to
overwrite a completed deployment. An interrupted initial setup may be resumed.

Data lives in Docker named volumes, including `bom-supabase_postgres-data`,
`bom-supabase_storage-data` and `bom-supabase_db-config`. The last contains
database configuration and encryption material. Never use `docker compose down -v`,
volume pruning, Docker factory reset, or the upstream `reset.sh` on this deployment.
This package deliberately provides `stop`, not a destructive reset command.

## Export Cloud and restore the local copy

1. Copy `.env.example` to a protected `source.local.env` file outside Git and fill
   `MIGRATION_DATABASE_URL` from Supabase **Connect > Session pooler**, including
   the database password. API keys cannot replace this password. Use port 5432;
   the exporter rejects transaction pooler port 6543.
2. Where corporate routing requires the existing HTTP CONNECT tunnel, run the
   tunnel on this PC and use `host.docker.internal:5432` in the export connection.
   A container's `localhost` is not this Windows host. Keep `sslmode=require`.
   URL-encode special characters in the password. Do not put it in command history.
3. Run the commands below with the BOM backend stopped for the **final** export.
   A rehearsal export is fine while Cloud stays live, but is not a final cutover.

```powershell
& $pythonExe $manager --root $deployRoot export-cloud --source-env 'C:\ProgramData\BOM-Supabase\source.local.env' 'C:\ProgramData\BOM-Supabase\exports\cloud-public.dump'
& $pythonExe $manager --root $deployRoot restore 'C:\ProgramData\BOM-Supabase\exports\cloud-public.dump'
& $pythonExe $manager --root $deployRoot verify
```

Export is read-only and sends the password through the child process environment,
not command-line arguments. An archive has a SHA-256 `.dump.json` manifest. Keep
both files. The checksum detects file corruption; only restore trusted archives.

Restore only targets the local Compose database and **refuses a nonempty public
schema**. It installs required extensions, restores application objects owned by
`service_role` in a transaction, enables RLS and restricts the RPC and application
tables to the server role. The current backend performs additive DDL at startup,
so this ownership is needed; a future separation of schema migrations and runtime
permissions can remove that requirement.

`verify` checks the 19 expected tables, RLS, a successful server-key SQL RPC,
anonymous RPC denial, and prints local row counts. Compare counts with the source
while writes are paused, then test BOM browsing, chat, proposal confirmation and
preference recall. A successful HTTP check alone does not establish data parity.

If a rehearsal already populated the local instance, use a separate host/engine
for the final rehearsal or have a maintainer perform a reviewed backup-and-reset.
The scripts intentionally cannot overwrite that data. Project/container names are
fixed, so multiple deployments on the same Docker engine are not supported.

## Switch the application after validation

Merge `$deployRoot\backend.local.env` into the backend's private environment.
It sets the local URL and generated opaque server key and clears the Cloud/direct
connection selectors. Preserve LLM and memory model settings from the existing
environment. In particular, preserve the Qwen collection name, model and dimensions.

Remove stale inherited `DATABASE_URL`, `MEM0_DATABASE_URL`, and
`SUPABASE_SERVICE_ROLE_KEY` values from the app service environment: existing
environment variables override the backend `.env` file. Add loopback entries to
any existing `NO_PROXY` list, rather than discarding corporate exclusions.
Restart the backend and check both `/health` and `/memory/status` (see the
backend route definitions if its mount prefix changes).

The manager does not change `backend/.env`, stop your existing application, or
write to/delete the Cloud database. Retain Cloud until migration acceptance.
If reverting after local writes, reconcile those writes first; simply pointing
back to Cloud would abandon them.

## Backups and restore rehearsal

```powershell
& $pythonExe $manager --root $deployRoot backup 'D:\BOM-Backups\bom-public-20260911.dump'
```

Each archive is a transactionally consistent PostgreSQL public-schema snapshot.
A failed dump remains `.partial` and is never published as a completed archive.
Backups never overwrite files and are not automatically deleted. Keep a protected
copy off this PC; a second folder on the same disk is not disaster recovery.
Recommended starting policy: daily backups, 14 daily copies and 8 weekly copies,
with cleanup only after confirming a recent restore works. Check job failures,
backup age, and free disk space. Daily backups can lose up to a day's changes.

To install the Windows daily backup task under the designated account:

```powershell
.\infra\supabase\Register-BackupTask.ps1 -PythonExe $pythonExe -DeploymentRoot $deployRoot -BackupDirectory '\\TEAM-SERVER\BOM-Backups' -TaskUser 'DOMAIN\TEAM-SERVICE-ACCOUNT'
```

Use the installed script's absolute path for the machine-level copy. This prompts
locally for that account's credentials; do not put a password in command history.
It refuses to replace an existing task. Test that the account can access both
Docker and the backup share while you are signed out. A scheduled task does not
start an unavailable Docker engine. Review Task Scheduler's `LastTaskResult` and
backup freshness; this package does not install an alert-delivery service.

Rehearse recovery on a clean second host/engine: prepare the same pinned release,
start it, restore the archive and manifest, run `verify`, then exercise the app.
Newly generated local API keys are fine for application-only recovery. For a
full-platform recovery, preserve `.env`, deployment configuration, encryption keys,
Storage files and other database schemas separately. Do not tar/copy a running
PostgreSQL data directory as if it were a consistent backup.

## Updates and handover

Pin updates deliberately. Review the upstream changelog and upgrade instructions,
take a verified backup, test on a separate engine, update the recorded tag/commit
and image expectations, then schedule the production change. Do not run upstream
`update.sh` unattended against this package: its edits must be reconciled with
the override and reviewed pin. A major PostgreSQL upgrade requires its documented
upgrade process; replacing the image tag is insufficient.

Before departure, record the host name, application URL, runtime folder, Docker
volume locations, designated primary/backup maintainers, secret-store location,
backup location, retention policy and patch owner. The successor must demonstrate
a reboot, application startup and backup restore without your account.

## Validation

```powershell
& $pythonExe -m unittest discover -s infra/supabase -p 'test_*.py' -v
& $pythonExe $manager --root $deployRoot config-check
```

Offline tests cover archive integrity, failed/incomplete backups, existing-backup protection,
nonempty-target protection, schema filtering, credential handling and pooler choice.
Live `verify` requires a running stack and completed database import.

An optional live rehearsal exercises the real backup/restore commands in two
new disposable databases on the local cluster, then drops only those databases:

```powershell
& $pythonExe .\infra\supabase\check_live_restore.py --root $deployRoot
```

It verifies quoted JSON data, pgvector values, identity sequence continuity,
service-role ownership, RLS, RPC permissions, and refusal to overwrite a populated
target. It does not replace final Cloud-to-local data comparison or app testing.

Failed subprocesses write a private diagnostic file under `runtime/logs`. These
logs may contain database statements or secrets; inspect locally and redact before
sharing. They are not application audit logs.

References: [official deployment](https://supabase.com/docs/guides/self-hosting/docker),
[Cloud migration](https://supabase.com/docs/guides/self-hosting/restore-from-platform),
[updates](https://supabase.com/docs/guides/self-hosting/updating),
[Docker Desktop startup](https://docs.docker.com/desktop/settings-and-maintenance/settings/).
