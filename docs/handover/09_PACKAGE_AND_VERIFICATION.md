# Package and verification

## Folder map

The exported folder is named BOM_HANDOVER_2026-09-30. Keep its relative paths together when transferring it.

~~~text
BOM_HANDOVER_2026-09-30/
  START_HERE.html                 Browser-readable combined maintenance guide
  START_HERE.md                   Entry point and transfer instructions
  CONFIDENTIAL.txt                Classification and handling note
  SHA256SUMS.json                 Every payload file's size and SHA-256
  source/                        Current working source, lockfiles, tests, docs and tools
    docs/handover/               Maintained Markdown chapters
    infra/handover/              Capture, restore rehearsal and verification tools
  inventory/                     Dated evidence, image IDs, schema/counts and validation
  PRIVATE/
    repository.bundle            Committed Git history and branches
    working-tree.patch           Tracked differences from captured HEAD
    deployment/                  Installed ProgramData deployment and old backups
      app-source/                Installed source, separate from current checkout
      runtime/                   Compose, generated keys, accounts and proxy files
    workspace/
      BOM table/                 Original monthly BOMs and archive
      analysis/output/           Saved analytical outputs
      backend/.env               Private developer environment if present
      frontend/                  Private dev auth/settings if present
    database/
      application-public.dump    Tested application recovery archive
      application-public.dump.json
      postgres-full.dump         Full logical postgres database archive
      platform-0.dump            Additional _supabase database archive
      cluster-globals.sql        Roles and role credentials; highly confidential
      public-schema.sql          Inspectable captured application schema
    docker-images/images.tar     Running images and available project rollback/build images
    docker-inspect.json          Full private container configuration
    volumes/                     Read-only non-PostgreSQL volume tar archives
    ollama/models/               Copied model manifests and blobs
    host/                        Project scheduled-task XML and host configuration evidence
    original-docs.zip            User's pre-existing document archive
~~~

If a file category did not exist on the source host it is not invented. inventory is the authoritative file/capture record; the checksum manifest lists actual files. The source snapshot includes tracked planning history and existing docs. The Git bundle preserves history but does not include uncommitted new handover files; those are present in source.

## Verified evidence

The public archive was restored into a fresh temporary database and matched the live source across **24 tables and 89,415 rows**, using row counts and content fingerprints. Checks also passed for table ownership, RLS, server-only invoker RPC access and refusal to overwrite an existing application schema. The temporary database was removed. Read inventory/restore-verification.json for exact observations and timestamps.

The backend offline suite passed 614 tests, infrastructure suite passed 30 tests, frontend authentication unit suite passed 8 tests, and package-integrity tests passed 5 tests. This did not include a new frontend production build, full browser regression run, separate-host disaster recovery, reboot, external client test or sending monitor email.

The capture includes 24 selected Docker images. The local default model directory contained about 28 GB of files. Exact totals and file sizes are generated in the package inventory. Running application services were not stopped, restarted or upgraded.

## What is intentionally excluded

Virtual environments, node_modules, build caches, Python bytecode, the active PostgreSQL data directory, general personal profile data, registry login credentials, Outlook profile credentials, Windows login credentials, and Docker Desktop's WSL disk are not copied. Dependencies are represented by lockfiles/requirements and saved runtime images. These exclusions avoid treating machine-specific caches and live database files as portable recovery artifacts.

The separately running Ollama API advertised an unused qwen3.6 model that was not in the copied default user store. The copied qwen3.8 manifest matched the API's configured chat model. The configured embedding model is copied but was absent from that API's model list; verify the target model store before claiming full AI readiness.

The package does not automatically make the service independent of the old Windows account. It does not configure off-host backup storage, transfer cloud permissions, deploy new features, send messages, rotate secrets or open a new public endpoint.

## Verify after transfer

Use a working Python 3.13 installation:

~~~powershell
$packageRoot = 'D:\BOM-Handover'
& 'C:\Program Files\Python313\python.exe' "$packageRoot\source\infra\handover\verify_package.py" $packageRoot
~~~

The tool checks missing, changed and unexpected files against SHA256SUMS.json and exits nonzero on failure. It reads model/image files in chunks and can take several minutes. A SHA-256 manifest detects accidental corruption; it is not encryption or a digital signature. Transfer the manifest and package through the same approved controlled process and retain an independently recorded trusted manifest hash if tamper detection is required.

Do not edit the finalized folder and continue treating its manifest as current. Keep working changes in the maintained repository and generate a new handover release.

## Regenerate a later handover

From the maintained source checkout with a functioning deployment and access to its private files, choose a new unused destination:

~~~powershell
$pythonExe = 'C:\Program Files\Python313\python.exe'
& $pythonExe infra\handover\capture_private.py --output 'BOM_HANDOVER_NEW_DATE'
if ($LASTEXITCODE -ne 0) { throw 'Capture failed; do not publish the partial folder' }
& $pythonExe infra\handover\restore_rehearsal.py --package 'BOM_HANDOVER_NEW_DATE'
if ($LASTEXITCODE -ne 0) { throw 'Restore rehearsal failed' }
# Update dated documentation, host/task evidence and validation results first.
& $pythonExe infra\handover\finalize_package.py --package 'BOM_HANDOVER_NEW_DATE'
if ($LASTEXITCODE -ne 0) { throw 'Finalization failed' }
& $pythonExe infra\handover\verify_package.py 'BOM_HANDOVER_NEW_DATE'
~~~

Capture creates a restricted folder on Windows and refuses to overwrite an existing package. It reads live application data, saves images, and uses temporary read-only helper containers for non-database volume archives. Restore rehearsal creates and drops only its own generated verification database.

Host/task evidence and policy/owner fields need a current review each time; do not reuse the 2026-09-30 acceptance claims uncritically. Review copied model-store paths and any newly introduced data sources. A new live service or external dependency may require additional capture steps.

For a final machine cutover, freeze business writes and repeat backup/capture under the agreed maintenance window. The existing package is a consistent application snapshot with independent auxiliary captures, not a continuously synchronized replica.

