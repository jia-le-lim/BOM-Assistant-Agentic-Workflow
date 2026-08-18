"""BOM Review Assistant API -- FastAPI scaffold (PRD section 7).

The engine calculates. NYRA explains. The engineer approves.
WINGS updates only after approval.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import init_db
from .llm import get_provider
from .routers import (chat, export, memory, recommend, review, rules_config,
                      triage, upload)

API_VERSION = "0.2.0-agent"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Schema work happens on startup, not on import.

    `app = create_app()` at the bottom of this module runs on any `import
    app.main` -- a doc generator, an editor autoimport, a stray script. With
    init_db() called directly, that import opened a connection and issued DDL;
    on a developer machine with backend/.env present it issued it against the
    real Supabase project.
    """
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=_lifespan,
        title="BOM Review Assistant API",
        version=API_VERSION,
        description="Decision-support backend for WINGS stocking-parameter review. "
                    "Rule engine v0.2.0-tcb; human approval mandatory before export.",
    )
    app.include_router(upload.router, tags=["ingestion"])
    app.include_router(recommend.router, tags=["recommendations"])
    app.include_router(review.router, tags=["review"])
    app.include_router(rules_config.router, tags=["config"])
    app.include_router(export.router, tags=["export"])
    app.include_router(chat.router, tags=["chat"])
    app.include_router(memory.router, tags=["memory"])
    app.include_router(triage.router, tags=["triage"])

    @app.get("/health", tags=["ops"])
    def health():
        """Which backend and which LLM this process is actually wired to.

        Worth surfacing: the same image runs against SQLite with a stub
        provider in CI and against Supabase with the internal endpoint in
        production, and confusing the two is an easy mistake to make.
        """
        from .config import is_postgres, use_rest
        from .memory import enabled as mem0_enabled

        provider = get_provider()
        return {"status": "ok", "api_version": API_VERSION,
                "database": "supabase-rest" if use_rest()
                            else "supabase-postgres" if is_postgres()
                            else "sqlite (test-only)",
                "llm_provider": provider.name, "llm_model": provider.model,
                "mem0_enabled": mem0_enabled()}

    return app


app = create_app()
