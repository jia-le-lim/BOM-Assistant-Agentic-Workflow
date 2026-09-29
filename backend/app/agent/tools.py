"""The agent's tool surface.

Every read tool is a thin wrapper over a query or service function that already
exists elsewhere in the app. No decision logic is reimplemented here: the
engine calculates, this layer only retrieves what the engine already stored.

There is exactly one write tool, `propose_change`, and it writes only to
`pending_change`. It cannot reach `review_history`, so it cannot produce a row
that `build_export()` would ever put in a WINGS file. That boundary is the
whole design; `tests/test_agent_boundary.py` asserts it.

Two tools are gated rather than free. `run_assist` starts the assist chain,
which spends one model call per live row, so it refuses until an engineer
confirms the row count. `stage_review_action` builds an action CARD and
executes nothing -- confirming it is a human click against the review endpoint,
which is what keeps the boundary above true while chat can still reach the
review queue.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import HTTPException

from ..db import Conn, active_config
from ..account_settings import ensure_settings
from ..llm.provider import ToolSpec
from ..redact import prompt_redaction_on, redact_for_prompt
from ..security import REVIEW_ROLES, can_read_all_workspaces, require_workspace
from ..services import (AmbiguousItem, current_values, derive_status,
                        latest_reviews, resolve_rec)

INT_RE = re.compile(r"\d+")


@dataclass
class ToolContext:
    conn: Conn
    actor: dict
    batch_id: int | None
    question: str
    sources: list[dict] = field(default_factory=list)
    page_context: dict | None = None
    page_actions: list[dict] = field(default_factory=list)


class ToolError(Exception):
    """Rejected at the tool boundary; surfaced to the model, not raised to HTTP."""

    def __init__(self, message: str, status: str = "clarification"):
        super().__init__(message)
        self.status = status


EMPTY: dict[str, Any] = {"_empty": True}


def _require_workspace(ctx: ToolContext, bid: int | None, *, allow_shared: bool = True):
    if bid is None:
        return None
    try:
        return require_workspace(ctx.conn, bid, ctx.actor, allow_shared=allow_shared)
    except HTTPException as exc:
        raise ToolError("Workspace not found or unavailable to this account.",
                        "permission_denied") from exc


def _require_review_role(ctx: "ToolContext", what: str) -> None:
    """Match the REST twin's gate.

    Several of these tools are the chat-side copy of an endpoint that is
    `require_role(*REVIEW_ROLES)` -- peer evidence and dormant coverage name
    other engineers' decisions and batch book value. A tool that reads the same
    rows without the same check makes /chat a way around the endpoint.
    """
    if ctx.actor.get("role") not in REVIEW_ROLES:
        raise ToolError(f"{what} needs a review role; this account has "
                        f"read-only access.", "permission_denied")


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------

def _resolve(ctx: ToolContext, bid: int | None, item_id: str,
             stockroom_id: str | None):
    """resolve_rec, with ambiguity turned into a question for the model."""
    _require_workspace(ctx, bid)
    try:
        return resolve_rec(ctx.conn, bid, item_id, stockroom_id)
    except AmbiguousItem as e:
        # stockroom_id is on the PRD 5.1 sensitive list, so the ids themselves
        # stay out of the prompt when redaction is on. The engineer knows their
        # own stockrooms; the answer comes back in their next message.
        if prompt_redaction_on():
            raise ToolError(f"Item {item_id} is stocked in more than one "
                            f"stockroom. Ask the engineer which one, and pass "
                            f"it back as stockroom_id.")
        raise ToolError(f"{e}. Ask the engineer which stockroom they mean.")


def get_recommendation(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None,
                       stockroom_id: str | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    r = _resolve(ctx, bid, item_id, stockroom_id)
    if r is None:
        return EMPTY
    ctx.sources.append({"type": "recommendation_result", "batch_id": bid,
                        "item_id": item_id,
                        "stockroom_id": r["stockroom_id"],
                        "reason_code": r["reason_code"],
                        "rule_version": r["rule_version"]})
    return {"item_id": r["item_id"], "stockroom_id": r["stockroom_id"],
            "new_max": r["new_max"],
            "new_rop": r["new_rop"], "new_min": r["new_min"],
            "review_required": r["review_required"], "action": r["action"],
            "reason_code": r["reason_code"], "risk_level": r["risk_level"],
            "confidence": r["confidence"], "explanation": r["explanation"],
            "exposure_usd": r["exposure_usd"], "rule_version": r["rule_version"]}


def get_triage_context(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None,
                       stockroom_id: str | None = None) -> dict:
    """Recommendation evidence without stock-level outputs.

    Triage explains an existing decision signal; it never proposes quantities.
    Keeping the quantity columns out of this tool makes that boundary structural.
    """
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    r = _resolve(ctx, bid, item_id, stockroom_id)
    if r is None:
        return EMPTY
    ctx.sources.append({"type": "recommendation_result", "batch_id": bid,
                        "item_id": item_id,
                        "stockroom_id": r["stockroom_id"],
                        "reason_code": r["reason_code"],
                        "rule_version": r["rule_version"]})
    return {"item_id": r["item_id"], "route": r["route"],
            "consumable": r["consumable"], "agreement": r["agreement"],
            "review_required": r["review_required"], "action": r["action"],
            "reason_code": r["reason_code"], "risk_level": r["risk_level"],
            "confidence": r["confidence"], "explanation": r["explanation"],
            "exposure_usd": r["exposure_usd"],
            "rule_version": r["rule_version"]}


def get_procurement_context(ctx: ToolContext, item_id: str,
                            batch_id: int | None = None,
                            stockroom_id: str | None = None) -> dict:
    """Read procurement signals without exposing or proposing stock levels."""
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    rec = _resolve(ctx, bid, item_id, stockroom_id)
    if rec is None:
        return EMPTY
    row = ctx.conn.execute(
        "SELECT payload FROM bom_rows WHERE batch_id=? AND item_id=? "
        "AND stockroom_id=?", (bid, item_id, rec["stockroom_id"])).fetchone()
    if row is None:
        return EMPTY
    payload = json.loads(row["payload"])
    machine = str(payload.get("machine_type") or "")
    configured = next((level for pattern, level in
                       active_config(ctx.conn, ctx.actor["user"]).get("machine_criticality", {}).items()
                       if pattern.lower() in machine.lower()), None)
    ctx.sources.append({"type": "bom_rows", "batch_id": bid,
                        "item_id": item_id,
                        "stockroom_id": rec["stockroom_id"]})
    return {
        "item_id": item_id,
        "criticality": configured or payload.get("sfm_criticality") or "unknown",
        "criticality_source": "confirmed config" if configured else "source row",
        "ownership": payload.get("ownership") or "unknown",
        "contractual_lead_time_days": payload.get("contractual_lead_time") or None,
        "order_qty_multiple": payload.get("order_qty_multiple") or None,
    }


def get_current_values(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None,
                       stockroom_id: str | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    # current_values matches stockroom_id exactly, so the hardcoded "" this used
    # to pass missed every real row -- the tool always answered "I don't know".
    # Take the stockroom off the scored row instead.
    rec = _resolve(ctx, bid, item_id, stockroom_id)
    if rec is None:
        return EMPTY
    try:
        mx, rop, mn = current_values(ctx.conn, bid, item_id, rec["stockroom_id"])
    except KeyError:
        return EMPTY
    ctx.sources.append({"type": "bom_rows", "batch_id": bid, "item_id": item_id,
                        "stockroom_id": rec["stockroom_id"]})
    return {"item_id": item_id, "stockroom_id": rec["stockroom_id"],
            "current_max": mx, "current_rop": rop, "current_min": mn}


def get_item_history(ctx: ToolContext, item_id: str) -> dict:
    """Across readable batches -- the monthly roster rotates, so an item's history
    is not confined to the batch currently loaded."""
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT reviewer, decision, final_max, final_rop, final_min, "
        "reviewed_at, batch_id FROM review_history WHERE item_id=? "
        "AND batch_id IN (SELECT batch_id FROM batches WHERE uploaded_by=? OR ?=1) "
        "ORDER BY review_id DESC LIMIT 20",
        (item_id, ctx.actor["user"], int(can_read_all_workspaces(ctx.actor))))]
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "review_history", "item_id": item_id,
                        "count": len(rows)})
    return {"item_id": item_id, "reviews": rows}


def get_agreement_history(ctx: ToolContext, item_id: str,
                          stockroom_id: str | None = None) -> dict:
    """This item's engine-vs-engineer verdict, one row per past cycle.

    Reads recommendation_result joined to the decision that was actually taken
    on it, across owned batches -- the monthly roster rotates, so an item's
    history is not confined to the batch currently loaded (same reasoning as
    get_item_history).

    stockroom_id is part of the join, not an afterthought: one item was reviewed
    twice in 2024-07 under two stockrooms with opposite verdicts, and keying on
    item_id alone silently merges them into one incoherent streak.
    """
    where = ["r.item_id=?", "r.batch_id IN (SELECT batch_id FROM batches WHERE uploaded_by=? OR ?=1)"]
    params = [item_id, ctx.actor["user"], int(can_read_all_workspaces(ctx.actor))]
    if stockroom_id is not None:
        where.append("r.stockroom_id=?")
        params.append(stockroom_id)
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT r.batch_id, r.stockroom_id, r.agreement, r.agreement_source, "
        "r.new_max AS engine_max, h.final_max, h.final_rop, h.justification, "
        "h.reviewed_at "
        "FROM recommendation_result r JOIN review_history h "
        "ON h.batch_id=r.batch_id AND h.item_id=r.item_id "
        "AND h.stockroom_id=r.stockroom_id "
        f"WHERE {' AND '.join(where)} ORDER BY r.batch_id DESC LIMIT 20",
        params)]
    if not rows:
        return EMPTY
    # Newest first, so the streak counts forward from the most recent cycle.
    # Ordering by item_id (what the s20 SQL does) gives a meaningless number.
    streak = 0
    for row in rows:
        if str(row.get("agreement") or "") != "diverge":
            break
        streak += 1
    last_final = next((r["final_max"] for r in rows if r["final_max"] is not None),
                      None)
    # The ROP from the SAME cycle as last_final_max, not the newest non-null of
    # each independently -- a Max from one review and a ROP from another is a
    # policy nobody approved.
    last_pair = next((r for r in rows if r["final_max"] is not None), None)
    ctx.sources.append({"type": "recommendation_result", "item_id": item_id,
                        "count": len(rows)})
    return {"item_id": item_id, "cycles": rows, "n_cycles": len(rows),
            "n_diverge": sum(1 for r in rows
                             if str(r.get("agreement") or "") == "diverge"),
            "diverge_streak": streak,
            "last_final_max": last_final,
            "last_final_rop": last_pair["final_rop"] if last_pair else None}


def get_item_notes(ctx: ToolContext, item_id: str) -> dict:
    """Item-keyed engineer context that survives batch rotation."""
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT note, author, created_at, origin_batch_id FROM item_note "
        "WHERE item_id=? AND active=1 AND (author=? OR (?=1 AND origin_batch_id IS NOT NULL)) "
        "AND (origin_batch_id IS NULL OR origin_batch_id IN "
        "(SELECT batch_id FROM batches WHERE uploaded_by=? OR ?=1)) "
        "ORDER BY note_id DESC LIMIT 20",
        (item_id, ctx.actor["user"], int(can_read_all_workspaces(ctx.actor)),
         ctx.actor["user"], int(can_read_all_workspaces(ctx.actor))))]
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "item_note", "item_id": item_id,
                        "count": len(rows)})
    return {"item_id": item_id, "notes": rows}


def get_similar_parts(ctx: ToolContext, item_id: str,
                      batch_id: int | None = None,
                      stockroom_id: str | None = None,
                      limit: int = 5) -> dict:
    """Historical PEER parts -- different items with comparable attributes.

    Review-gated for the same reason GET /similarity/{batch}/{item} is
    (routers/similarity.py): the rows name other engineers' decisions,
    justifications and reason codes. Without this the advisory branch -- which
    is not a WRITE_INTENT, so a read-only role is never downgraded out of it --
    served that free text to a viewer or auditor through /chat.

    Not this item's own history; that is get_item_history (spec section 7).
    neighbour_stockroom_id is deliberately withheld: stockroom_id is on the PRD
    5.1 sensitive list, and redact.py masks by key name, so the prefixed column
    would slip past LLM_REDACT_PROMPTS.
    """
    _require_review_role(ctx, "Peer evidence")
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    rec = _resolve(ctx, bid, item_id, stockroom_id)
    if rec is None:
        return EMPTY
    res = ctx.conn.execute(
        "SELECT * FROM similarity_result WHERE batch_id=? AND item_id=? "
        "AND stockroom_id=?", (bid, item_id, rec["stockroom_id"])).fetchone()
    if res is None:
        return EMPTY
    limit = max(1, min(int(limit), 10))
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT neighbour_rank, neighbour_item_id, distance, similarity_reasons, "
        "neighbour_decision, neighbour_final_max, neighbour_final_rop, "
        "neighbour_final_min, neighbour_reason_code, neighbour_justification "
        "FROM similarity_neighbour WHERE batch_id=? AND item_id=? "
        "AND stockroom_id=? ORDER BY neighbour_rank LIMIT ?",
        (bid, item_id, rec["stockroom_id"], limit))]
    for r in rows:
        r["distance"] = round(float(r["distance"]), 3)
    ctx.sources.append({"type": "similarity_result", "batch_id": bid,
                        "item_id": item_id,
                        "similarity_model_version": res["similarity_model_version"],
                        "neighbour_count": res["neighbour_count"]})
    return {"item_id": item_id, "neighbour_count": res["neighbour_count"],
            "pool_size": res["pool_size"], "is_outlier": res["is_outlier"],
            # Already computed and stored by the similarity run. Recomputing
            # it over the returned neighbours would be a second, quietly
            # different number.
            "historical_override_rate": res["historical_override_rate"],
            "advisory_codes": res["advisory_codes"],
            "analogue_max_median": res["analogue_max_median"],
            "analogue_max_p25": res["analogue_max_p25"],
            "analogue_max_p75": res["analogue_max_p75"],
            "analogue_rop_median": res["analogue_rop_median"],
            "analogue_min_median": res["analogue_min_median"],
            "confidence": res["confidence"], "neighbours": rows,
            "caveat": "advisory peer evidence; never a stock level to apply"}


def search_similar_reviews(ctx: ToolContext, query: str,
                           item_id: str | None = None,
                           limit: int = 5) -> dict:
    """Free-text search over what engineers WROTE on past reviews.

    Uses the GIN to_tsvector indexes db.PG_ONLY_INDEX_DDL already creates on
    review_history -- until now nothing queried them. SQLite (the test backend)
    has no FTS5 table here, so it falls back to LIKE.
    """
    limit = max(1, min(int(limit), 10))
    if ctx.conn.is_postgres:
        where = ("to_tsvector('english', coalesce(h.comment,'') || ' ' || "
                 "coalesce(h.justification,'')) @@ plainto_tsquery('english', ?)")
        params: list[Any] = [query]
    else:
        where = ("LOWER(COALESCE(h.comment,'') || ' ' || "
                 "COALESCE(h.justification,'')) LIKE ?")
        params = [f"%{query.strip().lower()}%"]
    sql = ("SELECT h.item_id, h.batch_id, h.decision, h.final_max, h.final_rop, "
           "h.final_min, h.comment, h.justification, h.reviewed_at "
           "FROM review_history h WHERE " + where +
           " AND h.batch_id IN (SELECT batch_id FROM batches WHERE uploaded_by=? OR ?=1)")
    params.extend([ctx.actor["user"], int(can_read_all_workspaces(ctx.actor))])
    if item_id:
        sql += " AND h.item_id=?"
        params.append(item_id)
    sql += " ORDER BY h.review_id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in ctx.conn.execute(sql, params)]
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "review_history", "query": query,
                        "count": len(rows)})
    return {"query": query, "results": rows}


def top_exposure(ctx: ToolContext, n: int = 5,
                 batch_id: int | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    n = max(1, min(int(n), 25))
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT item_id, exposure_usd, risk_level, reason_code "
        "FROM recommendation_result WHERE batch_id=? AND review_required='Y' "
        "ORDER BY exposure_usd DESC LIMIT ?", (bid, n))]
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "recommendation_result", "batch_id": bid})
    return {"batch_id": bid, "items": rows}


def list_review_queue(ctx: ToolContext, batch_id: int | None = None,
                      risk_level: str | None = None,
                      action: str | None = None,
                      reason_code: str | None = None,
                      limit: int = 20) -> dict:
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    limit = max(1, min(int(limit), 100))
    where, params = ["batch_id=?"], [bid]
    if risk_level in ("Low", "Medium", "High"):
        where.append("risk_level=?"); params.append(risk_level)
    if action in ("Increase", "Maintain", "Decrease"):
        where.append("action=?"); params.append(action)
    if reason_code:
        where.append("reason_code LIKE ?"); params.append(f"%{reason_code}%")

    reviews = latest_reviews(ctx.conn, bid)
    rows = []
    for r in ctx.conn.execute(
            "SELECT * FROM recommendation_result WHERE " + " AND ".join(where)
            + " ORDER BY exposure_usd DESC, item_id", params):
        st = derive_status(r, reviews.get((r["item_id"], r["stockroom_id"])))
        rows.append({"item_id": r["item_id"], "status": st,
                     "action": r["action"], "risk_level": r["risk_level"],
                     "reason_code": r["reason_code"],
                     "exposure_usd": r["exposure_usd"]})
        if len(rows) >= limit:
            break
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "recommendation_result", "batch_id": bid,
                        "count": len(rows)})
    return {"batch_id": bid, "items": rows}


def batch_summary(ctx: ToolContext, batch_id: int | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    b = ctx.conn.execute("SELECT * FROM batches WHERE batch_id=?",
                         (bid,)).fetchone()
    if b is None:
        return EMPTY
    reviews = latest_reviews(ctx.conn, bid)
    recs = ctx.conn.execute(
        "SELECT * FROM recommendation_result WHERE batch_id=?", (bid,)).fetchall()
    statuses: dict[str, int] = {}
    pending = 0.0
    for r in recs:
        st = derive_status(r, reviews.get((r["item_id"], r["stockroom_id"])))
        statuses[st] = statuses.get(st, 0) + 1
        if st in ("pending_review", "awaiting_senior"):
            pending += r["exposure_usd"] or 0
    ctx.sources.append({"type": "batches", "batch_id": bid})
    return {"batch_id": bid, "label": b["label"], "status": b["status"],
            "scored": len(recs), "statuses": statuses,
            "exposure_pending_usd": round(pending, 2)}


def explain_rules(ctx: ToolContext) -> dict:
    cfg = active_config(ctx.conn, ctx.actor["user"])
    ctx.sources.append({"type": "rule_config",
                        "rule_version": cfg.get("rule_version")})
    return {"rule_version": cfg.get("rule_version"),
            "config": {k: v for k, v in cfg.items()
                       if not k.startswith("_") and k != "rule_version"}}


def recall_context(ctx: ToolContext, query: str) -> dict:
    """Advisory recall. Returns [] unless MEM0_ENABLED=1.

    Anything from here is labelled non-authoritative and may never be the basis
    of a propose_change -- see the system prompt and memory.py.
    """
    from ..memory import search_memory

    hits = search_memory(query, user_id=ctx.actor.get("user", ""))
    if not hits:
        return EMPTY
    ctx.sources.append({"type": "mem0", "authoritative": False,
                        "count": len(hits)})
    return {"authoritative": False, "results": hits,
            "caveat": "recalled working context, not a system record"}


def get_assist_verdict(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None,
                       stockroom_id: str | None = None) -> dict:
    """The assist layer's advisory verdict for one row.

    Advisory on the same terms as the endpoint that serves the console
    (routers/assist.py): it names which rows deserve attention first and never
    changes a level. `suggested_max`/`suggested_rop` come from assist.rules,
    never from a model -- see assist/chain._evaluate.
    """
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    rec = _resolve(ctx, bid, item_id, stockroom_id)
    if rec is None:
        return EMPTY
    row = ctx.conn.execute(
        "SELECT batch_id, item_id, stockroom_id, verdict, reasons_json, "
        "narrative, suggested_max, suggested_rop, suggestion_basis, "
        "model_version, assisted_at FROM assist_result "
        "WHERE batch_id=? AND item_id=? AND stockroom_id=?",
        (bid, rec["item_id"], rec["stockroom_id"])).fetchone()
    if row is None:
        return EMPTY
    out = dict(row)
    out["reasons"] = json.loads(out.pop("reasons_json") or "[]")
    ctx.sources.append({"type": "assist_result", "batch_id": bid,
                        "item_id": rec["item_id"]})
    out["note"] = ("advisory verdict; it changes no stock level and records "
                   "no decision")
    return out


def list_assist_queue(ctx: ToolContext, batch_id: int | None = None,
                      verdict: str | None = None, limit: int = 10) -> dict:
    """Assisted rows for a batch, optionally one verdict only.

    Counts are over the whole batch, not the returned page: "how many are
    flagged" and "show me the flagged ones" are the same question asked twice,
    and a count that changed with `limit` would answer the first one wrongly.
    """
    from ..assist import rules as assist_rules

    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    if verdict is not None and verdict not in assist_rules.VERDICTS:
        raise ToolError(f"verdict must be one of "
                        f"{list(assist_rules.VERDICTS)}.")
    limit = max(1, min(int(limit), 25))
    counts = {v: 0 for v in assist_rules.VERDICTS}
    for row in ctx.conn.execute(
            "SELECT verdict, COUNT(*) AS n FROM assist_result "
            "WHERE batch_id=? GROUP BY verdict", (bid,)):
        if row["verdict"] in counts:
            counts[row["verdict"]] = row["n"]
    if not any(counts.values()):
        return EMPTY

    sql = ("SELECT item_id, stockroom_id, verdict, reasons_json, narrative, "
           "suggested_max, suggested_rop, suggestion_basis FROM assist_result "
           "WHERE batch_id=?")
    params: list = [bid]
    if verdict:
        sql += " AND verdict=?"
        params.append(verdict)
    params.append(limit)
    items = []
    for row in ctx.conn.execute(sql + " ORDER BY item_id LIMIT ?", params):
        out = dict(row)
        out["reasons"] = json.loads(out.pop("reasons_json") or "[]")
        items.append(out)
    ctx.sources.append({"type": "assist_result", "batch_id": bid,
                        "count": len(items)})
    return {"batch_id": bid, "items": items, "counts": counts,
            "note": "advisory verdicts; no row here has been decided"}


def get_similarity_outliers(ctx: ToolContext, batch_id: int | None = None,
                            limit: int = 10) -> dict:
    """The batch's peer outliers, ranked.

    The batch-level companion to get_similar_parts, which answers the same
    question for ONE item. Advisory evidence: an outlier is a row whose peers
    disagree with it, not a row whose level is wrong.
    """
    _require_review_role(ctx, "Peer outlier evidence")
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid)
    limit = max(1, min(int(limit), 25))
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT item_id, stockroom_id, neighbour_count, is_outlier, "
        "outlier_score, historical_override_rate, advisory_codes "
        "FROM similarity_result WHERE batch_id=? AND is_outlier=1 "
        "ORDER BY outlier_score DESC, item_id LIMIT ?", (bid, limit))]
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "similarity_result", "batch_id": bid,
                        "count": len(rows)})
    return {"batch_id": bid, "items": rows,
            "note": "advisory peer evidence; never a recommended level"}


def get_dormant_coverage(ctx: ToolContext, batch_id: int | None = None) -> dict:
    """How much of the dormant tail the confirmed rules cover, and what it costs.

    The read half of GET /config/dormant-rules/coverage. Coverage alone is not
    the decision, so the book value against what the engine itself said is
    reported beside it -- a rule set that raises agreement while adding stock is
    not obviously a win, and the engineer is the one who weighs that.
    """
    from .. import dormant_rules
    from ..part_category import categorise
    from ..part_category import load_rules as load_category_rules

    _require_review_role(ctx, "Dormant-rule coverage")
    bid = batch_id or ctx.batch_id
    workspace = _require_workspace(ctx, bid)
    owner = workspace["uploaded_by"] if workspace else ctx.actor["user"]
    rules = dormant_rules.load_rules(ctx.conn, owner)
    category_rules, _broken = load_category_rules(ctx.conn, owner)
    matched = total = 0
    engine_usd = rule_usd = 0.0
    for row in ctx.conn.execute(
            "SELECT r.new_max, b.payload FROM recommendation_result r "
            "JOIN bom_rows b ON b.batch_id=r.batch_id "
            "AND b.item_id=r.item_id AND b.stockroom_id=r.stockroom_id "
            "WHERE r.batch_id=? AND r.route='dormant'", (bid,)):
        payload = json.loads(row["payload"])
        total += 1
        try:
            price = float(payload.get("unitprice") or 0)
        except (TypeError, ValueError):
            price = 0.0
        engine_max = int(row["new_max"] or 0)
        engine_usd += price * engine_max
        rule = dormant_rules.resolve(
            rules, payload.get("item_id"),
            categorise(payload.get("item_desc"), category_rules))
        applied = dormant_rules.apply(rule, payload.get("max_qty"))
        if applied is None:
            rule_usd += price * engine_max
            continue
        matched += 1
        rule_usd += price * applied[2]
    if not total:
        return EMPTY
    ctx.sources.append({"type": "dormant_rule_config", "batch_id": bid,
                        "confirmed_rules": len(rules)})
    return {"batch_id": bid, "dormant_rows": total, "matched": matched,
            "uncovered": total - matched,
            "pct": round(100 * matched / total, 1),
            "engine_book_usd": round(engine_usd, 2),
            "proposed_book_usd": round(rule_usd, 2),
            "delta_usd": round(rule_usd - engine_usd, 2),
            "confirmed_rules": len(rules),
            "note": "a rule takes effect on the next engine run; a scored "
                    "batch keeps the numbers its reviewer saw"}


# ---------------------------------------------------------------------------
# the single write tool
# ---------------------------------------------------------------------------

def propose_change(ctx: ToolContext, item_id: str,
                   proposed_max: int | None = None,
                   proposed_rop: int | None = None,
                   proposed_min: int | None = None,
                   rationale: str = "",
                   batch_id: int | None = None,
                   stockroom_id: str | None = None) -> dict:
    """Stage what the engineer said. Never decide it.

    PRD section 8: the LLM never generates Min/Max/ROP. That is enforced here,
    not merely instructed -- every proposed number must appear literally in the
    engineer's own message. A model that infers "so about 5 then" is rejected.
    """
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid, allow_shared=False)
    stated = set(INT_RE.findall(ctx.question))
    proposed = {"proposed_max": proposed_max, "proposed_rop": proposed_rop,
                "proposed_min": proposed_min}
    given = {k: v for k, v in proposed.items() if v is not None}
    if not given:
        raise ToolError("propose_change needs at least one of proposed_max, "
                        "proposed_rop, proposed_min.")

    for key, val in given.items():
        if str(val) not in stated:
            raise ToolError(
                f"Refusing to stage {key}={val}: that number does not appear "
                f"in the engineer's message. Ask them to state the value "
                f"explicitly. (The assistant does not calculate stock levels.)")

    rec = _resolve(ctx, bid, item_id, stockroom_id)
    if rec is None:
        raise ToolError(f"Item {item_id} is not in scored batch {bid}. The "
                        f"monthly roster rotates, so it may not be in the "
                        f"current extract.")

    pending_id = ctx.conn.insert_returning(
        "INSERT INTO pending_change (batch_id, item_id, stockroom_id, "
        "proposed_max, proposed_rop, proposed_min, rationale, "
        "source_utterance, parsed_by, status, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
        (bid, item_id, rec["stockroom_id"], proposed_max, proposed_rop,
         proposed_min,
         rationale[:2000], ctx.question[:2000],
         ctx.actor.get("_parsed_by", ""), ctx.actor.get("user")),
        "pending_change")

    ctx.sources.append({"type": "pending_change", "pending_id": pending_id,
                        "item_id": item_id, "status": "pending"})
    return {"pending_id": pending_id, "item_id": item_id,
            "stockroom_id": rec["stockroom_id"],
            "proposed_max": proposed_max, "proposed_rop": proposed_rop,
            "proposed_min": proposed_min, "status": "pending",
            "note": "staged only; a human must confirm before this can be "
                    "reviewed, approved or exported"}


# ---------------------------------------------------------------------------
# gated tools
#
# Neither of these is a plain read, and neither is a decision. `run_assist`
# spends real money, so it costs a confirmation. `stage_review_action` touches
# the review queue, so it produces a CARD and stops -- the write itself is a
# human click against routers/review.py, which is what keeps this module's
# opening promise true while chat can still reach the queue.
# ---------------------------------------------------------------------------

def run_assist(ctx: ToolContext, batch_id: int | None = None,
               confirm: bool = False, refresh_peers: bool = False) -> dict:
    """Start the assist chain for a batch. Refuses until confirmed.

    One model call per live row: on a real monthly extract that is thousands.

    What `confirm` actually buys, stated honestly: the default is False, so the
    cheapest path -- the model calling this the way it calls everything else --
    costs nothing and returns a row count instead. It is NOT a structural gate.
    `confirm` is an argument the model fills in, so a model that sets it on the
    first call, or one steered by free text coming back from get_item_notes or
    search_similar_reviews, spends the whole batch with no human in the loop.
    Closing that needs server-side state -- a record that this session was shown
    the count for this batch -- which ToolContext has no session id to key on.
    Until then the real backstops are REVIEW_ROLES above and the audit row.

    Peers run first when the batch has none, for the same reason
    POST /assist/run does it: `peers` is one of the chain's five evidence
    sources, so assisting without it silently drops a source and changes the
    verdict.
    """
    from ..assist.chain import live_rows, run_batch
    from ..audit import audit
    from ..similarity import run_similarity

    if ctx.actor.get("role") not in REVIEW_ROLES:
        raise ToolError("Running assist needs a review role; this account has "
                        "read-only access.")
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid, allow_shared=False)
    if bid is None:
        raise ToolError("No scored batch to assist. Name a batch id.")
    if ctx.conn.execute("SELECT 1 FROM batches WHERE batch_id=?",
                        (bid,)).fetchone() is None:
        raise ToolError(f"Batch {bid} does not exist.")

    rows = live_rows(ctx.conn, bid)
    if not confirm:
        # The row count IS retrieved data, so it is a source. Without one,
        # loop.run_agent's no-source control (PRD section 8) replaces the whole
        # answer with "I don't know" -- and the engineer never learns the count
        # they are being asked to approve, which makes the gate unpassable.
        ctx.sources.append({"type": "assist_gate", "batch_id": bid,
                            "live_rows": len(rows), "ran": False})
        return {"gated": True, "batch_id": bid, "live_rows": len(rows),
                "estimated_model_calls": len(rows),
                "note": "not run; tell the engineer the row count and ask them "
                        "to confirm before calling this again with confirm=true"}

    similarity = None
    has_peers = ctx.conn.execute(
        "SELECT 1 FROM similarity_result WHERE batch_id=? LIMIT 1",
        (bid,)).fetchone() is not None
    if refresh_peers or not has_peers:
        try:
            similarity = run_similarity(ctx.conn, bid, refresh_peers)
        except ValueError as e:
            raise ToolError(str(e)) from e
        audit(ctx.conn, ctx.actor, "POST", "/chat", "similarity", bid,
              similarity)
    summary = run_batch(ctx.conn, bid, ctx.actor, active_config(ctx.conn, ctx.actor["user"]))
    summary["similarity"] = similarity
    audit(ctx.conn, ctx.actor, "POST", "/chat", "assist", bid, summary)
    # Committed here, not left to the end of the turn, exactly as
    # POST /assist/run commits straight after run_batch. Every other tool is
    # cheap to redo; this one has already spent a model call per live row, and
    # anything that raises later in the turn -- a provider timeout on the
    # summarising pass, say -- reaches chat_stream's rollback and throws all of
    # those rows away with the money already gone.
    ctx.conn.commit()
    ctx.sources.append({"type": "assist_result", "batch_id": bid,
                        "rows_assisted": summary["rows_assisted"]})
    return summary


# `open_review` is deliberately absent: a card that only says "go look at this
# row" needs a navigation handler the console does not have, and one that
# renders two live buttons doing nothing is worse than no card. Add it with the
# handler, not before.
ALLOWED_ACTIONS = ("confirm_pending", "discard_pending")


def stage_review_action(ctx: ToolContext, kind: str, item_id: str,
                        pending_id: int | None = None,
                        batch_id: int | None = None,
                        stockroom_id: str | None = None) -> dict:
    """Build the card an engineer presses. Never press it.

    The card is validated against the same three checks confirm_pending makes
    (routers/review.py), so a card can never be offered for an action the
    endpoint would reject -- an engineer who clicks must not get a 400.

    What this does NOT do is call that endpoint. The write stays a human action
    from the console session's own credentials, which is why `review_history`
    is still unreachable from here.
    """
    if kind not in ALLOWED_ACTIONS:
        raise ToolError(f"kind must be one of {list(ALLOWED_ACTIONS)}.")
    bid = batch_id or ctx.batch_id
    _require_workspace(ctx, bid, allow_shared=False)
    action: dict[str, Any] = {"kind": kind, "item_id": item_id,
                              "batch_id": bid, "stockroom_id": stockroom_id,
                              "pending_id": pending_id,
                              "proposed_max": None, "proposed_rop": None,
                              "proposed_min": None}

    if pending_id is None:
        raise ToolError(f"{kind} needs the pending_id of the staged proposal. "
                        f"Ask which one they mean.")
    p = ctx.conn.execute("SELECT * FROM pending_change WHERE pending_id=?",
                         (pending_id,)).fetchone()
    if p is None or p["batch_id"] is None:
        raise ToolError(f"There is no pending change {pending_id}.")
    _require_workspace(ctx, p["batch_id"], allow_shared=False)
    if p["status"] != "pending":
        raise ToolError(f"Pending change {pending_id} is already "
                        f"{p['status']}; it cannot be confirmed again.")
    if p["item_id"] != item_id:
        raise ToolError(f"Pending change {pending_id} is for item "
                        f"{p['item_id']}, not {item_id}.")
    # confirm_pending's fourth check, which the three above do not cover: it
    # re-resolves the row, 409s on an item that is now in two stockrooms and
    # 404s on one the batch no longer scores. Without this a card staged against
    # a re-scored batch renders fine and errors on click, which is the one thing
    # this tool's docstring promises cannot happen.
    if _resolve(ctx, p["batch_id"], item_id,
                p["stockroom_id"] or None) is None:
        raise ToolError(
            f"Item {item_id} is no longer scored in batch {p['batch_id']}, so "
            f"pending change {pending_id} cannot be confirmed as it stands. "
            f"The batch may have been re-scored since it was staged.")

    action.update({"batch_id": p["batch_id"],
                   "stockroom_id": p["stockroom_id"],
                   "proposed_max": p["proposed_max"],
                   "proposed_rop": p["proposed_rop"],
                   "proposed_min": p["proposed_min"]})

    # The whole card goes on ctx.sources, not just a breadcrumb. `sources` is
    # the only channel out of a tool that reaches the HTTP response -- the
    # return value below becomes a `tool` message and dies with the loop. A
    # source carrying just the ids would render a card whose proposed levels
    # are undefined, and the console would post those as zeros.
    ctx.sources.append({"type": "staged_action", **action, "executed": False})
    return {"staged_action": action, "executed": False,
            "note": "nothing has been recorded; the engineer must press "
                    "confirm, and an override still needs senior approval"}


# Chat may write `item` and `category` rules. `default` is absent on purpose:
# it sizes every dormant row that no other rule matches -- the whole tail -- and
# that is a console decision, not a sentence.
CHAT_DORMANT_SCOPES = ("item", "category")


def propose_dormant_rule(ctx: ToolContext, scope: str, policy: str,
                         match_key: str = "", fixed_qty: int | None = None,
                         replace: bool = False) -> dict:
    """Record a dormant stocking rule the engineer stated. Never activate it.

    Same shape as propose_change: the agent writes a row the system does not act
    on, and a human turns it into something real. `load_rules()` reads
    `WHERE confirmed=1`, so a row written here sizes nothing, and confirming it
    needs approval rights and the same account owner.

    The `replace` gate is the part that is not obvious. The underlying upsert
    resets `confirmed=0` on conflict -- deliberate for a console edit, which
    should be re-approved. From chat it would mean a restated sentence silently
    un-confirms a LIVE rule and changes what the next engine run sizes, with
    nobody approving it. So a confirmed rule is refused unless the engineer
    says otherwise.
    """
    from .. import dormant_rules
    from ..audit import audit
    from ..part_category import categories as category_names
    from ..part_category import load_rules as load_category_rules

    _require_review_role(ctx, "Proposing a dormant rule")

    if scope not in CHAT_DORMANT_SCOPES:
        if scope == "default":
            raise ToolError(
                "A rule with no item or category applies to every dormant row "
                "no other rule matches -- the whole tail. That one is set in "
                "Config > Dormant Rules, not here.")
        raise ToolError(f"scope must be one of {list(CHAT_DORMANT_SCOPES)}.")
    if policy not in dormant_rules.POLICIES:
        raise ToolError(f"policy must be one of {list(dormant_rules.POLICIES)}.")
    match_key = str(match_key or "").strip()
    if not match_key:
        raise ToolError(f"scope '{scope}' needs a match_key: the item id, or "
                        f"the part category the rule covers.")

    # A fixed_qty rule sets Min/ROP/Max on every matching dormant row
    # (dormant_rules.apply returns (q, q, q)), so the number is a stock level
    # and the same rule applies as in propose_change: it must be the engineer's
    # own, not the model's.
    if policy == "fixed_qty":
        if fixed_qty is None:
            raise ToolError("policy 'fixed_qty' needs fixed_qty -- the quantity "
                            "the engineer wants these parts to keep.")
        if str(fixed_qty) not in set(INT_RE.findall(ctx.question)):
            raise ToolError(
                f"Refusing to record fixed_qty={fixed_qty}: that number does "
                f"not appear in the engineer's message. Ask them to state the "
                f"quantity explicitly. (The assistant does not calculate stock "
                f"levels.)")
        if not 0 <= int(fixed_qty) <= 10_000:
            raise ToolError("fixed_qty must be between 0 and 10000.")
    elif fixed_qty is not None:
        raise ToolError(f"policy '{policy}' takes no quantity. Use 'fixed_qty' "
                        f"if the engineer named a number.")

    note_suffix = ""
    if scope == "category":
        rules, _broken = load_category_rules(ctx.conn, ctx.actor["user"])
        known = category_names(rules)
        if match_key not in known:
            raise ToolError(
                f"'{match_key}' is not a part category. The categories in use "
                f"are: {', '.join(known) if known else '(none configured)'}. "
                f"Ask the engineer which one they mean.")
    else:
        # A typo'd part number would otherwise become a permanent rule that
        # matches nothing and looks deliberate.
        if ctx.batch_id is None:
            note_suffix = (" The item could not be checked against a scored "
                           "batch, because none is loaded.")
        elif _resolve(ctx, ctx.batch_id, match_key, None) is None:
            raise ToolError(
                f"Item {match_key} is not in scored batch {ctx.batch_id}, so "
                f"the id cannot be checked. Confirm the part number.")

    ensure_settings(ctx.conn, ctx.actor["user"])
    existing = ctx.conn.execute(
        "SELECT rule_id, confirmed, policy, fixed_qty FROM user_dormant_rule_config "
        "WHERE owner_user=? AND scope=? AND match_key=?", (ctx.actor["user"], scope, match_key)).fetchone()
    if existing is not None and existing["confirmed"] and not replace:
        current = str(existing["policy"])
        if existing["fixed_qty"] is not None:
            current += f" of {existing['fixed_qty']}"
        raise ToolError(
            f"A CONFIRMED rule already covers {scope} '{match_key}' "
            f"({current}). Re-proposing it would un-confirm it and change what "
            f"the next engine run sizes. Tell the engineer what is there and "
            f"ask whether to replace it; only then call this again with "
            f"replace=true.")

    ctx.conn.execute(
        "INSERT INTO user_dormant_rule_config (owner_user, scope, match_key, policy, "
        "fixed_qty, set_by, confirmed, updated_at) "
        "VALUES (?,?,?,?,?,?,0,datetime('now')) "
        "ON CONFLICT(owner_user, scope, match_key) DO UPDATE SET "
        "policy=excluded.policy, fixed_qty=excluded.fixed_qty, "
        "set_by=excluded.set_by, "
        "confirmed=0, confirmed_by=NULL, updated_at=datetime('now')",
        (ctx.actor["user"], scope, match_key, policy, fixed_qty, ctx.actor["user"]))
    audit(ctx.conn, ctx.actor, "POST", "/chat", "dormant_rule",
          f"{scope}:{match_key}",
          {"scope": scope, "match_key": match_key, "policy": policy,
           "fixed_qty": fixed_qty,
           "replaced_confirmed": bool(replace and existing)})

    row = ctx.conn.execute(
        "SELECT rule_id FROM user_dormant_rule_config WHERE owner_user=? AND scope=? AND match_key=?",
        (ctx.actor["user"], scope, match_key)).fetchone()
    rule_id = row["rule_id"] if row else None
    ctx.sources.append({"type": "dormant_rule", "rule_id": rule_id,
                        "scope": scope, "match_key": match_key,
                        "confirmed": False})
    return {"rule_id": rule_id, "scope": scope, "match_key": match_key,
            "policy": policy, "fixed_qty": fixed_qty, "confirmed": False,
            "note": "personal proposal only; confirm it with approval rights "
                    "before it takes effect on "
                    "the next engine run -- a batch already scored keeps the "
                    "numbers its reviewer saw." + note_suffix}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_ITEM = {"type": "string", "description": "Numeric item/part id"}
_BATCH = {"type": "integer",
          "description": "Batch id; omit to use the latest scored batch"}
_STOCK = {"type": "string",
          "description": "Stockroom id; only needed when one item is stocked "
                         "in more than one stockroom"}

REGISTRY: dict[str, tuple[ToolSpec, Callable[..., dict]]] = {
    "get_recommendation": (ToolSpec(
        "get_recommendation",
        "What the engine recommends CHANGING an item to, with reason code, "
        "risk level, confidence and USD exposure. Use for 'why', 'what does "
        "the engine say', 'should this change'. NOT for the item's present "
        "stock levels -- that is get_current_values.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH,
                                          "stockroom_id": _STOCK},
         "required": ["item_id"]}), get_recommendation),

    "get_current_values": (ToolSpec(
        "get_current_values",
        "The item's PRESENT Max/ROP/Min as loaded from the source workbook. "
        "Use for 'what is it now', 'current max'. NOT for what the engine "
        "recommends -- that is get_recommendation.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH,
                                          "stockroom_id": _STOCK},
         "required": ["item_id"]}), get_current_values),

    "get_triage_context": (ToolSpec(
        "get_triage_context",
        "Read-only engine routing, demand class, agreement, risk, confidence, "
        "reason and explanation for triage. It deliberately omits stock-level "
        "quantities.",
        {"type": "object", "properties": {"item_id": _ITEM,
                                            "batch_id": _BATCH,
                                            "stockroom_id": _STOCK},
         "required": ["item_id"]}), get_triage_context),

    "get_procurement_context": (ToolSpec(
        "get_procurement_context",
        "Read-only criticality, ownership, contractual lead time, and order "
        "multiple for procurement triage. It omits stock-level quantities.",
        {"type": "object", "properties": {"item_id": _ITEM,
                                            "batch_id": _BATCH,
                                            "stockroom_id": _STOCK},
         "required": ["item_id"]}), get_procurement_context),

    "get_item_history": (ToolSpec(
        "get_item_history",
        "Past review DECISIONS for an item across all batches. Use for 'what "
        "was decided before'. NOT for free-text notes -- that is "
        "get_item_notes.",
        {"type": "object", "properties": {"item_id": _ITEM},
         "required": ["item_id"]}), get_item_history),

    "get_agreement_history": (ToolSpec(
        "get_agreement_history",
        "Whether the engine AGREED with the engineer on this item in each past "
        "cycle, with the divergence streak. Use for 'has the engine been right "
        "on this part', 'does it keep getting overridden'. NOT the decisions "
        "themselves -- that is get_item_history.",
        {"type": "object", "properties": {"item_id": _ITEM,
                                          "stockroom_id": _STOCK},
         "required": ["item_id"]}), get_agreement_history),

    "get_item_notes": (ToolSpec(
        "get_item_notes",
        "Engineer notes previously recorded against this item. Use for 'what "
        "was said about', 'any notes'. These carry across months even when the "
        "item is absent from the current roster.",
        {"type": "object", "properties": {"item_id": _ITEM},
         "required": ["item_id"]}), get_item_notes),

    "get_similar_parts": (ToolSpec(
        "get_similar_parts",
        "Historical PEER parts with comparable machine family, criticality, "
        "lead time and demand route, with what was finally decided on each. "
        "Use for 'show me similar parts', 'is this item unusual', 'what did we "
        "do for comparable parts'. NOT this item's own past decisions -- that "
        "is get_item_history. Advisory evidence only; it never sets a level.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH,
                                          "stockroom_id": _STOCK,
                                          "limit": {"type": "integer",
                                                    "description": "1-10"}},
         "required": ["item_id"]}), get_similar_parts),

    "search_similar_reviews": (ToolSpec(
        "search_similar_reviews",
        "Free-text search of what engineers WROTE on past reviews -- comments "
        "and justifications across all items and batches. Use for 'why do "
        "dormant parts keep Max 1', 'what did we say about long lead times'. "
        "NOT for structured peer attributes -- that is get_similar_parts.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "item_id": _ITEM,
            "limit": {"type": "integer", "description": "1-10"}},
         "required": ["query"]}), search_similar_reviews),

    "top_exposure": (ToolSpec(
        "top_exposure",
        "The N HIGHEST-USD items still requiring review, ranked. Use only when "
        "the question is about value at risk or 'what should I look at first'. "
        "For any other filter or listing use list_review_queue.",
        {"type": "object",
         "properties": {"n": {"type": "integer", "description": "1-25"},
                        "batch_id": _BATCH}}), top_exposure),

    "list_review_queue": (ToolSpec(
        "list_review_queue",
        "LIST or COUNT scored items, optionally filtered by risk_level, action "
        "or reason_code, with workflow status. Use for 'show the queue', "
        "'which items are High risk', 'how many Decrease items'. NOT for "
        "whole-batch totals -- that is batch_summary.",
        {"type": "object", "properties": {
            "batch_id": _BATCH,
            "risk_level": {"type": "string", "enum": ["Low", "Medium", "High"]},
            "action": {"type": "string",
                       "enum": ["Increase", "Maintain", "Decrease"]},
            "reason_code": {"type": "string"},
            "limit": {"type": "integer"}}}), list_review_queue),

    "batch_summary": (ToolSpec(
        "batch_summary",
        "WHOLE-BATCH counts by status and total pending USD exposure. Use for "
        "'how is the batch doing'. NOT for a filtered subset or a per-item "
        "list -- that is list_review_queue.",
        {"type": "object", "properties": {"batch_id": _BATCH}}), batch_summary),

    "explain_rules": (ToolSpec(
        "explain_rules",
        "The active rule thresholds and rule_version the engine is using. Use "
        "for 'what thresholds', 'which rules', 'how is this configured'.",
        {"type": "object", "properties": {}}), explain_rules),

    "recall_context": (ToolSpec(
        "recall_context",
        "Advisory recall of the engineer's past working preferences. NOT a "
        "system record and never a basis for proposing a value.",
        {"type": "object", "properties": {"query": {"type": "string"}},
         "required": ["query"]}), recall_context),

    "get_assist_verdict": (ToolSpec(
        "get_assist_verdict",
        "The ADVISORY assist verdict for one item -- flag_for_review, "
        "bulk_accept_candidate or needs_context -- with its reasons and the "
        "one-sentence narrative. Use for 'what does assist say', 'should I "
        "look at this row'. NOT what the ENGINE recommends -- that is "
        "get_recommendation.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH,
                                          "stockroom_id": _STOCK},
         "required": ["item_id"]}), get_assist_verdict),

    "list_assist_queue": (ToolSpec(
        "list_assist_queue",
        "LIST or COUNT assisted rows for a batch, optionally one verdict only. "
        "Use for 'what did assist flag', 'how many are bulk-accept'. NOT the "
        "engine's own queue or risk filters -- that is list_review_queue.",
        {"type": "object", "properties": {
            "batch_id": _BATCH,
            "verdict": {"type": "string",
                        "enum": ["flag_for_review", "bulk_accept_candidate",
                                 "needs_context"]},
            "limit": {"type": "integer", "description": "1-25"}}}),
        list_assist_queue),

    "get_similarity_outliers": (ToolSpec(
        "get_similarity_outliers",
        "The batch's peer OUTLIERS, ranked -- rows whose comparable parts "
        "disagree with them. Use for 'which items are unusual', 'show me the "
        "outliers'. NOT one item's own peers -- that is get_similar_parts.",
        {"type": "object", "properties": {
            "batch_id": _BATCH,
            "limit": {"type": "integer", "description": "1-25"}}}),
        get_similarity_outliers),

    "get_dormant_coverage": (ToolSpec(
        "get_dormant_coverage",
        "How many dormant rows the confirmed dormant rules match, and the book "
        "value that implies against the engine's own answer. Use for 'dormant "
        "coverage', 'how much of the tail do our rules cover'. NOT the active "
        "rule thresholds -- that is explain_rules.",
        {"type": "object", "properties": {"batch_id": _BATCH}}),
        get_dormant_coverage),

    "run_assist": (ToolSpec(
        "run_assist",
        "Start the assist chain for a batch. It costs one model call per live "
        "row, so calling it WITHOUT confirm returns the row count and runs "
        "nothing -- report that count and ask the engineer before calling "
        "again with confirm=true. Never pass confirm=true on your own "
        "initiative.",
        {"type": "object", "properties": {
            "batch_id": _BATCH,
            "confirm": {"type": "boolean",
                        "description": "Only true after the engineer has "
                                       "agreed to the row count"},
            "refresh_peers": {"type": "boolean"}}}), run_assist),

    "stage_review_action": (ToolSpec(
        "stage_review_action",
        "Stage a review-queue action as a CARD for the engineer to press: "
        "confirm or discard a staged proposal. This records nothing and "
        "decides nothing -- the engineer clicks. Use for 'confirm that "
        "change', 'discard pending 4'. Treat 'cancel' as discard.",
        {"type": "object", "properties": {
            "kind": {"type": "string", "enum": list(ALLOWED_ACTIONS)},
            "item_id": _ITEM,
            "pending_id": {"type": "integer",
                           "description": "The staged proposal to act on"},
            "batch_id": _BATCH,
            "stockroom_id": _STOCK},
         "required": ["kind", "item_id"]}), stage_review_action),

    "propose_dormant_rule": (ToolSpec(
        "propose_dormant_rule",
        "Record a proposed DORMANT STOCKING RULE: how much stock parts with no "
        "consumption keep. Scope 'item' for one part, 'category' for a part "
        "category. Policy 'hold_current' keeps today's level, 'fixed_qty' a "
        "stated quantity, 'zero' the engine's own answer. Use for 'keep all "
        "filter parts at 2', 'hold the current level for 100005'. This does "
        "NOT activate the rule -- the owner explicitly confirms it with approval rights. NOT for "
        "changing one item's Max/ROP/Min on a scored batch, which is "
        "propose_change.",
        {"type": "object", "properties": {
            "scope": {"type": "string", "enum": list(CHAT_DORMANT_SCOPES)},
            "match_key": {"type": "string",
                          "description": "Item id, or part category name"},
            "policy": {"type": "string",
                       "enum": ["hold_current", "fixed_qty", "zero"]},
            "fixed_qty": {"type": "integer",
                          "description": "Required for fixed_qty; must be a "
                                         "number the engineer stated"},
            "replace": {"type": "boolean",
                        "description": "Only true once the engineer has agreed "
                                       "to replace a CONFIRMED rule"}},
         "required": ["scope", "match_key", "policy"]}), propose_dormant_rule),

    "propose_change": (ToolSpec(
        "propose_change",
        "Stage a stock-level change the engineer has explicitly stated. Only "
        "use numbers the engineer wrote themselves. This does NOT apply the "
        "change; a human confirms it afterwards.",
        {"type": "object", "properties": {
            "item_id": _ITEM,
            "proposed_max": {"type": "integer"},
            "proposed_rop": {"type": "integer"},
            "proposed_min": {"type": "integer"},
            "rationale": {"type": "string",
                          "description": "The engineer's stated reason"},
            "batch_id": _BATCH,
            "stockroom_id": _STOCK},
         "required": ["item_id"]}), propose_change),
}

# Everything that is not a plain read. `propose_*` writes a row a human must
# then act on; `run_assist` spends money; `stage_review_action` builds a card
# against the review queue. A read-only role is offered none of them.
#
# Derived rather than assumed: this used to be `- {"propose_change"}`, which
# quietly classified every later write tool as read-only.
from .workspace import REGISTRY as WORKSPACE_TOOLS

REGISTRY.update(WORKSPACE_TOOLS)

WRITE_TOOLS = frozenset({"propose_change", "propose_dormant_rule",
                         "run_assist", "stage_review_action", "fill_settings_form"})

READ_ONLY_TOOLS = frozenset(REGISTRY) - WRITE_TOOLS


# Which tools each graph branch offers. Subsetting is the point of the graph:
# 21 specs on every model call is more than the routing layer can discriminate,
# and a branch that cannot SEE propose_change cannot stage anything, whatever
# the model decides it wants. See agent/graph.py.
INTENT_TOOLS: dict[str, frozenset[str]] = {
    "lookup": frozenset({
        "get_recommendation", "get_current_values", "get_item_history",
        "get_item_notes", "get_agreement_history", "get_triage_context",
        "get_procurement_context", "search_similar_reviews", "top_exposure",
        "list_review_queue", "batch_summary", "explain_rules",
        "recall_context"}),
    "assist": frozenset({
        "get_assist_verdict", "list_assist_queue", "run_assist",
        "batch_summary"}),
    "advisory": frozenset({
        "get_similar_parts", "get_similarity_outliers", "get_dormant_coverage",
        "explain_rules"}),
    "propose": frozenset({
        "propose_change", "propose_dormant_rule", "get_current_values",
        "get_recommendation", "get_dormant_coverage"}),
    "action": frozenset({"stage_review_action", "get_current_values"}),
    "configure": frozenset({"get_page_context", "get_settings", "fill_settings_form",
                             "navigate_to_page", "get_dormant_coverage"}),
    "unknown": frozenset(),
}

for _intent in ("lookup", "assist", "advisory", "propose", "action"):
    INTENT_TOOLS[_intent] |= {"get_page_context", "navigate_to_page"}

# Branches a read-only role may never be routed into. graph.classify downgrades
# to `lookup` rather than refusing, so a viewer still gets an answer -- they
# just cannot reach a tool that writes or stages.
#
# `assist` is deliberately absent: it also serves pure reads, and run_assist
# carries its own role check rather than closing the branch to viewers.
WRITE_INTENTS = frozenset({"propose", "action"})


def specs(allow_writes: bool,
          names: Collection[str] | None = None) -> list[ToolSpec]:
    allowed = set(names) if names is not None else None
    return [spec for name, (spec, _) in REGISTRY.items()
            if (allow_writes or name not in WRITE_TOOLS)
            and (allowed is None or name in allowed)]


def dispatch(ctx: ToolContext, name: str, args: dict) -> str:
    """Run a tool, always returning a JSON string for the tool message.

    The result is redacted here, because this string becomes a `tool` message
    and is sent to the provider verbatim. The loop redacted only the tool
    *arguments*, and only for its own log -- so with an external endpoint
    configured, LLM_REDACT_PROMPTS=1 masked nothing that actually left the
    process.
    """
    entry = REGISTRY.get(name)
    if entry is None:
        return json.dumps({"error": f"unknown tool {name}"})
    _, fn = entry
    try:
        bid = args.get("batch_id")
        _require_workspace(ctx, bid if bid is not None else ctx.batch_id,
                           allow_shared=name in READ_ONLY_TOOLS)
        return json.dumps(redact_for_prompt(fn(ctx, **args)), default=str)
    except ToolError as e:
        return json.dumps({"error": str(e), "response_status": e.status})
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
