"""Pinned, local Supabase operations. Python 3.11+, Git, Node 16+, Docker Compose.

Never prints secrets, accepts shell command strings, or changes backend/.env.
Backups are application-only public-schema archives; see README for scope.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
TAG = "self-hosted/v0.8.1"
COMMIT = "8c7a4d9dbbaf8b552893822e89d7bf06f33f9220"
PG_IMAGE = "supabase/postgres:17.6.1.136"
PROJECT = "bom-supabase"
ERROR_ROOT = None
TABLES = set("assist_result audit_log batches bom_engineer_memory "
             "bom_engineer_memory_qwen3_1024 bom_rows conversation_turn "
             "dormant_rule_config item_note machine_criticality_config "
             "model_prediction_log part_category_config pending_change "
             "recommendation_result review_history rule_config "
             "similarity_neighbour similarity_result triage_result".split())


def run(args, *, cwd=None, input=None, stdout=subprocess.PIPE, env=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, input=input,
                            stdout=stdout, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        # Command lines, stderr and SQL errors can contain credentials or BOM data.
        detail = "output suppressed because it may contain secrets."
        if ERROR_ROOT is not None:
            path = ERROR_ROOT / f"operation-{time.time_ns()}.log"
            write_private(path, result.stderr.decode("utf-8", errors="replace"))
            detail = f"Private diagnostic log: {path}"
        raise RuntimeError(f"{Path(str(args[0])).name} failed (exit {result.returncode}); "
                           + detail)
    return result.stdout or b""


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    if os.name != "nt":
        path.chmod(0o600)


def jwt(secret, role):
    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=")
    now = int(time.time())
    payload = {"role": role, "iss": "supabase", "iat": now,
               "exp": now + 5 * 365 * 24 * 3600}
    body = b64(b'{"alg":"HS256","typ":"JWT"}') + b"." + b64(json.dumps(payload).encode())
    return (body + b"." + b64(hmac.new(secret.encode(), body, hashlib.sha256).digest())).decode()


class Deployment:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.upstream = self.root / "upstream"
        self.stack = self.root / "stack"

    def compose(self, *args, **kwargs):
        # Do not let a developer's shell silently override generated deployment secrets.
        env = os.environ.copy()
        for key in read_env(self.stack / ".env"):
            env.pop(key, None)
        for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
            env.pop(key, None)
        app_layer = self.stack / "compose.app.yml"
        app_args = ["-f", app_layer] if app_layer.exists() else []
        return run(["docker", "compose", "--project-name", PROJECT,
                    "--env-file", self.stack / ".env", "-f", self.stack / "docker-compose.yml",
                    "-f", self.stack / "compose.override.yml", *app_args, *args],
                   cwd=self.stack, env=env, **kwargs)

    def sql(self, query):
        return self.compose("exec", "-T", "db", "psql", "-X", "-U", "supabase_admin",
                            "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-At",
                            input=query.encode()).decode().strip()

    def setup(self):
        if self.stack.exists():
            if (self.root / "deployment.json").exists():
                raise RuntimeError("Stack already prepared. Use config-check or up; secrets were not changed.")
            actual = run(["git", "-C", self.upstream, "rev-parse", "HEAD"]).decode().strip()
            if actual != COMMIT or not (self.stack / ".env").exists():
                raise RuntimeError("Existing directory is not a resumable setup.")
            self.finish_setup()
            return
        self.root.mkdir(parents=True, exist_ok=True)
        git = ["git", "-c", "core.autocrlf=false"]
        if os.name == "nt":
            git += ["-c", "http.sslBackend=openssl"]
        if not self.upstream.exists():
            run([*git, "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                 "--branch", TAG, "https://github.com/supabase/supabase.git", self.upstream])
        actual = run([*git, "-C", self.upstream, "rev-parse", "HEAD"]).decode().strip()
        if actual != COMMIT:
            raise RuntimeError("Upstream commit differs from reviewed pin; refusing setup.")
        run([*git, "-C", self.upstream, "sparse-checkout", "set", "docker"])
        shutil.copytree(self.upstream / "docker", self.stack)
        # Windows checkout settings must not break Linux entrypoint scripts.
        for path in self.stack.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                if b"\x00" not in data:
                    path.write_bytes(data.replace(b"\r\n", b"\n"))
        values = read_env(self.stack / ".env.example")
        for key in ("POSTGRES_PASSWORD", "JWT_SECRET", "SECRET_KEY_BASE", "PG_META_CRYPTO_KEY",
                    "LOGFLARE_PUBLIC_ACCESS_TOKEN", "LOGFLARE_PRIVATE_ACCESS_TOKEN",
                    "S3_PROTOCOL_ACCESS_KEY_SECRET", "MINIO_ROOT_PASSWORD"):
            values[key] = secrets.token_hex(32)
        values.update(REALTIME_DB_ENC_KEY=secrets.token_hex(8), VAULT_ENC_KEY=secrets.token_hex(16),
                      S3_PROTOCOL_ACCESS_KEY_ID=secrets.token_hex(16),
                      DASHBOARD_USERNAME="bom-admin", DASHBOARD_PASSWORD="Bom" + secrets.token_hex(20),
                      SUPABASE_PUBLIC_URL="http://127.0.0.1:18000",
                      API_EXTERNAL_URL="http://127.0.0.1:18000/auth/v1", SITE_URL="http://localhost:3000",
                      POOLER_TENANT_ID="bom-local", STUDIO_DEFAULT_ORGANIZATION="BOM Team",
                      STUDIO_DEFAULT_PROJECT="BOM Assistant", OPENAI_API_KEY="",
                      DISABLE_SIGNUP="true", ENABLE_EMAIL_SIGNUP="false", ENABLE_PHONE_SIGNUP="false",
                      FUNCTIONS_VERIFY_JWT="true")
        values["ANON_KEY"] = jwt(values["JWT_SECRET"], "anon")
        values["SERVICE_ROLE_KEY"] = jwt(values["JWT_SECRET"], "service_role")
        write_private(self.stack / ".env", "".join(f"{k}={v}\n" for k, v in values.items()))
        self.finish_setup()

    def finish_setup(self):
        # Run the exact Node crypto implementation from the pinned upstream script.
        # Avoid a Bash/WSL dependency and keep JWT_SECRET out of process arguments.
        source = (self.stack / "utils/add-new-auth-keys.sh").read_text(encoding="utf-8")
        match = re.search(r"\$node_runner -e '\n(.*?)\n' \"\$jwt_secret\"", source, re.S)
        if not match:
            raise RuntimeError("Pinned upstream key generator format changed.")
        code = match[1].replace("const jwtSecret = process.argv[1];",
            "const jwtSecret = require('fs').readFileSync('.env', 'utf8')"
            ".split(/\\r?\\n/).find(x => x.startsWith('JWT_SECRET=')).slice(11);")
        values = read_env(self.stack / ".env")
        if not values.get("SUPABASE_SECRET_KEY"):
            generated = run(["node", "-e", code], cwd=self.stack).decode()
            for line in generated.splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            (self.stack / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()),
                                             encoding="utf-8", newline="\n")
        compose_path = self.stack / "docker-compose.yml"
        content = compose_path.read_text(encoding="utf-8")
        for key in ("GOTRUE_JWT_KEYS", "API_JWT_JWKS", "JWT_JWKS", "SUPABASE_JWKS"):
            content = content.replace("#" + key + ":", key + ":")
        compose_path.write_text(content, encoding="utf-8", newline="\n")
        shutil.copyfile(HERE / "compose.override.yml", self.stack / "compose.override.yml")
        self.configure()
        self.validate_config()
        (self.root / "deployment.json").write_text(json.dumps({"tag": TAG, "commit": COMMIT,
            "postgres_image": PG_IMAGE, "project": PROJECT}, indent=2) + "\n", encoding="utf-8")
        print(f"Prepared {self.stack}. Credentials are in its private .env file.")

    def configure(self):
        values = read_env(self.stack / ".env")
        target = self.root / "backend.local.env"
        if target.exists():
            return
        write_private(target, "# Merge these settings into backend/.env after verified migration.\n"
            "DATABASE_URL=\nMEM0_DATABASE_URL=\nSUPABASE_SERVICE_ROLE_KEY=\n"
            f"SUPABASE_URL=http://127.0.0.1:18000\nSUPABASE_SECRET_KEY={values['SUPABASE_SECRET_KEY']}\n"
            "MEM0_VECTOR_STORE=supabase_rest\nNO_PROXY=localhost,127.0.0.1,::1\n")

    def validate_config(self):
        config = json.loads(self.compose("config", "--format", "json"))
        for name, svc in config["services"].items():
            for port in svc.get("ports", []):
                if port.get("host_ip") != "127.0.0.1":
                    raise RuntimeError(f"Unexpected public port on {name}.")
            if svc.get("restart") != "unless-stopped":
                raise RuntimeError(f"Missing restart policy on {name}.")
        mounts = config["services"]["db"]["volumes"]
        if not any(v["target"] == "/var/lib/postgresql/data" and v["type"] == "volume" for v in mounts):
            raise RuntimeError("Postgres data must use a named volume.")
        if config["services"]["db"]["image"] != PG_IMAGE:
            raise RuntimeError("Unexpected database image.")
        print(f"Compose validated: {len(config['services'])} services, PostgreSQL 17, loopback gateway.")

    def up(self):
        self.validate_config()
        try:
            info = json.loads(run(["docker", "info", "--format", "{{json .}} "]))
        except RuntimeError:
            raise RuntimeError("Docker engine unavailable. Start Docker with Linux containers, then retry up.") from None
        if info.get("OSType") != "linux":
            raise RuntimeError("Docker must be running Linux containers.")
        print("Pulling pinned images and starting Supabase; this may take several minutes.", flush=True)
        self.compose("up", "-d", "--wait", "--wait-timeout", "300")
        print("Supabase started. Studio: http://127.0.0.1:18000 (credentials in stack/.env).")

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise RuntimeError("Backup destination already exists; choose a new .dump filename.")
        found = set(self.sql("SELECT tablename FROM pg_tables WHERE schemaname='public';").splitlines())
        if not TABLES <= found:
            raise RuntimeError("Application tables are missing; refusing to publish an incomplete backup.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".partial")
        with open(partial, "xb") as stream:
            self.compose("exec", "-T", "db", "pg_dump", "-U", "supabase_admin", "-d", "postgres",
                         "--format=custom", "--schema=public", "--no-owner", "--no-privileges", stdout=stream)
        with open(partial, "rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise RuntimeError("Backup archive is invalid; partial file retained for diagnosis.")
        partial.rename(destination)
        write_manifest(destination)
        print(f"Application backup saved: {destination}. Copy to protected storage on another machine.")

    def restore(self, archive):
        archive = Path(archive).resolve()
        verify_manifest(archive)
        count = self.sql("SELECT count(*) FROM pg_tables WHERE schemaname='public';")
        if count != "0":
            raise RuntimeError("Restore requires an EMPTY local public schema. Existing data was not changed.")
        self.sql("CREATE SCHEMA IF NOT EXISTS extensions; "
                 "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions; "
                 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions; '
                 "CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions; "
                 "GRANT USAGE, CREATE ON SCHEMA public TO service_role; "
                 "GRANT USAGE ON SCHEMA extensions TO service_role;")
        # One transaction includes import and permissions. Failure leaves no partial app tables.
        # Convert the custom archive inside the running local DB container, never a Cloud target.
        self.compose("cp", str(archive), "db:/tmp/bom-import.dump")
        toc = self.compose("exec", "-T", "db", "pg_restore", "--list", "/tmp/bom-import.dump")
        # Supabase already owns public. Import its objects without attempting CREATE SCHEMA public.
        filtered = filter_restore_toc(toc)
        self.compose("exec", "-T", "db", "sh", "-c", "cat > /tmp/bom-restore.list", input=filtered)
        sql_bytes = self.compose("exec", "-T", "db", "pg_restore", "--no-owner", "--no-privileges",
                                 "--schema=public", "--use-list=/tmp/bom-restore.list",
                                 "--file=-", "/tmp/bom-import.dump")
        script = (b"BEGIN; SET ROLE service_role;\n" + sql_bytes +
                  b"\nRESET ROLE;\n" + (HERE / "app-permissions.sql").read_bytes() + b"\nCOMMIT;\n")
        self.compose("exec", "-T", "db", "psql", "-X", "-U", "supabase_admin", "-d", "postgres",
                     "-v", "ON_ERROR_STOP=1", input=script)
        self.compose("exec", "-T", "db", "rm", "--", "/tmp/bom-import.dump", "/tmp/bom-restore.list")
        print("Application schema restored; REST permissions restricted to service_role.")

    def smoke(self):
        version = self.sql("SHOW server_version;")
        if not version.startswith("17.") or self.sql("SELECT 1;") != "1":
            raise RuntimeError("Database smoke check failed.")
        values = read_env(self.stack / ".env")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for route in ("/auth/v1/health", "/rest/v1/"):
            req = urllib.request.Request("http://127.0.0.1:18000" + route,
                                         headers={"apikey": values["SUPABASE_SECRET_KEY"]})
            with opener.open(req, timeout=15) as response:
                if response.status != 200:
                    raise RuntimeError("Supabase API smoke check failed.")
        print(f"Live smoke checks passed: PostgreSQL {version}, SQL query, Auth and REST APIs.")

    def verify(self):
        found = set(self.sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;").splitlines())
        if not TABLES <= found:
            raise RuntimeError("Missing application tables: " + ", ".join(sorted(TABLES - found)))
        unsafe = self.sql("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity;")
        if unsafe != "0":
            raise RuntimeError("Application tables without RLS found.")
        values = read_env(self.stack / ".env")
        body = json.dumps({"q": "SELECT 1 AS ok", "params": [], "want_rows": True}).encode()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for key_name, should_pass in (("SUPABASE_SECRET_KEY", True), ("ANON_KEY", False)):
            key = values[key_name]
            headers = {"apikey": key, "Content-Type": "application/json"}
            if key_name == "ANON_KEY":
                headers["Authorization"] = "Bearer " + key
            req = urllib.request.Request("http://127.0.0.1:18000/rest/v1/rpc/exec_sql", body, headers)
            try:
                with opener.open(req, timeout=15) as response:
                    result = json.load(response)
                if not should_pass or result != [{"ok": 1}]:
                    raise RuntimeError("Unexpected RPC access or response.")
            except urllib.error.HTTPError as exc:
                if should_pass or exc.code not in (401, 403, 404):
                    raise RuntimeError("RPC verification failed.") from None
        for table in sorted(found):
            quoted = table.replace('"', '""')
            count = self.sql(f'SELECT count(*) FROM public."{quoted}";')
            print(f"{table}: {count}")
        print("Verified: required tables, RLS, service-key RPC, anonymous RPC denied.")


def filter_restore_toc(toc):
    return b"\n".join(line for line in toc.splitlines()
                       if not re.search(rb"\bSCHEMA - public\b|\bCOMMENT - SCHEMA public\b", line)) + b"\n"


def write_manifest(path):
    with open(path, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    metadata = {"sha256": digest,
                "created_utc": datetime.now(timezone.utc).isoformat(), "scope": "public",
                "postgres_major": 17, "deployment_commit": COMMIT}
    Path(str(path) + ".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def verify_manifest(path):
    metadata = json.loads(Path(str(path) + ".json").read_text(encoding="utf-8"))
    with open(path, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if metadata.get("scope") != "public" or metadata.get("sha256") != digest:
        raise RuntimeError("Archive checksum or scope does not match its manifest.")


def export_cloud(source_env, destination, snapshot=None):
    """Read-only export; password is passed by environment, never on command line."""
    source = read_env(source_env).get("MIGRATION_DATABASE_URL", "")
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname or not parsed.password:
        raise RuntimeError("Source file needs MIGRATION_DATABASE_URL with database credentials.")
    if parsed.port == 6543:
        raise RuntimeError("Use the session pooler (5432) or direct endpoint, not transaction pooler 6543.")
    if any(k not in {"sslmode", "connect_timeout", "application_name"}
           for k, _ in urllib.parse.parse_qsl(parsed.query)):
        raise RuntimeError("Only sslmode, connect_timeout and application_name URL options are supported.")
    env = os.environ.copy()
    env["PGPASSWORD"] = urllib.parse.unquote(parsed.password)
    env["PGCONNECT_TIMEOUT"] = "15"
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{parsed.username}@{host}:{parsed.port or 5432}"
    dsn = urllib.parse.urlunsplit(parsed._replace(netloc=netloc))
    destination = Path(destination).resolve()
    if destination.exists():
        raise RuntimeError("Export already exists; choose a new filename.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    snapshot_args = []
    if snapshot is not None:
        if not re.fullmatch(r"[0-9A-Fa-f-]+", snapshot):
            raise RuntimeError("Invalid exported snapshot identifier.")
        snapshot_args = ["--snapshot", snapshot]
    with open(partial, "xb") as stream:
        run(["docker", "run", "--rm", "--env", "PGPASSWORD", "--env", "PGCONNECT_TIMEOUT", "--entrypoint", "pg_dump", PG_IMAGE,
             "--dbname", dsn, "--no-password", "--format=custom", "--schema=public", "--no-owner", "--no-privileges", *snapshot_args],
            env=env, stdout=stream)
    with open(partial, "rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise RuntimeError("Export archive is invalid; partial retained.")
    partial.rename(destination)
    write_manifest(destination)
    print(f"Cloud public schema exported read-only to {destination}.")


def main():
    global ERROR_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE / "runtime",
                        help="Deployment directory; use a team-owned path for handover.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "config-check", "up", "status", "stop", "smoke", "verify"):
        sub.add_parser(name)
    for name in ("backup", "restore"):
        sub.add_parser(name).add_argument("archive", type=Path)
    export = sub.add_parser("export-cloud")
    export.add_argument("--source-env", type=Path, required=True)
    export.add_argument("archive", type=Path)
    args = parser.parse_args()
    deployment = Deployment(args.root)
    ERROR_ROOT = deployment.root / "logs"
    if args.command == "export-cloud":
        export_cloud(args.source_env, args.archive)
    elif args.command == "config-check":
        deployment.validate_config()
    elif args.command in ("backup", "restore"):
        getattr(deployment, args.command)(args.archive)
    elif args.command == "status":
        print(deployment.compose("ps").decode())
    elif args.command == "stop":
        deployment.compose("stop")
        print("Services stopped; persistent volumes retained.")
    else:
        getattr(deployment, args.command)()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
