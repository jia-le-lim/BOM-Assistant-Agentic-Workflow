# Recovery and moving machines

## Choose the correct recovery path

For the existing healthy host, start the installed services; do not restore a backup over a working database.

For a lost host or a planned move, use a separate machine and Docker engine. Project and container names are fixed, so a second full installation on the same engine is not isolated. The public-schema restore intentionally refuses nonempty application tables. Never bypass that guard on the production database.

Use the deployed image/source snapshot to reproduce the captured service first. Deploy the newer repository only after establishing a working baseline. This separates recovery from a feature/schema release.

## What is portable

The package contains the source snapshot and Git bundle, existing installed runtime, original BOM and analytical files, application backup plus manifest, full logical PostgreSQL archives and cluster roles, non-PostgreSQL volume archives, saved Docker images, and local Ollama model manifests/blobs.

It does not contain a Windows installation, Docker Desktop installer, WSL virtual disk, GPU drivers, Outlook profile/password, Windows login session, corporate network permissions, a live public tunnel identity, or a portable Python virtual environment. The receiving machine must provide those dependencies and access rights. Running saved app images can avoid a source dependency rebuild; rebuilding images still needs package registries or an independently prepared package cache.

## Before moving

1. Verify SHA256SUMS.json using the included verification tool after transferring the complete folder.
2. Establish machine and disk encryption, restricted team access, Docker licensing/installation, Linux containers, working Python 3.13, Git, Node 22 for development/key generation, and Ollama/GPU support.
3. Review the copied model sizes and image architecture. Allocate model memory and disk beyond the database stack itself.
4. Agree the downtime window, final backup time, new URL/IP, administrator, rollback point and who performs WINGS operations.
5. Stop new business writes for the final export, take a fresh backup and retain the original host until acceptance. The current package is a dated handover snapshot.
6. Keep a copy outside the source host and outside a single user's storage lifecycle.

## Restore the captured application on a clean host

The example uses D:\BOM-Handover for the transferred package. Replace it with the actual path. Never copy the private deployment over an existing deployment without reviewing its contents.

~~~powershell
$packageRoot = 'D:\BOM-Handover'
$deployRoot = 'C:\ProgramData\BOM-Supabase'
$pythonExe = 'C:\Program Files\Python313\python.exe'
& $pythonExe "$packageRoot\source\infra\handover\verify_package.py" $packageRoot
if ($LASTEXITCODE -ne 0) { throw 'Package verification failed' }
docker image load --input "$packageRoot\PRIVATE\docker-images\images.tar"
if ($LASTEXITCODE -ne 0) { throw 'Docker image load failed' }
if (Test-Path -LiteralPath $deployRoot) { throw 'Use a clean destination; review existing deployment first' }
Copy-Item -LiteralPath "$packageRoot\PRIVATE\deployment" -Destination $deployRoot -Recurse
~~~

Restrict the new deployment folder's access to team maintainers, Administrators and SYSTEM. Copies do not reliably preserve NTFS ACLs across transfer media. The package is confidential and not encrypted by the checksum process.

Do not start the application yet: its startup DDL would populate public tables and cause the restore guard to refuse the archive. Start the pinned database platform using only the base and database override layers:

~~~powershell
$stack = Join-Path $deployRoot 'runtime\stack'
Set-Location $stack
# Use a clean shell: inherited variables can override .env during raw Compose calls.
docker compose --project-name bom-supabase --env-file .env -f docker-compose.yml -f compose.override.yml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration invalid' }
docker compose --project-name bom-supabase --env-file .env -f docker-compose.yml -f compose.override.yml up -d --no-build --wait --wait-timeout 300
if ($LASTEXITCODE -ne 0) { throw 'Supabase startup failed' }
& $pythonExe "$deployRoot\manage.py" --root "$deployRoot\runtime" restore "$packageRoot\PRIVATE\database\application-public.dump"
if ($LASTEXITCODE -ne 0) { throw 'Application restore failed; do not start the app' }
& $pythonExe "$deployRoot\manage.py" --root "$deployRoot\runtime" verify
if ($LASTEXITCODE -ne 0) { throw 'Database verification failed' }
~~~

After loading images, validate every pinned image reference in the Compose layers. Docker archive transfer may not preserve every registry digest association. If an exact digest is unavailable, pull that exact reviewed digest through the approved network; do not silently remove the pin. Fully offline cold-host startup was not rehearsed.

Inspect runtime/stack/.env privately for host-specific bind paths, ports and secrets. The copied configuration retains the original credentials for this recovery route; rotate them afterward using a coordinated procedure. Do not mix only some newly generated keys with the old configuration.

The standard public restore recreates application objects owned by service_role, enables RLS and restricts RPC permissions. Compare all table counts with the package report, not only the manager's original 19-table minimum. Use the schema SQL and restore report to investigate any mismatch.

Restore Ollama models as described below, then start the app:

~~~powershell
Set-Location $deployRoot
.\Compose.ps1 start
.\Compose.ps1 status
Invoke-RestMethod 'http://127.0.0.1:8011/health'
~~~

Check login, expected owners/workspaces, source and review counts, historical comments, pending decisions, chat retrieval, and an approved export in a rehearsal workspace. Recreating a username does not by itself resolve ownership or cross-account approval constraints.

## Ollama recovery

Install a compatible Ollama version and GPU drivers. Quit the intended Ollama server before copying model files. Choose a team-owned destination such as C:\ProgramData\BOM-Ollama\models, copy the package's PRIVATE/ollama/models contents there, and configure the server process's OLLAMA_MODELS to that path. Restart that server under the team account.

Windows Ollama inherits the environment of its launching account/process; changing variables only in an unrelated terminal does not reconfigure an already running server. See [Ollama configuration guidance](https://docs.ollama.com/faq).

~~~powershell
ollama list
Invoke-RestMethod 'http://127.0.0.1:11434/api/tags'
docker exec bom-supabase-backend-1 python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags',timeout=10).status)"
~~~

The configured chat tag is qwen3.8:latest, and the configured embedding tag is qwen3-embedding:0.6b with 1024 dimensions. Verify both names, saved manifest digests, and a preference-readiness probe after recovery. Do not mix embeddings from different models in the same collection.

At capture, the default Windows store contained qwen3.8, qwen3-embedding and gemma4 manifests. The API instead listed qwen3.8 and qwen3.6. The qwen3.8 digest matched the copied manifest, but the embedding tag was absent from the API. The unused API-advertised qwen3.6 asset was not found in the copied default store. This discrepancy is documented, not repaired by changing the live server.

## LAN and optional public access

The copied LAN override binds 10.138.215.247. A new host may not own that IP. Before starting the gateway, update its LAN configuration using configure_lan.py with the new private IP and actual client CIDRs; the script also renders account files and validates Caddy. The matching firewall change may require IT approval under endpoint policy.

~~~powershell
# Example values must be replaced for the new host/network.
& $pythonExe "$packageRoot\source\infra\supabase\configure_lan.py" --deployment-root $deployRoot --address '10.0.0.25' --clients '10.0.0.0/24'
Set-Location $deployRoot
.\Pilot.ps1 local-start
~~~

Test from a second PC, not only from localhost. Public access is optional; open a new tunnel only when intended. Old quick-tunnel links are not stable recovery endpoints.

Do not automatically reinstall or start the old tunnel monitor from copied settings. It contains the departing user's account/mailbox arrangement. Configure the new monitor owner and approved recipients first. Task XML is evidence of the old setup, not a portable credential or an instruction to email people.

## Full platform recovery limits

The public restore is the tested route because Auth users and Storage objects were both zero at capture. The package also preserves a full logical dump of the postgres database, a separate _supabase database dump, cluster globals, old configuration/keys and non-database volumes for deeper recovery.

Full-platform restoration needs a separate reviewed procedure for the exact pinned release. Do not pipe cluster-globals.sql into a running initialized Supabase cluster or replace its internal schemas blindly. It can change role passwords and ownership. Storage metadata and files must be restored together; encryption material must match protected data. A clean-host full-platform rehearsal was not performed here.

For future deployments that start using Supabase Auth, Storage, Vault or additional schemas/databases, extend the scheduled backup scope before relying on it. The upstream [self-hosting documentation](https://supabase.com/docs/guides/self-hosting/docker) is supporting reference; this repository's reviewed pins and overrides define this installation.

