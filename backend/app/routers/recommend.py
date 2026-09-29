"""Recommendation endpoints -- run the engine, list/inspect results."""

import json
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query

from ..audit import audit
from ..db import get_conn
from ..engine_adapter import score_batch
from ..security import UPLOAD_ROLES, any_role, can_read_all_workspaces, require_role, require_workspace
from ..services import (AmbiguousItem, build_export, derive_status,
                        latest_reviews, resolve_rec)

router = APIRouter()


def _qty(value):
    """A quantity from the upload, or None. '' and junk are both 'not stated'."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _enrich(conn, batch_id: int, rows: list[dict]) -> None:
    """Attach the queue's display-only fields to ONE page of rows.

    These live outside recommendation_result: the description and the current
    Wings settings are in the upload payload, the part category is written by
    the similarity run. Enriching the page rather than the whole result set
    keeps the JSON parse off the 2.8k-row scan above.
    """
    if not rows:
        return
    items = sorted({r["item_id"] for r in rows})
    marks = ",".join(["?"] * len(items))
    payloads = {
        (x["item_id"], x["stockroom_id"]): json.loads(x["payload"])
        for x in conn.execute(
            "SELECT item_id, stockroom_id, payload FROM bom_rows "
            f"WHERE batch_id=? AND item_id IN ({marks})", [batch_id, *items])}
    cats = {
        (x["item_id"], x["stockroom_id"]): x["part_category"]
        for x in conn.execute(
            "SELECT item_id, stockroom_id, part_category FROM similarity_result "
            f"WHERE batch_id=? AND item_id IN ({marks})", [batch_id, *items])}
    for r in rows:
        key = (r["item_id"], r["stockroom_id"])
        p = payloads.get(key, {})
        r["item_desc"] = str(p.get("item_desc") or "")
        r["part_category"] = cats.get(key) or ""
        r["current_max"] = _qty(p.get("max_qty"))
        r["current_rop"] = _qty(p.get("rop_qty"))
        # The engineer's own number for this cycle, when the upload carried one.
        # `agreement_source` already says whether it was this or a prior review
        # that the engine was graded against.
        r["bench_max"] = _qty(p.get("factory_recommended_new_max"))
        r["bench_rop"] = _qty(p.get("factory_recommended_new_rop"))


@router.get("/batches")
def list_batches(actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM batches WHERE (uploaded_by=? OR ?=1) ORDER BY batch_id DESC",
            (actor["user"], int(can_read_all_workspaces(actor))))]
    finally:
        conn.close()


@router.get("/batches/{batch_id}/summary")
def batch_summary(batch_id: int, actor: dict = Depends(any_role())):
    """Counts the review console needs: workflow states, risk, actions, exposure."""
    conn = get_conn()
    try:
        b = require_workspace(conn, batch_id, actor, allow_shared=True)

        reviews = latest_reviews(conn, batch_id)
        recs = conn.execute(
            "SELECT * FROM recommendation_result WHERE batch_id=?", (batch_id,)).fetchall()

        # Counter, not dict.get(k, 0) + 1 seven times over. A Counter IS a dict,
        # so the JSON response shape is unchanged.
        statuses: Counter[str] = Counter()
        risk: Counter[str] = Counter()
        actions: Counter[str] = Counter()
        codes: Counter[str] = Counter()
        consumables: Counter[str] = Counter()
        routes: Counter[str] = Counter()
        agreements: Counter[str] = Counter()
        exposure_total = exposure_pending = 0.0
        bulk_acceptable = 0
        exposures: list[float] = []
        for r in recs:
            st = derive_status(r, reviews.get((r["item_id"], r["stockroom_id"])))
            ag = r["agreement"] or "none"
            statuses[st] += 1
            risk[r["risk_level"]] += 1
            actions[r["action"]] += 1
            consumables[r["consumable"] or "none"] += 1
            routes[r["route"] or "none"] += 1
            agreements[ag] += 1
            codes.update(c for c in (r["reason_code"] or "").split(",") if c)
            exp = r["exposure_usd"] or 0
            exposure_total += exp
            exposures.append(exp)
            if st in ("pending_review", "awaiting_senior"):
                exposure_pending += exp
            # The bulk-clear candidate set: engine confident, not high-risk, and
            # a STRONG benchmark actually agreed. "ag != diverge" used to stand
            # in for that, but it also passes rows with no benchmark at all --
            # inert on a reviewed month, wide open on a brand-new one.
            src = r["agreement_source"] or ""
            if (st == "pending_review" and r["risk_level"] != "High"
                    and (r["confidence"] or 0) >= 0.8 and ag == "match"
                    and src in ("factory", "prior_review")):
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
            "read_only": b["uploaded_by"] != actor["user"],
            "scored": len(recs),
            "statuses": statuses,
            "risk_levels": risk,
            "actions": actions,
            "consumables": consumables,
            "routes": routes,
            "agreements": agreements,
            "reason_codes": dict(codes.most_common()),
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
        b = require_workspace(conn, batch_id, actor)
        if b["status"] in ("draft", "uploading"):
            raise HTTPException(409, "Upload a datasheet to this workspace before running the review engine.")
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
    q: str | None = Query(default=None, max_length=64),
    min_exposure: float | None = None,
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(any_role()),
):
    conn = get_conn()
    try:
        require_workspace(conn, batch_id, actor, allow_shared=True)
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
        if q and q.strip():
            # Free-text part lookup. UPPER() on both sides because Postgres LIKE
            # is case-sensitive and SQLite's is not -- without it the same search
            # behaves differently on the two backends.
            where.append("(UPPER(item_id) LIKE ? OR UPPER(stockroom_id) LIKE ?)")
            needle = f"%{q.strip().upper()}%"
            params += [needle, needle]
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
        page = rows[offset:offset + limit]
        _enrich(conn, batch_id, page)
        return {"total": total, "limit": limit, "offset": offset, "items": page}
    finally:
        conn.close()


@router.get("/recommendations/{item_id}")
def get_recommendation(item_id: str, batch_id: int,
                       stockroom_id: str | None = None,
                       actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        workspace = require_workspace(conn, batch_id, actor, allow_shared=True)
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
            "read_only": workspace["uploaded_by"] != actor["user"],
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
