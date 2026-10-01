# Daily operations and Docker

## Host and service inventory

The installed root is `C:ProgramDataBOM-Supabase`. The original development checkout is under the departing engineer's OneDrive profile. Runtime containers do not mount that checkout or depend on its virtual environments. Use the installed root for operations.

| Endpoint | Intended use |
|---|---|
| http://localhost:3010 | Production frontend from the shared host |
| http://127.0.0.1:8011/health | Private backend health |
| http://127.0.0.1:8011/docs | Private backend API reference; direct calls can mutate records |
| http://127.0.0.1:18000 | Supabase administration gateway / Studio, private |
| http://10.138.215.247:13010 | Captured LAN gateway address; recheck after moving host or network |
| http://127.0.0.1:13010 | Local Caddy gateway |
| http://127.0.0.1:13011 | Windows tunnel monitor status |
| http://127.0.0.1:11434 | Ollama on Windows |
| http://localhost:3011 and http://127.0.0.1:8012 | Development frontend and API |

No PostgreSQL or pooler port is published to the host. Admin SQL uses `docker exec` into the database container. Keep frontend, backend and database administrative ports private. Exposing the backend directly would bypass the frontend authentication boundary.

## Start and inspect

Open Docker Desktop with Linux containers and wait for its engine to be ready. Start the intended Ollama installation and models.

```powershell
Set-Location 'C:\ProgramData\BOM-Supabase'
.\Compose.ps1 start
.\Compose.ps1 status
Invoke-RestMethod 'http://127.0.0.1:8011/health'
```


Expected main services are frontend, backend, db, api-gw, rest, auth, storage, imgproxy, realtime, meta, studio, functions and supavisor. Exact images, ports and statuses are in the package inventory. HTTP health confirms the backend is responding; it does not establish database parity, user access, AI readiness or successful exports.

Caddy and the tunnel have their own controls:

```powershell
.\Pilot.ps1 local-start
.\Pilot.ps1 status
# Only when public pilot access is intended:
.\Pilot.ps1 start
.\Pilot.ps1 link
```


`local-start` starts the gateway without opening a new tunnel. `start` verifies the authentication gate, opens the public tunnel and enables monitor recovery. It can consequently cause the separately running monitor to send configured notices. Do not use it as a generic health check.

## Logs and safe shutdown

```powershell
.\Compose.ps1 logs -Service backend
.\Compose.ps1 logs -Service frontend
.\Pilot.ps1 logs
docker logs --tail 100 supabase-db
docker system df
```


Ctrl+C exits the log follower, not the service. Logs can contain prompts, row identifiers, database statements or credentials from failures. Inspect locally and redact before sharing.

For an intentional shutdown, first close or pause the pilot so it does not advertise a stopped app:

```powershell
.\Pilot.ps1 stop
.\Compose.ps1 stop
```


These stop processes and retain volumes. Do not run `docker compose down -v`, volume pruning, the upstream reset script, or Docker Desktop factory reset on this installation. Do not manually delete the WSL virtual disk.

## Persistent storage

| Volume | Contents and recovery treatment |
|---|---|
| `bom-supabase_postgres-data` | Database cluster files; back up through logical dumps, not a live directory copy |
| `bom-supabase_db-config` | Database configuration and encryption material; confidential |
| `bom-supabase_storage-data` | Supabase object files; a database dump alone cannot restore object bytes |
| `bom-supabase_studio-snippets` | Saved Studio snippets |
| Other volumes in inventory | Preserve their recorded purpose; do not assume unnamed/new volumes are disposable |

The handover includes read-only tar archives of non-PostgreSQL volumes. The old cluster's configuration/encryption archive is for full-platform recovery planning; do not blindly overwrite freshly generated keys in an application-only restore.

## Application update

Take a new backup before updates that change schema or behavior. Record the current running image IDs and give them explicit rollback tags before building; the `:local` tag will move.

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backendImage = docker inspect --format '{{.Image}}' bom-supabase-backend-1
$frontendImage = docker inspect --format '{{.Image}}' bom-supabase-frontend-1
docker tag $backendImage "bom-assistant/backend:rollback-$stamp"
docker tag $frontendImage "bom-assistant/frontend:rollback-$stamp"
# From the maintained repository root:
& 'C:\Program Files\Python313\python.exe' infra\supabase\install_app.py --deployment-root 'C:\ProgramData\BOM-Supabase'
Set-Location 'C:\ProgramData\BOM-Supabase'
.\Compose.ps1 build
# Continue only if the build exited successfully:
.\Compose.ps1 update-app
.\Compose.ps1 status
```


The installer replaces only its recognized generated `app-source` directory and preserves existing `runtime/backend.docker.env`. A build does not replace running containers. `update-app` replaces the app containers, and backend startup can perform additive DDL and invalidate old advisory caches. Frontend replacement signs users out.

Version-tagged rollback images restore application binaries, not database state. Validate schema compatibility before pointing `:local` back to a recorded rollback tag and running `update-app`. A data rollback requires the database recovery procedure and reconciliation of subsequent writes.

## Supabase and dependency upgrades

This project pins Supabase `self-hosted/v0.8.1`, commit `8c7a4d9dbbaf8b552893822e89d7bf06f33f9220`, and PostgreSQL image `17.6.1.136`. Do not replace these with the current upstream quick-start version during recovery.

Test platform upgrades on a separate engine, review release notes and changes to the base Compose file, merge all overrides, restore a backup, run access checks, and schedule the change. Major PostgreSQL upgrades need an explicit migration plan. App image rebuilds do not upgrade Supabase. The current Compose files use `!override` and raw env-file support; match the captured Compose version or validate both features before using an older version.

Docker's image archive can be loaded with `docker image load --input <archive>`. It preserves packaged images for recovery; volume data is separate. See the [Docker image save reference](https://docs.docker.com/reference/cli/docker/image/save/).

## Daily and periodic care

Daily: check main service health, login and workspace browsing, failed backup jobs, newest backup age, free disk space and tunnel status if used.

Weekly: confirm a protected off-host backup exists, review growing tables/logs and disk use, inspect pending approvals and failed ingestion reports, and check that the operational account can access Docker and backup storage.

Monthly and after material changes: rehearse recovery on a separate host, review account access and departed users, check model/embedding readiness, assess dependency/platform advisories, and test a representative approved export.

After Windows, Docker, network or account changes: perform a cold reboot exercise. Container restart policies operate only after the Docker engine starts. Docker Desktop, Ollama and the Outlook monitor currently depend on the Windows account/startup arrangement.

