"""Review workflow -- the human-in-the-loop core (PRD sections 6.6/6.7).

accept   -> final = engine values
override -> final = engineer-provided values (requires senior approval)
reject   -> final = current values (keep as-is, engine proposal declined)

High-risk items and every override require senior approval before export
(PRD section 3: senior engineer approves overrides and high-risk changes).
"""

from fastapi import APIRouter, Depends, HTTPException

from ..audit import audit
from ..db import active_config, get_conn
from ..justifications import JUSTIFICATION_TEMPLATES
from ..schemas import BulkReviewRequest, ConfirmPendingRequest, ReviewRequest
from ..security import APPROVE_ROLES, REVIEW_ROLES, any_role, require_role
from ..services import (AmbiguousItem, current_values, derive_status,
                        latest_reviews, resolve_rec)

router = APIRouter()


_REVIEW_INSERT = (
    "INSERT INTO review_history (batch_id, item_id, stockroom_id, reviewer, role, "
    "decision, current_max, current_rop, current_min, engine_max, engine_rop, "
    "engine_min, final_max, final_rop, final_min, comment, justification, "
    "requires_senior_approval, rule_version, model_version) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


@router.get("/review/justification-templates")
def justification_templates(actor: dict = Depends(any_role())):
    return {"templates": JUSTIFICATION_TEMPLATES}


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


def _bulk_targets(conn, req: BulkReviewRequest) -> tuple[list, list]:
    """Resolve a bulk request to recommendation rows; returns (recs, failures)."""
    failures: list[dict] = []
    if req.items:
        recs = []
        for it in req.items:
            try:
                rec = resolve_rec(conn, req.batch_id, it.item_id, it.stockroom_id)
            except AmbiguousItem as e:
                failures.append({"item_id": it.item_id, "error": str(e)})
                continue
            if rec is None:
                failures.append({"item_id": it.item_id, "error": "not scored"})
            else:
                recs.append(rec)
        return recs, failures

    f = req.filters
    use_assist = bool(f.assist_verdict or f.assist_preselect)
    join = (" JOIN assist_result a ON a.batch_id=r.batch_id "
            "AND a.item_id=r.item_id AND a.stockroom_id=r.stockroom_id"
            if use_assist else "")
    where, params = ["r.batch_id=?"], [req.batch_id]
    if f.risk_level:
        where.append("r.risk_level=?"); params.append(f.risk_level)
    if f.action:
        where.append("r.action=?"); params.append(f.action)
    if f.consumable:
        where.append("r.consumable=?"); params.append(f.consumable)
    if f.route:
        where.append("r.route=?"); params.append(f.route)
    if f.agreement:
        where.append("r.agreement=?"); params.append(f.agreement)
    if f.reason_code:
        where.append("r.reason_code LIKE ?"); params.append(f"%{f.reason_code}%")
    if f.min_exposure is not None:
        where.append("r.exposure_usd>=?"); params.append(f.min_exposure)
    if f.min_confidence is not None:
        where.append("r.confidence>=?"); params.append(f.min_confidence)
    if f.assist_verdict:
        where.append("a.verdict=?"); params.append(f.assist_verdict)
    if f.assist_preselect:
        # Gated on triage_guarded_assist_enabled, which outlived the triage
        # graph it was named for: it is the single switch an admin flips to
        # allow ANY pre-ticked bulk acceptance, and renaming a live config key
        # costs a migration for nothing.
        cfg = active_config(conn)
        if not cfg.get("triage_guarded_assist_enabled", False):
            raise HTTPException(409, "guarded preselection is disabled")
        where.append("a.verdict='bulk_accept_candidate'")
    if f.exclude_high_risk:
        where.append("r.risk_level<>?"); params.append("High")
    sql = ("SELECT r.* FROM recommendation_result r" + join + " WHERE "
           + " AND ".join(where))
    return list(conn.execute(sql, params)), failures


# NOTE: declared before /review/{item_id} so POST /review/bulk is not captured
# as item_id="bulk" (Starlette matches routes in declaration order).
@router.post("/review/bulk")
def bulk_review(body: BulkReviewRequest,
                actor: dict = Depends(require_role(*REVIEW_ROLES))):
    """One-click accept/reject across the review queue.

    The lever that turns 2,700 individual clicks into a handful of decisions:
    the engineer bulk-clears the high-confidence agreements and keeps their
    attention for the rows the engine is unsure about. Only pending_review rows
    are touched; already-decided and auto-cleared rows are skipped, and the
    senior-approval gate still fires per row.
    """
    conn = get_conn()
    try:
        recs, failed = _bulk_targets(conn, body)
        reviews = latest_reviews(conn, body.batch_id)

        reviewed = awaiting = skipped = 0
        for rec in recs:
            key = (rec["item_id"], rec["stockroom_id"])
            if derive_status(rec, reviews.get(key)) != "pending_review":
                skipped += 1
                continue
            cur = current_values(conn, body.batch_id, *key)
            eng = (rec["new_max"], rec["new_rop"], rec["new_min"])
            final = eng if body.decision == "accept" else cur
            _, _, _, requires_senior = _record_review(
                conn, actor, body.batch_id, rec, body.decision, final,
                body.comment, body.justification or "bulk review")
            if requires_senior:
                awaiting += 1
            else:
                reviewed += 1

        audit(conn, actor, "POST", "/review/bulk", "review", body.batch_id,
              {"decision": body.decision, "reviewed": reviewed,
               "awaiting_senior": awaiting, "skipped": skipped,
               "failed": len(failed)})
        conn.commit()
        return {"batch_id": body.batch_id, "decision": body.decision,
                "selected": len(recs), "reviewed": reviewed,
                "awaiting_senior": awaiting, "skipped": skipped,
                "failed": failed}
    finally:
        conn.close()


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
