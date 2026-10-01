# BOM Assistant engineering handover

This handover is for the engineer taking responsibility for the BOM Review Assistant, its database, Windows host, Docker services, local AI models, user access, and operational recovery. It accompanies a confidential package containing source, private deployment files, business records, database archives, Docker images, and local model assets. Start with the operating guide before changing the installation.

Prepared on 30 September 2026 from repository commit `8f01d4f014855e821e1a6cbe33b2e0f4d9f012a4` plus the handover documentation and tools added in this working tree. Live observations are a dated snapshot, not a promise of future availability. The package inventory and validation reports record what was actually captured and tested.

## Read in this order

| Document | What it helps you do |
|---|---|
| [Project and architecture](01_PROJECT_AND_ARCHITECTURE.md) | Understand the workflow, code, data ownership, and AI boundaries |
| [Daily operations and Docker](02_OPERATIONS_AND_DOCKER.md) | Start, stop, inspect, troubleshoot and update services |
| [Database administration](03_DATABASE_ADMINISTRATION.md) | Understand schema, permissions, backups, retention and recovery checks |
| [Recovery and moving machines](04_RECOVERY_AND_MIGRATION.md) | Rebuild on another host and restore the captured application |
| [Configuration and access](05_CONFIGURATION_AND_ACCESS.md) | Locate private files, administer users, and maintain network access |
| [Development and releases](06_DEVELOPMENT_AND_RELEASES.md) | Set up development, run tests, change code and deploy safely |
| [Troubleshooting](07_TROUBLESHOOTING.md) | Diagnose failures by symptom |
| [Ownership and acceptance](08_OWNERSHIP_AND_ACCEPTANCE.md) | Complete operational ownership and departure checks |
| [Package and verification](09_PACKAGE_AND_VERIFICATION.md) | Understand package contents, exclusions, hashes and regeneration |

## First hour for the successor

1. Obtain access to the shared Windows host and approved private package storage. The whole package is confidential, including old documentation and source history.
2. Read the inventory and open the deployment's existing configuration locally. Do not paste credentials into a ticket, chat, screenshot or repository.
3. Start Docker Desktop and Ollama under the agreed Windows account. Open PowerShell in `C:ProgramDataBOM-Supabase`.
4. Run `.Compose.ps1 status`, then open http://localhost:3010 and sign in with your own handed-over pilot identity.
5. Confirm that the expected workspaces are visible. A valid login does not imply ownership of another engineer's workspaces.
6. Locate `PRIVATE/database/application-public.dump` and its manifest in the package. Read the restore rehearsal report before calling this a tested recovery package.
7. Arrange the team backup destination, backup account, and cold reboot exercise. These are separate from having a copy of the source.

## What needs attention at handover

The live stack was healthy at inspection, but the installed application differs from the latest checkout. The live database contained 24 public tables, all owned by `service_role` with row level security enabled. `engineer_reminder` was absent; it exists in current source and will be created by that version's startup schema code. The copied installed source and Docker images preserve the deployed version separately from the newer source snapshot.

The `BOM-Supabase-Backup` scheduled task was absent at inspection. `BOM-Cloudflare-Monitor` was running as `jialelim`. A team account, protected off-host backup destination, and reboot test remain owner decisions.

The local Ollama API did not advertise the configured `qwen3-embedding:0.6b` model, although its manifest and blobs exist in the Windows user's default model directory. Treat preference recall as requiring verification. The package preserves local assets; the model guide explains the discrepancy and recovery checks.

Existing project documents contain historical counts and earlier access rules. Use the current source for intended behavior, the captured deployment for installed behavior, and this dated inventory to reconcile them. Nothing in this handover changes production configuration, schedules, accounts, or routing.

