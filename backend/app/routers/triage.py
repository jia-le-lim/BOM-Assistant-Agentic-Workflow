"""Explicit, RBAC-protected advisory triage endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..agent.triage import iter_triage, run_triage
from ..audit import audit
from ..db import get_conn
from ..schemas import TriageRunRequest
from ..security import REVIEW_ROLES, require_role

router = APIRouter()


def _public(row) -> dict:
    out = dict(row)
    out["sources"] = json.loads(out.pop("sources_json") or "[]")
    return out


@router.post("/triage/run")
def triage_batch(body: TriageRunRequest,
                 actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        try:
            summary = run_triage(conn, body.batch_id, actor,
                                 body.llm_call_budget, body.refresh,
                                 body.item_id, body.stockroom_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        audit(conn, actor, "POST", "/triage/run", "triage", body.batch_id,
              summary)
        conn.commit()
        return summary
    finally:
        conn.close()


@router.post("/triage/run/stream")
def triage_batch_stream(body: TriageRunRequest,
                        actor: dict = Depends(require_role(*REVIEW_ROLES))):
    """Same run as POST /triage/run, reported phase by phase as NDJSON.

    Triage spends 3 to 7 model calls on a row, which is tens of seconds of
    silence on the plain endpoint. Streaming the graph's own phases lets the
    console show what is actually happening instead of a disabled button.
    """
    conn = get_conn()
    events = iter_triage(conn, body.batch_id, actor, body.llm_call_budget,
                         body.refresh, body.item_id, body.stockroom_id)
    try:
        # Drawn before the response starts, so a bad batch still fails as a
        # 400 rather than as an error event inside a 200 stream.
        first = next(events)
    except ValueError as e:
        conn.close()
        raise HTTPException(400, str(e)) from e
    except BaseException:
        conn.close()
        raise

    def generate():
        try:
            summary = None
            for event in [first, *events]:
                if event["type"] == "complete":
                    summary = event["summary"]
                yield json.dumps(event, default=str,
                                 separators=(",", ":")) + "\n"
            if summary is not None:
                audit(conn, actor, "POST", "/triage/run/stream", "triage",
                      body.batch_id, summary)
                conn.commit()
        except Exception as exc:
            conn.rollback()
            yield json.dumps({"type": "error",
                              "message": (str(exc)
                                          or type(exc).__name__)[:500]}) + "\n"
        finally:
            conn.close()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform",
                 "X-Accel-Buffering": "no"},
    )


@router.get("/triage/{batch_id}")
def list_triage(batch_id: int,
                actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM batches WHERE batch_id=?",
                        (batch_id,)).fetchone() is None:
            raise HTTPException(404, f"batch {batch_id} not found")
        rows = [_public(r) for r in conn.execute(
            "SELECT * FROM triage_result WHERE batch_id=? "
            "ORDER BY priority_score DESC, item_id", (batch_id,))]
        return {"batch_id": batch_id, "total": len(rows), "items": rows}
    finally:
        conn.close()


@router.get("/triage/{batch_id}/{item_id}")
def get_triage(batch_id: int, item_id: str, stockroom_id: str | None = None,
               actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        sql = "SELECT * FROM triage_result WHERE batch_id=? AND item_id=?"
        params: list = [batch_id, item_id]
        if stockroom_id is not None:
            sql += " AND stockroom_id=?"
            params.append(stockroom_id)
        rows = list(conn.execute(sql, params))
        if not rows:
            raise HTTPException(404, f"no triage result for item {item_id}")
        if len(rows) > 1:
            raise HTTPException(409, "item is present in more than one stockroom")
        return _public(rows[0])
    finally:
        conn.close()
