# Troubleshooting

Work from the first failed dependency outward. Preserve errors privately, record the time, affected account/workspace and last change, and avoid restarting healthy database services repeatedly.

| Symptom | Check and likely next action |
|---|---|
| Docker commands report unavailable engine | Start Docker Desktop, confirm Linux containers and team account access; inspect desktop/WSL state |
| localhost:3010 does not load | Compose.ps1 status; frontend logs; port binding and image health |
| Frontend reports backend unavailable | Backend container status/logs and BACKEND_URL; Docker uses backend:8011 rather than host loopback |
| Login returns 503 | PILOT_AUTH_FILE path, mounted auth.json, JSON/bcrypt validity and file permissions |
| Login fails after frontend restart | Expected in-memory session loss; sign in again |
| Login alias/password mismatch | Check private account alias and hash; changing only plaintext password does not regenerate an existing hash |
| A workspace disappears for a valid user | Compare signed-in identity and batches.uploaded_by; admin does not bypass owner checks |
| Shared reader cannot edit | Expected read-only boundary; do not broaden writes to solve a viewing issue |
| Senior approval remains pending | Check separate-approver rule and owner restriction; current pilot can block cross-account approvals |
| Upload rejected or many rows quarantined | File type/size, required normalized columns, module filter, duplicate item/stockroom and demand-window consistency |
| Valid upload has unexpected scope | Default is TCB; draft workspace fixes module scope; review exact versus tag matching |
| Score changes after settings update | Confirm owner, active rule version/config hash, engine selection, source vintage and reviewed-row preservation |
| Export has fewer rows than queue | Only eligible reviewed changes export; pending approval, unchanged values and rejected changes follow workflow gates |
| REST returns 401/403 | Correct server key, key generation/rotation consistency, RPC grants and role ownership |
| Startup fails with permissions/DDL error | Confirm service_role owns app tables and appropriate schema privileges; compare public-schema.sql |
| Unexpected database selected | Inherited DATABASE_URL overrides REST; shell variables override .env |
| Database looks empty | Confirm actual host/transport and batch ownership before importing anything; never enable SQLite as a workaround |
| Chat is slow or unavailable | Check Ollama endpoint/model, memory/GPU pressure and 180-second timeout; inspect factual fallback response |
| Chat works but preference recall fails | Confirm qwen3-embedding:0.6b appears in the actual server API, vector collection and 1024 dimensions |
| /health says mem0 enabled but recall fails | Enabled is configuration, not a successful embedding/storage probe |
| Reminders absent on production | Captured production predates current reminder table/source; deploy through a reviewed app release |
| Screenshot reading fails | Check vision-capable model and valid image limits; manual reminder entry remains available in current source |
| LAN fails but localhost works | Host IP, Caddy binding, active network profile, firewall/endpoint policy and routing; test from a second PC |
| Gateway says address unavailable | Copied compose.lan.yml binds old host IP; update before gateway startup |
| Public link stops working | Pilot.ps1 status/logs and current link; quick-tunnel URL may rotate; check corporate proxy |
| Intentional tunnel stop is undone | Use Pilot.ps1 tunnel-stop to pause recovery; direct docker stop can trigger monitor restart |
| Monitor page is unavailable | BOM-Cloudflare-Monitor task, Python path and Windows user session |
| Monitor email is retrying | Classic Outlook, configured sending account, connection and automation prompts; submission is not delivery proof |
| Backup job fails | Working Python path, Docker availability/account access, share permissions and free space |
| .partial backup remains | Failed capture; retain diagnostics privately and create a new archive filename after resolving cause |
| Restore says public schema is not empty | Correct protection; use a new host/engine, and do not start backend before restore |
| Python says missing Python312 executable | Old .venv is broken; use system Python313 or rebuild .venv |
| Disk growth | Compare database/table sizes, Docker images/cache, model blobs, backups and analysis outputs before choosing cleanup |

## A minimal diagnostic sequence

~~~powershell
Set-Location 'C:\ProgramData\BOM-Supabase'
.\Compose.ps1 status
.\Pilot.ps1 status
Invoke-RestMethod 'http://127.0.0.1:8011/health'
docker logs --tail 100 bom-supabase-backend-1
docker logs --tail 100 bom-supabase-frontend-1
docker logs --tail 100 supabase-db
docker system df
Get-ScheduledTaskInfo -TaskName 'BOM-Supabase-Backup' -ErrorAction SilentlyContinue
~~~

A read of the protected frontend session/backend route without a session should return 401. This is expected. Do not disable authentication to make a health probe return 200.

For preference diagnostics, an authenticated request through the frontend to /api/backend/memory/status?probe=true tests the configured optional memory path. The same private backend route may be called by a trusted operator locally with the intended identity headers. Do not expose that header-based path over the LAN.

## When to escalate

Ask the database/platform maintainer about restore conflicts, role/permission errors, corrupt archives, unexpected schema changes or unreconciled writes. Ask the application owner about approval policy, ownership transfer and quantity correctness. Ask IT about endpoint firewall policy, Docker/WSL startup, corporate proxy, certificates, account lifecycle and team storage.

Bring the source revision, image ID, operation time, endpoint/status, sanitized error and exact change that preceded the failure. Do not attach a full private environment, Docker inspect or database archive to an ordinary ticket.

