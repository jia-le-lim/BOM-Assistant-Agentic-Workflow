"""Advisory peer-similarity endpoints.

Read rights are REVIEW_ROLES rather than any_role: the evidence names other
engineers' decisions, so it sits with the same audience as triage.
"""

from fastapi import APIRouter, Depends, HTTPException

from ..audit import audit
from ..db import get_conn
from ..schemas import SimilarityRunRequest
from ..security import REVIEW_ROLES, require_role
from ..similarity import attach_neighbour_desc, run_similarity

router = APIRouter()


@router.post("/similarity/run")
def similarity_batch(body: SimilarityRunRequest,
                     actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        try:
            summary = run_similarity(conn, body.batch_id, body.refresh)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        audit(conn, actor, "POST", "/similarity/run", "similarity",
              body.batch_id, summary)
        conn.commit()
        return summary
    finally:
        conn.close()


@router.get("/similarity/{batch_id}")
def list_similarity(batch_id: int,
                    actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM batches WHERE batch_id=?",
                        (batch_id,)).fetchone() is None:
            raise HTTPException(404, f"batch {batch_id} not found")
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM similarity_result WHERE batch_id=? "
            "ORDER BY is_outlier DESC, outlier_score DESC, item_id",
            (batch_id,))]
        return {"batch_id": batch_id, "total": len(rows), "items": rows}
    finally:
        conn.close()


@router.get("/similarity/{batch_id}/{item_id}")
def get_similarity(batch_id: int, item_id: str, stockroom_id: str | None = None,
                   actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        sql = ("SELECT * FROM similarity_result WHERE batch_id=? AND item_id=?")
        params: list = [batch_id, item_id]
        if stockroom_id is not None:
            sql += " AND stockroom_id=?"
            params.append(stockroom_id)
        rows = list(conn.execute(sql, params))
        if not rows:
            raise HTTPException(404, f"no similarity result for item {item_id}")
        if len(rows) > 1:
            raise HTTPException(409, "item is present in more than one stockroom")
        out = dict(rows[0])
        out["neighbours"] = attach_neighbour_desc(conn, [dict(r) for r in conn.execute(
            "SELECT * FROM similarity_neighbour WHERE batch_id=? AND item_id=? "
            "AND stockroom_id=? ORDER BY neighbour_rank",
            (batch_id, item_id, out["stockroom_id"]))])
        return out
    finally:
        conn.close()
