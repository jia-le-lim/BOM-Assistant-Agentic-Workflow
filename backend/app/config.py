"""Paths and scaffold-level settings."""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENGINE_DIR = REPO_ROOT / "analysis" / "engine"


def db_path() -> Path:
    """Resolved at call time so tests can point at a temp DB via env."""
    p = os.environ.get("BOM_DB_PATH")
    if p:
        return Path(p)
    d = BACKEND_DIR / "data"
    d.mkdir(exist_ok=True)
    return d / "bom_review.db"


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MODULE_FILTER = "TCB"          # MVP scope: TCB only (confirmed 28 Jul 2026)

# Phase-1 safety switch: even when the engine says review_required = N, a row
# whose proposed values differ from current is NOT auto-adopted -- it goes to
# the review queue. Auto-clear therefore means "no change and no risk flag".
REQUIRE_REVIEW_FOR_ALL_CHANGES = True
