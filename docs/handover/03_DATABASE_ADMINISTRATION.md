# Database administration

## Active database and connection modes

Production uses self-hosted PostgreSQL through the local Supabase REST SQL RPC. Its gateway is http://api-gw:8000 inside Docker and http://127.0.0.1:18000 from Windows. The server API secret stays on the backend.

The backend supports direct PostgreSQL if DATABASE_URL is nonempty. That setting takes precedence over REST configuration. Direct connections provide ordinary multi-statement transactions. The REST adapter executes each request separately; its Python commit/rollback interface does not make several requests atomic. Account for partial progress when designing operations or retries.

SQLite is for isolated tests only. Do not set BOM_ALLOW_SQLITE=1 to get a broken production installation to start. The old cloud project and pre-cutover environment remain in private historical files; they are not the active data source. Switching to old cloud settings would abandon changes made locally since cutover.

## Schema ownership and migrations

There is no complete sequential migration history for the application. Most schema definition and additive migration logic lives in [db.py](../../backend/app/db.py), called during startup. [The explicit SQL migration](../../supabase/migrations/20260910142548_ollama_preference_memory.sql) adds the Qwen memory collection; it does not recreate the whole application by itself.

At capture, 24 public tables existed, all owned by service_role and with RLS enabled. The manager's hardcoded 19-table set is a minimum baseline, not the full current schema. Its backup uses --schema=public, so additional user tables are included. The current source also creates engineer_reminder, which was absent from the installed database. Inspect the schema archive and live table list before assuming a migration is already applied.

The trusted backend role owns application objects because startup can run DDL. Browser roles must not execute the SQL RPC. The function exec_sql(text,jsonb,boolean) must remain SECURITY INVOKER, executable by the service role and denied to anonymous/authenticated browser roles. RLS alone does not supply the pilot's per-user authorization: the privileged backend enforces owner predicates.

When changing schema: back up, implement both supported test/production dialects, preserve old data, test initialization against an existing schema and an empty database, test role/ownership behavior, and rehearse rollback. Do not silently rewrite historical rule/model version stamps.

## Application table guide

| Tables | Stored records | Care required |
|---|---|---|
| batches | Workspace, owner, file/date/scoring metadata | Owner is a stable identity; admin role does not automatically transfer it |
| bom_rows | Normalized source payload and quarantine reason | Composite identity is batch + item + stockroom |
| recommendation_result | Derived quantities, route, risk, explanation and versions | Linked to source by all three identity fields |
| review_history | Human decision, current/engine/final values and approval | Business audit record; deletion is restricted |
| pending_change | Explicit unconfirmed proposals | Not eligible for export until confirmed through review workflow |
| audit_log, conversation_turn, item_note | Actions, conversations and cross-cycle context | Sensitive free text; preserve provenance |
| user_settings, user_rule_config | Per-owner initialization and active thresholds | First use seeds from legacy baseline |
| user_machine_criticality_config, user_part_category_config, user_dormant_rule_config | Per-owner proposals and confirmed rules | Owner checks apply to reads, writes and confirmation |
| rule_config, machine_criticality_config, part_category_config, dormant_rule_config | Legacy shared settings / migration templates | Do not remove just because user tables now exist |
| assist_result, similarity_result, similarity_neighbour | Advisory evidence caches | Old cache versions may be invalidated on startup |
| triage_result, model_prediction_log | Triage and prediction trace | Versioned derived records |
| bom_engineer_memory, bom_engineer_memory_qwen3_1024 | Preference vector collections | Embedding model and dimensions must match collection |
| engineer_reminder | Current-source personal reminders and screenshots | Additive on deployment; absent from captured live schema |

Foreign keys encode business retention: source/derived rows cascade, decisions and pending changes restrict removal, and provenance references may become null. Notes intentionally outlive a batch. Do not disable constraints to delete a workspace. Inspect the existing referential-integrity script's data and mutation requirements before using it against a live environment.

## Read-only administration

From the shared host:

~~~powershell
docker exec supabase-db psql -X -U supabase_admin -d postgres -c "SELECT tablename,tableowner,rowsecurity FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
docker exec supabase-db psql -X -U supabase_admin -d postgres -c "SELECT pg_size_pretty(pg_database_size(current_database()));"
docker exec supabase-db psql -X -U supabase_admin -d postgres -c "SELECT relname,n_live_tup,n_dead_tup,last_autovacuum,last_autoanalyze FROM pg_stat_user_tables ORDER BY n_dead_tup DESC;"
docker exec supabase-db psql -X -U supabase_admin -d postgres -c "SELECT state,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY state;"
docker exec supabase-db psql -X -U supabase_admin -d postgres -c "SELECT count(*) AS auth_users FROM auth.users; SELECT count(*) AS storage_objects FROM storage.objects;"
~~~

Statistics are estimates where PostgreSQL reports estimates. A rising dead-tuple count or slow query warrants checking autovacuum, long transactions and query plans. Do not schedule blanket VACUUM FULL or rebuild every index: those actions can block work and require additional storage. No new database tuning is applied by this handover.

For concrete query/schema changes, review plans on representative data and include owner and batch filters. Preserve composite identity when joining items. A join on item ID alone can mix stockrooms and review cycles.

## Routine backups

Use a working system Python, not the old broken personal virtual environment:

~~~powershell
$pythonExe = 'C:\Program Files\Python313\python.exe'
$manager = 'C:\ProgramData\BOM-Supabase\manage.py'
$runtime = 'C:\ProgramData\BOM-Supabase\runtime'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
& $pythonExe $manager --root $runtime backup "D:\BOM-Backups\bom-public-$stamp.dump"
if ($LASTEXITCODE -ne 0) { throw 'Backup failed' }
~~~

Choose a real protected backup path before running. Keep both the .dump and .dump.json files. The manager refuses to overwrite an archive, publishes only after a successful custom-format dump, and writes a SHA-256 manifest. A leftover .partial is not a completed backup.

Routine backup scope is the whole public application schema: table definitions, rows, sequences, indexes, constraints and functions. It excludes Auth users, Storage file bytes, non-public schemas, cluster roles, separate databases and host credentials. The handover adds separate full logical database archives, roles, deployment files and non-database volumes; see the package inventory. Do not mistake the normal daily job for that broader capture.

A logical dump provides a consistent snapshot for that dump. Independent dumps, model copies and file archives taken while the app runs are not one atomic cutover. Pause business writes and take a new final backup for an actual move. PostgreSQL describes the snapshot and recovery behavior in its [SQL dump documentation](https://www.postgresql.org/docs/17/backup-dump.html).

## Backup schedule and retention

No BOM database backup task was registered at inspection. A starting operating policy is daily backups, 14 daily copies and 8 weekly copies, with a protected off-host copy and a monthly restore exercise. This is a recommendation for the team to approve, not an existing automated retention policy. The supplied scripts do not delete old backups.

~~~powershell
# Replace the account and destination placeholders first.
$registerScript = 'C:\ProgramData\BOM-Supabase\Register-BackupTask.ps1'
& $registerScript -PythonExe 'C:\Program Files\Python313\python.exe' -DeploymentRoot 'C:\ProgramData\BOM-Supabase\runtime' -BackupDirectory '\\TEAM-SERVER\BOM-Backups' -TaskUser 'DOMAIN\TEAM-SERVICE-ACCOUNT'
Start-ScheduledTask -TaskName 'BOM-Supabase-Backup'
Get-ScheduledTaskInfo -TaskName 'BOM-Supabase-Backup'
~~~

The account needs both Docker access and share write access. The script prompts locally for its credentials, refuses to replace an existing task, and uses an absolute Python path. Test while the departing engineer is signed out. A task cannot back up a Docker engine that is not running.

Monitor task result, backup age, size trends, manifest verification and off-host arrival. A daily schedule implies up to roughly one day's data loss; required recovery time has not been agreed or measured. Record the team's accepted recovery point and recovery time targets in the acceptance document.

## Recovery evidence

The package's real archive is rehearsed in a uniquely named temporary database on the local cluster. Verification compares content fingerprints and row counts for every public table, checks source stability during the comparison, service ownership, RLS, RPC permissions, and refusal to restore over existing data. The generated database is removed afterward.

This proves application archive integrity and the application restore procedure in the current cluster environment. It does not prove a cold reboot, recovery on another machine, restoration of every Supabase internal schema, email delivery, or all end-user flows. Those remain explicit acceptance exercises.

