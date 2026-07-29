"""Paths and scaffold-level settings."""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENGINE_DIR = REPO_ROOT / "analysis" / "engine"


def _load_dotenv() -> None:
    """Minimal .env reader -- no python-dotenv dependency.

    Existing environment variables always win, so an explicit
    `$env:DATABASE_URL=...` or a CI secret overrides the file rather than being
    silently replaced by it.
    """
    path = BACKEND_DIR / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def db_path() -> Path:
    """SQLite file location. Test-only -- see require_database() below."""
    p = os.environ.get("BOM_DB_PATH")
    if p:
        return Path(p)
    d = BACKEND_DIR / "data"
    d.mkdir(exist_ok=True)
    return d / "bom_review.db"


# The application runs on Supabase Postgres. SQLite remains ONLY as the test
# backend -- the suite must stay offline and secret-free, because every
# connection on this network has to traverse an HTTP proxy
# (Backend_Scaffold_Notes.md F1/F6).
#
# Supabase project bom-review-assistant / qiwdjbsrtxeorzpcqmxy, ap-southeast-1:
#   postgresql://postgres.<ref>:<pw>@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require
# From a developer machine the pooler is only reachable through
# scripts/proxy_tunnel.py, so the host becomes 127.0.0.1.
# Resolved at call time, not import time. A module constant would be captured
# before pytest's monkeypatch runs, so a developer with backend/.env populated
# would have the suite silently run against the real Supabase project.
def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def is_postgres() -> bool:
    return database_url().startswith(("postgres://", "postgresql://"))


# Set by tests/conftest.py only. Without it, an unset DATABASE_URL is a hard
# error rather than a silent fall back to a local file -- the failure mode that
# matters is starting the server, appearing to work, and writing an engineer's
# decisions somewhere nobody else can see.
def allow_sqlite() -> bool:
    return os.environ.get("BOM_ALLOW_SQLITE", "0") == "1"


class DatabaseNotConfigured(RuntimeError):
    pass


def require_database() -> None:
    if is_postgres() or allow_sqlite():
        return
    raise DatabaseNotConfigured(
        "DATABASE_URL is not set, so there is no database to talk to.\n"
        "\n"
        "The application runs on Supabase Postgres; SQLite is test-only.\n"
        "  1. Copy backend/.env.example to backend/.env\n"
        "  2. Put the project's database password in DATABASE_URL\n"
        "     (Supabase Dashboard > Project Settings > Database)\n"
        "  3. On this network, start the tunnel first:\n"
        "       python backend/scripts/proxy_tunnel.py --listen-port 5432\n"
        "     then use host 127.0.0.1 in DATABASE_URL.\n"
        "\n"
        "Tests set BOM_ALLOW_SQLITE=1 themselves; do not set it by hand to "
        "work around this."
    )


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MODULE_FILTER = "TCB"          # MVP scope: TCB only (confirmed 28 Jul 2026)

# Phase-1 safety switch: even when the engine says review_required = N, a row
# whose proposed values differ from current is NOT auto-adopted -- it goes to
# the review queue. Auto-clear therefore means "no change and no risk flag".
REQUIRE_REVIEW_FOR_ALL_CHANGES = True
