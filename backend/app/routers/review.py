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
from ..schemas import ConfirmPendingRequest, ReviewRequest
from ..security import APPROVE_ROLES, REVIEW_ROLES, any_role, require_role
from ..services import AmbiguousItem, current_values, resolve_rec

router = APIRouter()


_REVIEW_INSERT = (
    "INSERT INTO review_history (batch_id, item_id, stockroom_id, reviewer, role, "
    "decision, current_max, current_rop, current_min, engine_max, engine_rop, "
    "engine_min, final_max, final_rop, final_min, comment, justification, "
    "requires_senior_approval, rule_version, model_version) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def _record_review(conn, actor, batch_id, rec, decision, final,
                   comment, justification, link_pending: int | None = None):
    """The single path into review_history.

    Both the console and a confirmed chat proposal come through here, so a
    chat-originated decision is indistinguishable downstream: same approval
    gate, same audit trail, same export path. Nothing else may INSERT into
    review_history.

    `link_pending` is the pending_change this review confirms. On Postgres the
    two writes are ONE statement, via a data-modifying CTE: the Supabase REST
    transport gives every call its own transaction, so as two statements a
    failure in between would record the decision while leaving the proposal
    open for someone to confirm a second time. SQLite has no data-modifying
    CTEs -- but it is the test backend and does have real transactions, so it
    keeps the two-statement form.
    """
    cur = current_values(conn, batch_id, rec["item_id"], rec["stockroom_id"])
    eng = (rec["new_max"], rec["new_rop"], rec["new_min"])
    requires_senior = int(decision == "override" or rec["risk_level"] == "High")
    params = (batch_id, rec["item_id"], rec["stockroom_id"], actor["user"],
              actor["role"], decision, *cur, *eng, *final, comment, justification,
              requires_senior, rec["rule_version"], rec["model_version"])

    if link_pending is not None and conn.is_postgres:
        row = conn.execute(
            f"WITH r AS ({_REVIEW_INSERT} RETURNING review_id) "
            "UPDATE pending_change SET status='confirmed', "
            "confirmed_review_id=(SELECT review_id FROM r) "
            "WHERE pending_id=? RETURNING confirmed_review_id AS review_id",
            (*params, link_pending)).fetchone()
        return int(row["review_id"]), cur, eng, requires_senior

    review_id = conn.insert_returning(_REVIEW_INSERT, params, "review_history")
    if link_pending is not None:
        conn.execute("UPDATE pending_change SET status='confirmed', "
                     "confirmed_review_id=? WHERE pending_id=?",
                     (review_id, link_pending))
    return review_id, cur, eng, requires_senior


@router.post("/review/{item_id}")
def submit_review(item_id: str, batch_id: int, body: ReviewRequest,
                  stockroom_id: str | None = None,
                  actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        try:
            rec = resolve_rec(conn, batch_id, item_id, stockroom_id)
        except AmbiguousItem as e:
            raise HTTPException(409, str(e))
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

        review_id, cur, eng, requires_senior = _record_review(
            conn, actor, batch_id, rec, body.decision, final,
            body.comment, body.justification)
        audit(conn, actor, "POST", f"/review/{item_id}", "review", review_id,
              {"decision": body.decision, "final": final,
               "requires_senior_approval": bool(requires_senior)})
        conn.commit()
        return {"review_id": review_id, "item_id": rec["item_id"],
                "stockroom_id": rec["stockroom_id"], "decision": body.decision,
                "final_max": final[0], "final_rop": final[1], "final_min": final[2],
                "requires_senior_approval": bool(requires_senior),
                "status": "awaiting_senior" if requires_senior else "reviewed"}
    finally:
        conn.close()


@router.post("/review/{item_id}/confirm-pending")
def confirm_pending(item_id: str, body: ConfirmPendingRequest,
                    actor: dict = Depends(require_role(*REVIEW_ROLES))):
    """Turn an agent-staged proposal into a real review decision.

    This is the only bridge out of `pending_change`, and it is a human action.
    Until it runs, a chat utterance has produced exactly one thing: a staged
    row that no export path can see.
    """
    conn = get_conn()
    try:
        p = conn.execute("SELECT * FROM pending_change WHERE pending_id=?",
                         (body.pending_id,)).fetchone()
        if p is None:
            raise HTTPException(404, f"no pending change {body.pending_id}")
        if p["status"] != "pending":
            raise HTTPException(409, f"pending change {body.pending_id} is "
                                     f"already {p['status']}")
        if p["item_id"] != item_id:
            raise HTTPException(400, f"pending change {body.pending_id} is for "
                                     f"item {p['item_id']}, not {item_id}")

        # The staged row carries the stockroom, so the confirmation lands on the
        # same row the proposal was made against. `or None` covers rows staged
        # before propose_change recorded it: fall back to resolving by item.
        try:
            rec = resolve_rec(conn, p["batch_id"], item_id,
                              p["stockroom_id"] or None)
        except AmbiguousItem as e:
            raise HTTPException(409, str(e))
        if rec is None:
            raise HTTPException(404, f"item {item_id} not scored in batch "
                                     f"{p['batch_id']}")

        cur = current_values(conn, p["batch_id"], rec["item_id"], rec["stockroom_id"])
        # The confirming engineer's numbers win over the agent's parse; the
        # staged values are only the default.
        staged = (p["proposed_max"], p["proposed_rop"], p["proposed_min"])
        entered = (body.final_max, body.final_rop, body.final_min)
        final = tuple(e if e is not None else (s if s is not None else c)
                      for e, s, c in zip(entered, staged, cur))

        # The console enforces this via ReviewRequest's validator. Here the
        # values are merged from three sources, so the invariant has to be
        # checked on the result -- a proposal that only set `max` can otherwise
        # land below the item's existing ROP.
        if not (final[0] >= final[1] >= final[2]):
            raise HTTPException(
                400, f"must satisfy max >= rop >= min; merging your values with "
                     f"the staged proposal and current levels gave "
                     f"max={final[0]}, rop={final[1]}, min={final[2]}. "
                     f"State all three explicitly.")

        review_id, cur, eng, requires_senior = _record_review(
            conn, actor, p["batch_id"], rec, body.decision, final,
            body.comment or p["rationale"] or "",
            body.justification or "confirmed from chat proposal",
            link_pending=body.pending_id)

        audit(conn, actor, "POST", f"/review/{item_id}/confirm-pending",
              "review", review_id,
              {"pending_id": body.pending_id, "decision": body.decision,
               "final": final, "staged": staged,
               "requires_senior_approval": bool(requires_senior)})
        conn.commit()
        return {"review_id": review_id, "item_id": item_id,
                "pending_id": body.pending_id, "decision": body.decision,
                "final_max": final[0], "final_rop": final[1],
                "final_min": final[2],
                "requires_senior_approval": bool(requires_senior),
                "status": "awaiting_senior" if requires_senior else "reviewed"}
    finally:
        conn.close()


@router.post("/review/{item_id}/discard-pending")
def discard_pending(item_id: str, pending_id: int,
                    actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        p = conn.execute("SELECT * FROM pending_change WHERE pending_id=?",
                         (pending_id,)).fetchone()
        if p is None or p["status"] != "pending":
            raise HTTPException(404, f"no open pending change {pending_id}")
        # Same ownership check confirm_pending makes. Without it, discarding
        # under the wrong item_id succeeds and the audit entry names an item
        # that had nothing to do with the proposal.
        if p["item_id"] != item_id:
            raise HTTPException(400, f"pending change {pending_id} is for "
                                     f"item {p['item_id']}, not {item_id}")
        conn.execute("UPDATE pending_change SET status='discarded' "
                     "WHERE pending_id=?", (pending_id,))
        audit(conn, actor, "POST", f"/review/{item_id}/discard-pending",
              "pending_change", pending_id, {"discarded": True})
        conn.commit()
        return {"pending_id": pending_id, "status": "discarded"}
    finally:
        conn.close()


@router.post("/review/{item_id}/approve")
def approve_review(item_id: str, batch_id: int, stockroom_id: str | None = None,
                   actor: dict = Depends(require_role(*APPROVE_ROLES))):
    conn = get_conn()
    try:
        # Without stockroom_id an item stocked in two stockrooms would approve
        # whichever review happens to be newer, not the one the senior meant.
        sql = "SELECT * FROM review_history WHERE batch_id=? AND item_id=?"
        params = [batch_id, item_id]
        if stockroom_id is not None:
            sql += " AND stockroom_id=?"
            params.append(stockroom_id)
        r = conn.execute(sql + " ORDER BY review_id DESC LIMIT 1",
                         params).fetchone()
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
