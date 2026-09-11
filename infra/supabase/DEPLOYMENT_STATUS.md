# Deployment status — 2026-09-11

Installed on the shared Windows PC at `C:\ProgramData\BOM-Supabase`.

## Internal pilot access

Password gateway configured at http://10.138.215.247:13010 and verified from this
host. The gateway and API require a pilot login. Following a failed TCP test
from Wi-Fi client `172.21.222.159`, a Windows firewall rule was saved on
2026-09-11 for TCP 13010 and private IPv4 clients. The remote retest still failed.
The rule is enabled but **inactive**, with enforcement `CategoryDisabled`.
CrowdStrike Falcon Sensor is registered as a firewall provider owning rule
categories. IT must check the controlling endpoint/network policy; the exact
packet drop location remains unconfirmed. See
[NETWORK_ACCESS_REQUEST.md](NETWORK_ACCESS_REQUEST.md).
Individual accounts `tcb-1` and `epoxy-1`
passed live browser tests:
unique passwords, signed-in identity display, identity retained during role
changes, and spoofed identity headers overridden by the gateway. Both access the
same pilot workspace. Credentials are in `pilot-accounts.local.json` privately.
See [INTERNAL_ACCESS.md](INTERNAL_ACCESS.md) and `Enable-LanFirewall.ps1`.
The first direct Cloudflare attempt failed its TCP/UDP port 7844 checks. Public
sharing now works through the corporate HTTP proxy in Docker container
`bom-pilot-tunnel-1`, containing Python and Cloudflared 2026.9.0. The existing
password gateway remains the tunnel origin. The former Windows processes are stopped.
Authenticated public page, health, batch-list, and pilot-identity checks passed;
anonymous and wrong-password requests were denied. Docker health and clean
stop/start passed. Use `Pilot.ps1 link` for the current temporary URL and
`Pilot.ps1 tunnel-stop` to close this tunnel. `Proxy-Tunnel.ps1` now delegates to
these Docker operations. See [PROXY_TUNNEL.md](PROXY_TUNNEL.md).

## Verified

- Official release `self-hosted/v0.8.1`, commit
  `8c7a4d9dbbaf8b552893822e89d7bf06f33f9220`.
- Thirteen containers running and healthy: eleven Supabase services plus the
  production frontend and backend; PostgreSQL 17.6.
- Studio/gateway at http://127.0.0.1:18000; no database host ports exposed.
- Generated local credentials and deployment files protected by filesystem ACLs.
- Named volumes configured for PostgreSQL, Storage and Studio snippets.
- Corporate proxy configured for both Docker host operations and image downloads.
  Original Desktop settings saved as `docker-settings-before-proxy.json` in the
  protected deployment folder.
- Live SQL, Auth and REST smoke checks passed.
- Eight offline safety tests passed.
- Live backup/restore rehearsal passed using disposable local databases:
  quoted JSON, vector values, identity sequences, ownership, RLS, server-only
  RPC permissions and refusal to overwrite existing tables. Test databases removed.
- Cloud application data migrated: **19 tables, 29,253 rows, 10 batches**.
  Row counts and content fingerprints matched a single exported Cloud snapshot.
  Cloud was checked again after pausing the backend; no changes were missed.
- Source Auth users and Storage objects were both empty at export time.
- Real local backup restored into a disposable database and all 19 table content
  fingerprints matched. Recovery database removed afterward.
- Backend `backend/.env` switched to `http://127.0.0.1:18000`, including the
  separate preference-memory connection. Previous Cloud configuration retained
  privately at `C:\ProgramData\BOM-Supabase\backend-cloud-before-cutover.env`.
- Backend restarted on port 8011. Live health, batch browsing, recommendations,
  and Qwen preference-memory search passed. Frontend remains on port 3010.
- Frontend and backend subsequently moved into the same `bom-supabase` Compose
  project. Previous host development processes stopped. Application build source
  is installed under `C:\ProgramData\BOM-Supabase\app-source`; app containers do
  not mount the OneDrive checkout or depend on its Python environment.
- Production images built successfully with pinned base-image digests. Frontend
  TypeScript and production build passed. Container checks passed for the frontend,
  backend, batches, summary, recommendations and Ollama-backed preference memory.
  A live headless browser displayed 10 batches and opened a batch detail page with
  no JavaScript errors. All existing database containers remained running.
- Everyday operations: [START_HERE.md](START_HERE.md), installed beside
  `Compose.ps1`. `Compose.ps1 start` starts the complete stack; `stop` preserves data.
- Migration evidence is in `C:\ProgramData\BOM-Supabase\migration-report.json`.
  The first real backup and checksum manifest are under
  `C:\ProgramData\BOM-Supabase\backups\bom-public-after-migration-20260911.dump`.

## Still pending

- Copy the verified application backup and manifest to team-controlled storage
  on another machine; the current copy is on this PC only.
- Register the supplied backup task under the designated team account; no task
  has been registered under the departing user's account.
- Complete unattended startup and ownership handover. Docker Desktop currently
  runs under the current Windows user; moving the Compose files does not move
  its WSL disk or make the engine independent of Windows sign-in. Have IT establish
  the supported startup/account arrangement and verify a cold reboot before
  disabling that account. Windows Ollama and its models also need team ownership
  and startup. No Docker Hub account was used to pull the images.

Cloud data has not been deleted or modified by the migration. Local Supabase is
now the backend's active database. If reverting later, reconcile any writes made
locally after cutover before returning to Cloud.

See [README.md](README.md) for commands, backup scope and handover details.
