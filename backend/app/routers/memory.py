"""mem0 preference endpoints.

The agent tool surface stays read-only except for propose_change (see
tests/test_agent_boundary.py). These routes let engineers explicitly store and
query their own working preferences for advisory recall.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..audit import audit
from ..db import get_conn
from ..memory import enabled as mem0_enabled
from ..memory import probe as mem0_probe
from ..memory import remember, search_memory, status as mem0_status
from ..security import any_role

router = APIRouter(prefix="/memory")


class MemoryAddRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("/status")
def get_memory_status(probe: bool = False, actor: dict = Depends(any_role())):
    """Health for the optional mem0 layer."""
    _ = actor  # RBAC stub: presence of headers still matters.
    payload = mem0_status()
    if probe and payload.get("enabled"):
        payload["ready"] = mem0_probe()
        payload.update(mem0_status())
    return payload


@router.post("")
def add_memory(body: MemoryAddRequest, actor: dict = Depends(any_role())):
    if not mem0_enabled():
        raise HTTPException(
            status_code=503,
            detail="mem0 is disabled (set MEM0_ENABLED=1)",
        )
    user_id = actor.get("user") or ""
    ok = remember(body.text, user_id=user_id)
    if not ok:
        raise HTTPException(
            status_code=503,
            detail="Preference recall is unavailable (check /memory/status and the configured embedding model and storage)",
        )

    conn = get_conn()
    try:
        audit(conn, actor, "POST", "/memory", "mem0", user_id, {"stored": True})
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/search")
def search_memories(
    q: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=25),
    actor: dict = Depends(any_role()),
):
    if not mem0_enabled():
        return {"enabled": False, "count": 0, "results": []}
    user_id = actor.get("user") or ""
    hits = search_memory(q, user_id=user_id, limit=limit)
    return {"enabled": True, "count": len(hits), "results": hits}

