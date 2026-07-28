"""Review workflow -- the human-in-the-loop core (PRD sections 6.6/6.7).

accept   -> final = engine values
override -> final = engineer-provided values (requires senior approval)
reject   -> final = current values (keep as-is, engine proposal declined)

High-risk items and every override require senior approval before export
(PRD section 3: senior engineer approves overrides and high-risk changes).
"""

from fastapi import APIRouter, Depends, HTTPException

from ..audit import audit
from ..db import get_conn
from ..schemas import ReviewRequest
from ..security import APPROVE_ROLES, REVIEW_ROLES, any_role, require_role
from ..services import current_values, derive_status, latest_reviews

router = APIRouter()


@router.post("/review/{item_id}")
def submit_review(item_id: str, batch_id: int, body: ReviewRequest,
                  actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        rec = conn.execute(
            "SELECT * FROM recommendation_result WHERE batch_id=? AND item_id=?",
            (batch_id, item_id)).fetchone()
        if rec is None:
            raise HTTPException(404, f"item {item_id} not scored in batch {batch_id}")

        cur = current_values(conn, batch_id, rec["item_id"], rec["stockroom_id"])
        eng = (rec["new_max"], rec["new_rop"], rec["new_min"])
        if body.decision == "accept":
            final = eng
        elif body.decision == "reject":
            final = cur
        else:
            final = (body.final_max, body.final_rop, body.final_min)

        requires_senior = int(body.decision == "override" or rec["risk_level"] == "High")

        cursor = conn.execute(
            "INSERT INTO review_history (batch_id, item_id, stockroom_id, reviewer, role, "
            "decision, current_max, current_rop, current_min, engine_max, engine_rop, "
            "engine_min, final_max, final_rop, final_min, comment, justification, "
            "requires_senior_approval, rule_version, model_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, rec["item_id"], rec["stockroom_id"], actor["user"], actor["role"],
             body.decision, *cur, *eng, *final, body.comment, body.justification,
             requires_senior, rec["rule_version"], rec["model_version"]))
        review_id = cursor.lastrowid
        audit(conn, actor, "POST", f"/review/{item_id}", "review", review_id,
              {"decision": body.decision, "final": final,
               "requires_senior_approval": bool(requires_senior)})
        conn.commit()
        return {"review_id": review_id, "item_id": rec["item_id"],
                "decision": body.decision,
                "final_max": final[0], "final_rop": final[1], "final_min": final[2],
                "requires_senior_approval": bool(requires_senior),
                "status": "awaiting_senior" if requires_senior else "reviewed"}
    finally:
        conn.close()


@router.post("/review/{item_id}/approve")
def approve_review(item_id: str, batch_id: int,
                   actor: dict = Depends(require_role(*APPROVE_ROLES))):
    conn = get_conn()
    try:
        r = conn.execute(
            "SELECT * FROM review_history WHERE batch_id=? AND item_id=? "
            "ORDER BY review_id DESC LIMIT 1", (batch_id, item_id)).fetchone()
        if r is None:
            raise HTTPException(404, f"no review found for item {item_id} in batch {batch_id}")
        if not r["requires_senior_approval"]:
            raise HTTPException(400, "latest review does not require senior approval")
        if r["senior_approved_by"]:
            raise HTTPException(409, f"already approved by {r['senior_approved_by']}")
        if r["reviewer"] == actor["user"]:
            raise HTTPException(403, "reviewer cannot approve their own review")

        conn.execute(
            "UPDATE review_history SET senior_approved_by=?, senior_approved_at=datetime('now') "
            "WHERE review_id=?", (actor["user"], r["review_id"]))
        audit(conn, actor, "POST", f"/review/{item_id}/approve", "review",
              r["review_id"], {"approved": True})
        conn.commit()
        return {"review_id": r["review_id"], "item_id": item_id,
                "senior_approved_by": actor["user"], "status": "reviewed"}
    finally:
        conn.close()


@router.get("/history/{item_id}")
def get_history(item_id: str, actor: dict = Depends(any_role())):
    """Review history for an item across ALL batches -- the memory layer
    (process owner, 28 Jul 2026: history records when and what was reviewed)."""
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM review_history WHERE item_id=? ORDER BY review_id DESC",
            (item_id,))]
        return {"item_id": item_id, "reviews": rows}
    finally:
        conn.close()
