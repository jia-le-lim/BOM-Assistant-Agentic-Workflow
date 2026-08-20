"""The agent's tool surface.

Every read tool is a thin wrapper over a query or service function that already
exists elsewhere in the app. No decision logic is reimplemented here: the
engine calculates, this layer only retrieves what the engine already stored.

There is exactly one write tool, `propose_change`, and it writes only to
`pending_change`. It cannot reach `review_history`, so it cannot produce a row
that `build_export()` would ever put in a WINGS file. That boundary is the
whole design; `tests/test_agent_boundary.py` asserts it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import Conn, active_config
from ..llm.provider import ToolSpec
from ..redact import prompt_redaction_on, redact_for_prompt
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


class ToolError(Exception):
    """Rejected at the tool boundary; surfaced to the model, not raised to HTTP."""


EMPTY: dict[str, Any] = {"_empty": True}


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------

def _resolve(ctx: ToolContext, bid: int | None, item_id: str,
             stockroom_id: str | None):
    """resolve_rec, with ambiguity turned into a question for the model."""
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
                       active_config(ctx.conn).get("machine_criticality", {}).items()
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
    """Across ALL batches -- the monthly roster rotates, so an item's history
    is not confined to the batch currently loaded."""
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT reviewer, decision, final_max, final_rop, final_min, "
        "reviewed_at, batch_id FROM review_history WHERE item_id=? "
        "ORDER BY review_id DESC LIMIT 20", (item_id,))]
    if not rows:
        return EMPTY
    ctx.sources.append({"type": "review_history", "item_id": item_id,
                        "count": len(rows)})
    return {"item_id": item_id, "reviews": rows}


def get_item_notes(ctx: ToolContext, item_id: str) -> dict:
    """Item-keyed engineer context that survives batch rotation."""
    rows = [dict(r) for r in ctx.conn.execute(
        "SELECT note, author, created_at, origin_batch_id FROM item_note "
        "WHERE item_id=? AND active=1 ORDER BY note_id DESC LIMIT 20",
        (item_id,))]
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

    Not this item's own history; that is get_item_history (spec section 7).
    neighbour_stockroom_id is deliberately withheld: stockroom_id is on the PRD
    5.1 sensitive list, and redact.py masks by key name, so the prefixed column
    would slip past LLM_REDACT_PROMPTS.
    """
    bid = batch_id or ctx.batch_id
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
           "FROM review_history h WHERE " + where)
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
    cfg = active_config(ctx.conn)
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

READ_ONLY_TOOLS = frozenset(REGISTRY) - {"propose_change"}


def specs(allow_writes: bool,
          names: Collection[str] | None = None) -> list[ToolSpec]:
    allowed = set(names) if names is not None else None
    return [spec for name, (spec, _) in REGISTRY.items()
            if (allow_writes or name != "propose_change")
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
        return json.dumps(redact_for_prompt(fn(ctx, **args)), default=str)
    except ToolError as e:
        return json.dumps({"error": str(e)})
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
