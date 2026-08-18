"""Recommendation endpoints -- run the engine, list/inspect results."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from ..audit import audit
from ..db import get_conn
from ..engine_adapter import score_batch
from ..security import UPLOAD_ROLES, any_role, require_role
from ..services import (AmbiguousItem, build_export, derive_status,
                        latest_reviews, resolve_rec)

router = APIRouter()


@router.get("/batches")
def list_batches(actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM batches ORDER BY batch_id DESC")]
    finally:
        conn.close()


@router.get("/batches/{batch_id}/summary")
def batch_summary(batch_id: int, actor: dict = Depends(any_role())):
    """Counts the review console needs: workflow states, risk, actions, exposure."""
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if b is None:
            raise HTTPException(404, f"batch {batch_id} not found")

        reviews = latest_reviews(conn, batch_id)
        recs = conn.execute(
            "SELECT * FROM recommendation_result WHERE batch_id=?", (batch_id,)).fetchall()

        statuses: dict[str, int] = {}
        risk: dict[str, int] = {}
        actions: dict[str, int] = {}
        codes: dict[str, int] = {}
        consumables: dict[str, int] = {}
        routes: dict[str, int] = {}
        agreements: dict[str, int] = {}
        exposure_total = exposure_pending = 0.0
        bulk_acceptable = 0
        exposures: list[float] = []
        for r in recs:
            st = derive_status(r, reviews.get((r["item_id"], r["stockroom_id"])))
            statuses[st] = statuses.get(st, 0) + 1
            risk[r["risk_level"]] = risk.get(r["risk_level"], 0) + 1
            actions[r["action"]] = actions.get(r["action"], 0) + 1
            cons = r["consumable"] or "none"
            consumables[cons] = consumables.get(cons, 0) + 1
            rt = r["route"] or "none"
            routes[rt] = routes.get(rt, 0) + 1
            ag = r["agreement"] or "none"
            agreements[ag] = agreements.get(ag, 0) + 1
            for c in (r["reason_code"] or "").split(","):
                if c:
                    codes[c] = codes.get(c, 0) + 1
            exp = r["exposure_usd"] or 0
            exposure_total += exp
            exposures.append(exp)
            if st in ("pending_review", "awaiting_senior"):
                exposure_pending += exp
            # The bulk-clear candidate set: engine confident, not high-risk, and
            # not diverging from the engineer's own benchmark -> safe to accept.
            if (st == "pending_review" and r["risk_level"] != "High"
                    and (r["confidence"] or 0) >= 0.8 and ag != "diverge"):
                bulk_acceptable += 1

        # Exposure Pareto: how few rows carry the money. Working the queue in
        # exposure order means a handful of decisions cover most of the value.
        exposures.sort(reverse=True)
        cum = 0.0
        items_for_80 = 0
        for i, e in enumerate(exposures, 1):
            cum += e
            if exposure_total and cum >= 0.8 * exposure_total:
                items_for_80 = i
                break
        top100 = sum(exposures[:100])

        export = build_export(conn, batch_id) if b["status"] == "scored" else {"rows": []}
        return {
            "batch": dict(b),
            "scored": len(recs),
            "statuses": statuses,
            "risk_levels": risk,
            "actions": actions,
            "consumables": consumables,
            "routes": routes,
            "agreements": agreements,
            "reason_codes": dict(sorted(codes.items(), key=lambda kv: -kv[1])),
            "exposure_total_usd": round(exposure_total, 2),
            "exposure_pending_usd": round(exposure_pending, 2),
            "bulk_acceptable": bulk_acceptable,
            "pareto": {
                "items_for_80pct": items_for_80,
                "top100_coverage_pct": round(100 * top100 / exposure_total, 1)
                if exposure_total else 0.0,
            },
            "export_ready_rows": len(export["rows"]),
        }
    finally:
        conn.close()


@router.post("/run-recommendation")
def run_recommendation(batch_id: int,
                       actor: dict = Depends(require_role(*UPLOAD_ROLES))):
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if b is None:
            raise HTTPException(404, f"batch {batch_id} not found")
        try:
            summary = score_batch(conn, batch_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        audit(conn, actor, "POST", "/run-recommendation", "batch", batch_id, summary)
        conn.commit()
        return summary
    finally:
        conn.close()


@router.get("/recommendations")
def list_recommendations(
    batch_id: int,
    review_required: str | None = Query(default=None, pattern="^[YN]$"),
    risk_level: str | None = Query(default=None, pattern="^(Low|Medium|High)$"),
    action: str | None = Query(default=None, pattern="^(Increase|Maintain|Decrease)$"),
    status: str | None = Query(default=None,
                               pattern="^(auto_cleared|pending_review|awaiting_senior|reviewed)$"),
    consumable: str | None = Query(default=None,
                                   pattern="^(constant|sporadic|dying|none)$"),
    route: str | None = Query(default=None,
                              pattern="^(active|dormant|dying|no-data)$"),
    agreement: str | None = Query(default=None, pattern="^(match|diverge|none)$"),
    reason_code: str | None = None,
    min_exposure: float | None = None,
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(any_role()),
):
    conn = get_conn()
    try:
        where, params = ["batch_id=?"], [batch_id]
        if review_required:
            where.append("review_required=?"); params.append(review_required)
        if risk_level:
            where.append("risk_level=?"); params.append(risk_level)
        if action:
            where.append("action=?"); params.append(action)
        if consumable:
            where.append("consumable=?"); params.append(consumable)
        if route:
            where.append("route=?"); params.append(route)
        if agreement:
            where.append("agreement=?"); params.append(agreement)
        if reason_code:
            where.append("reason_code LIKE ?"); params.append(f"%{reason_code}%")
        if min_exposure is not None:
            where.append("exposure_usd>=?"); params.append(min_exposure)
        if min_confidence is not None:
            where.append("confidence>=?"); params.append(min_confidence)
        sql = ("SELECT * FROM recommendation_result WHERE " + " AND ".join(where)
               + " ORDER BY exposure_usd DESC, item_id")

        reviews = latest_reviews(conn, batch_id)
        rows = []
        for r in conn.execute(sql, params):
            st = derive_status(r, reviews.get((r["item_id"], r["stockroom_id"])))
            if status and st != status:
                continue
            d = dict(r)
            d["status"] = st
            rows.append(d)
        total = len(rows)
        # NOTE (perf, flagged in Backend_Scaffold_Notes.md): the status filter is
        # applied in Python because status is derived from review joins; page
        # slicing therefore happens after the full batch scan. Fine at 2.8k rows;
        # must move into SQL (view or status column) before multi-module scale.
        return {"total": total, "limit": limit, "offset": offset,
                "items": rows[offset:offset + limit]}
    finally:
        conn.close()


@router.get("/recommendations/{item_id}")
def get_recommendation(item_id: str, batch_id: int,
                       stockroom_id: str | None = None,
                       actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        try:
            r = resolve_rec(conn, batch_id, item_id, stockroom_id)
        except AmbiguousItem as e:
            raise HTTPException(409, str(e))
        if r is None:
            raise HTTPException(404, f"item {item_id} not scored in batch {batch_id}")
        reviews = latest_reviews(conn, batch_id)
        review = reviews.get((r["item_id"], r["stockroom_id"]))
        payload = conn.execute(
            "SELECT payload FROM bom_rows WHERE batch_id=? AND item_id=? AND stockroom_id=?",
            (batch_id, r["item_id"], r["stockroom_id"])).fetchone()
        p = json.loads(payload["payload"]) if payload else {}
        return {
            "recommendation": dict(r),
            "status": derive_status(r, review),
            "latest_review": dict(review) if review else None,
            "context": {k: p.get(k) for k in (
                "item_desc", "machine_type", "aging_status", "unitprice",
                "contractual_lead_time", "max_qty", "rop_qty", "min_qty",
                "avail_qty", "last_365_day_cnsmptn_qty", "recom_max",
                "atm_recommended_max", "sfm_brr_max", "replenishment_policy",
                "factory_recommended_new_max", "factory_recommended_new_rop",
                "factory_recommended_new_min")},
        }
    finally:
        conn.close()
