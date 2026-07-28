"""BOM Review Assistant API -- FastAPI scaffold (PRD section 7).

The engine calculates. NYRA explains. The engineer approves.
WINGS updates only after approval.
"""

from fastapi import FastAPI

from .db import init_db
from .routers import chat, export, recommend, review, rules_config, upload

API_VERSION = "0.1.0-scaffold"


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(
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

    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok", "api_version": API_VERSION}

    return app


app = create_app()
