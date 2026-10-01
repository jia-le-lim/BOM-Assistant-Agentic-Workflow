"""Capture a confidential handover without stopping application services."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "infra/supabase"))
import manage

def run(args, *, output=None, input=None):
    result = subprocess.run([str(x) for x in args], input=input,
                            stdout=output or subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed with exit {result.returncode}; output suppressed")
    return result.stdout or b""

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def copy_tree(source, target):
    excluded = {".git", "__pycache__", ".pytest_cache", "node_modules", ".next"}
    def ignore(directory, names):
        return [n for n in names if n in excluded or n.endswith((".pyc", ".lock"))]
    shutil.copytree(source, target, ignore=ignore)

def fetch_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=15) as response:
        return json.load(response)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--deployment", type=Path, default=Path("C:/ProgramData/BOM-Supabase"))
    parser.add_argument("--models", type=Path, default=Path.home() / ".ollama/models")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("Choose a new output folder; existing packages are never overwritten")
    if not (args.deployment / "runtime/stack/.env").is_file():
        raise RuntimeError("Installed deployment is inaccessible")
    output.mkdir(parents=True)
    if os.name == "nt":
        identity = run(["whoami"]).decode().strip()
        run(["icacls", output, "/inheritance:r", "/grant:r", identity + ":(OI)(CI)F",
             "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"])
    private = output / "PRIVATE"
    private.mkdir()
    inventory = output / "inventory"
    inventory.mkdir()
    (output / "CONFIDENTIAL.txt").write_text(
        "INTERNAL HANDOVER: contains live credentials, BOM business records, database backups, "
        "account files and model assets. Share only through approved restricted team storage. "
        "This folder is not encrypted. Do not commit, email, or publish it.\n", encoding="utf-8")
    status = {"started_utc": datetime.now(timezone.utc).isoformat(), "complete": False}
    write_json(inventory / "capture-status.json", status)
    print("Capturing installed configuration and original business files", flush=True)
    copy_tree(args.deployment, private / "deployment")
    workspace = private / "workspace"
    for relative in ("BOM table", "analysis/output", "backend/models", "frontend/.local"):
        source = REPO / relative
        if source.is_dir():
            copy_tree(source, workspace / relative)
    for component in (REPO, REPO / "backend", REPO / "frontend"):
        for source in component.glob(".env*"):
            if source.is_file() and source.name != ".env.example":
                target = workspace / source.relative_to(REPO)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    if (REPO / "docs.zip").is_file():
        shutil.copy2(REPO / "docs.zip", private / "original-docs.zip")

    deployment = manage.Deployment(args.deployment / "runtime")
    manage.ERROR_ROOT = private / "capture-logs"
    db = private / "database"
    db.mkdir()
    print("Exporting public application backup and full logical database archive", flush=True)
    deployment.backup(db / "application-public.dump")
    for filename, command in (
        ("postgres-full.dump", ["pg_dump", "-U", "supabase_admin", "-d", "postgres", "--format=custom"]),
        ("cluster-globals.sql", ["pg_dumpall", "-U", "supabase_admin", "--globals-only"]),
        ("public-schema.sql", ["pg_dump", "-U", "supabase_admin", "-d", "postgres", "--schema=public", "--schema-only"]),
    ):
        target = db / filename
        partial = target.with_suffix(target.suffix + ".partial")
        with partial.open("xb") as stream:
            deployment.compose("exec", "-T", "db", *command, stdout=stream)
        partial.rename(target)
    catalog = json.loads(deployment.sql("SELECT coalesce(json_agg(t), '[]'::json) FROM "
        "(SELECT tablename,tableowner,rowsecurity FROM pg_tables WHERE schemaname='public' ORDER BY tablename) t;"))
    counts = {}
    for table in catalog:
        name = table["tablename"]
        counts[name] = int(deployment.sql('SELECT count(*) FROM public."' + name.replace('"', '""') + '";'))
    scope = json.loads(deployment.sql("SELECT json_build_object('server_version',current_setting('server_version'),"
        "'auth_users',(SELECT count(*) FROM auth.users),'storage_objects',(SELECT count(*) FROM storage.objects),"
        "'database_bytes',pg_database_size(current_database()));"))
    scope.update(tables=catalog, row_counts_observed_after_dump=counts,
                 consistency="Each pg_dump is consistent individually; exports and file copies are not one atomic cutover snapshot.")
    write_json(inventory / "database.json", scope)
    (inventory / "database-list.txt").write_text(deployment.sql(
        "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname;"), encoding="utf-8")
    extra_databases = deployment.sql("SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY datname;").splitlines()
    for number, database in enumerate(extra_databases):
        filename = "platform-" + str(number) + ".dump"
        with (db / (filename + ".partial")).open("xb") as stream:
            deployment.compose("exec", "-T", "db", "pg_dump", "-U", "supabase_admin", "-d", database, "--format=custom", stdout=stream)
        (db / (filename + ".partial")).rename(db / filename)
    write_json(inventory / "additional-databases.json", [{"database": name, "archive": "PRIVATE/database/platform-" + str(number) + ".dump"} for number,name in enumerate(extra_databases)])
    for archive in ("application-public.dump", "postgres-full.dump"):
        with (db / archive).open("rb") as stream:
            toc = deployment.compose("exec", "-T", "db", "pg_restore", "--list", input=stream.read())
        (inventory / (archive + ".toc.txt")).write_bytes(toc)

    print("Recording Docker image IDs, mounts, versions and source differences", flush=True)
    ids = run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=bom-supabase"]).decode().split()
    ids += run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=bom-pilot"]).decode().split()
    containers = json.loads(run(["docker", "inspect", *ids]))
    write_json(private / "docker-inspect.json", containers)
    public_containers = [{"name": c["Name"].lstrip("/"), "image": c["Config"]["Image"],
        "image_id": c["Image"], "state": {k:c["State"].get(k) for k in ("Status", "Running", "StartedAt")},
        "health":c["State"].get("Health",{}).get("Status","not configured"),
        "ports": c["NetworkSettings"]["Ports"], "mounts": c["Mounts"],
        "restart": c["HostConfig"]["RestartPolicy"]} for c in containers]
    write_json(inventory / "containers.json", public_containers)
    for filename, command in (("docker-version.txt",["docker","version"]),
                              ("compose-version.txt",["docker","compose","version"]),
                              ("docker-disk-usage.txt",["docker","system","df"])):
        (inventory / filename).write_bytes(run(command))
    write_json(inventory / "ollama-api-models.json", fetch_json("http://127.0.0.1:11434/api/tags"))
    write_json(inventory / "ollama-version.json", fetch_json("http://127.0.0.1:11434/api/version"))
    write_json(inventory / "backend-health.json", fetch_json("http://127.0.0.1:8011/health"))
    volumes = sorted({m["Name"] for c in containers for m in c["Mounts"] if m["Type"] == "volume"})
    write_json(inventory / "volumes.json", json.loads(run(["docker", "volume", "inspect", *volumes])))
    volume_dir = private / "volumes"
    volume_dir.mkdir()
    for name in volumes:
        if name == "bom-supabase_postgres-data":
            continue
        print(f"Archiving read-only volume {name}", flush=True)
        target = volume_dir / (name + ".tar.gz")
        with target.with_suffix(".partial").open("xb") as stream:
            run(["docker", "run", "--rm", "--network", "none", "--read-only", "--mount",
                 f"type=volume,source={name},target=/source,readonly", "--entrypoint", "tar",
                 manage.PG_IMAGE, "-czf", "-", "-C", "/source", "."], output=stream)
        target.with_suffix(".partial").rename(target)

    running_images = {c["Image"] for c in containers}
    all_image_ids = run(["docker", "image", "ls", "-aq"]).decode().split()
    images = json.loads(run(["docker", "image", "inspect", *sorted(set(all_image_ids))]))
    selected = [i for i in images if i["Id"] in running_images or any(
        t.startswith(("bom-assistant/", "node:22", "python:3.13")) for t in (i.get("RepoTags") or []))]
    image_args = sorted({tag for i in selected for tag in (i.get("RepoTags") or [i["Id"]])})
    write_json(inventory / "images.json", [{k: i.get(k) for k in
        ("Id", "RepoTags", "RepoDigests", "Created", "Size", "Architecture", "Os")} for i in selected])
    image_dir = private / "docker-images"
    image_dir.mkdir()
    print(f"Exporting {len(selected)} Docker images including running app and available rollback tags", flush=True)
    image_archive = image_dir / "images.tar"
    run(["docker", "image", "save", "--output", str(image_archive) + ".partial", *image_args])
    Path(str(image_archive) + ".partial").rename(image_archive)

    print("Copying local Ollama model manifests and blobs", flush=True)
    copy_tree(args.models, private / "ollama/models")
    model_files = [p for p in args.models.rglob("*") if p.is_file()]
    write_json(inventory / "ollama-files.json", {"source": str(args.models), "files": len(model_files),
        "bytes": sum(p.stat().st_size for p in model_files), "note":
        "Compare copied manifests with ollama-api-models.json; a running server may use a different store."})
    differences = []
    for file in (args.deployment / "app-source").rglob("*"):
        if not file.is_file() or "__pycache__" in file.parts or file.name == ".bom-app-source":
            continue
        relative = file.relative_to(args.deployment / "app-source")
        current = REPO / relative
        installed_hash = hashlib.sha256(file.read_bytes()).hexdigest()
        if not current.is_file() or installed_hash != hashlib.sha256(current.read_bytes()).hexdigest():
            differences.append(str(relative))
    write_json(inventory / "installed-source-differences.json", differences)
    status.update(complete=True, finished_utc=datetime.now(timezone.utc).isoformat(),
        note="Capture complete. Run restore_rehearsal.py and finalize_package.py before transfer.")
    write_json(inventory / "capture-status.json", status)
    print("Private capture complete; application services were not stopped or restarted", flush=True)

if __name__ == "__main__":
    main()

