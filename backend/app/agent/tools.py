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
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import Conn, active_config
from ..llm.provider import ToolSpec
from ..services import current_values, derive_status, latest_reviews

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

def get_recommendation(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    r = ctx.conn.execute(
        "SELECT * FROM recommendation_result WHERE batch_id=? AND item_id=?",
        (bid, item_id)).fetchone()
    if r is None:
        return EMPTY
    ctx.sources.append({"type": "recommendation_result", "batch_id": bid,
                        "item_id": item_id, "reason_code": r["reason_code"],
                        "rule_version": r["rule_version"]})
    return {"item_id": r["item_id"], "new_max": r["new_max"],
            "new_rop": r["new_rop"], "new_min": r["new_min"],
            "review_required": r["review_required"], "action": r["action"],
            "reason_code": r["reason_code"], "risk_level": r["risk_level"],
            "confidence": r["confidence"], "explanation": r["explanation"],
            "exposure_usd": r["exposure_usd"], "rule_version": r["rule_version"]}


def get_current_values(ctx: ToolContext, item_id: str,
                       batch_id: int | None = None) -> dict:
    bid = batch_id or ctx.batch_id
    try:
        mx, rop, mn = current_values(ctx.conn, bid, item_id, "")
    except KeyError:
        return EMPTY
    ctx.sources.append({"type": "bom_rows", "batch_id": bid, "item_id": item_id})
    return {"item_id": item_id, "current_max": mx, "current_rop": rop,
            "current_min": mn}


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
                   batch_id: int | None = None) -> dict:
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

    if not ctx.conn.execute(
            "SELECT 1 FROM recommendation_result WHERE batch_id=? AND item_id=?",
            (bid, item_id)).fetchone():
        raise ToolError(f"Item {item_id} is not in scored batch {bid}. The "
                        f"monthly roster rotates, so it may not be in the "
                        f"current extract.")

    pending_id = ctx.conn.insert_returning(
        "INSERT INTO pending_change (batch_id, item_id, stockroom_id, "
        "proposed_max, proposed_rop, proposed_min, rationale, "
        "source_utterance, parsed_by, status, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
        (bid, item_id, "", proposed_max, proposed_rop, proposed_min,
         rationale[:2000], ctx.question[:2000],
         ctx.actor.get("_parsed_by", ""), ctx.actor.get("user")),
        "pending_change")

    ctx.sources.append({"type": "pending_change", "pending_id": pending_id,
                        "item_id": item_id, "status": "pending"})
    return {"pending_id": pending_id, "item_id": item_id,
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

REGISTRY: dict[str, tuple[ToolSpec, Callable[..., dict]]] = {
    "get_recommendation": (ToolSpec(
        "get_recommendation",
        "What the engine recommended for one item, with its reason code and "
        "explanation. Use this to answer 'why' questions.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH},
         "required": ["item_id"]}), get_recommendation),

    "get_current_values": (ToolSpec(
        "get_current_values",
        "The item's current Max/ROP/Min as loaded from the source workbook.",
        {"type": "object", "properties": {"item_id": _ITEM, "batch_id": _BATCH},
         "required": ["item_id"]}), get_current_values),

    "get_item_history": (ToolSpec(
        "get_item_history",
        "Past review decisions for an item across all batches.",
        {"type": "object", "properties": {"item_id": _ITEM},
         "required": ["item_id"]}), get_item_history),

    "get_item_notes": (ToolSpec(
        "get_item_notes",
        "Engineer notes previously recorded against this item. These carry "
        "across months even when the item is absent from the current roster.",
        {"type": "object", "properties": {"item_id": _ITEM},
         "required": ["item_id"]}), get_item_notes),

    "top_exposure": (ToolSpec(
        "top_exposure",
        "Highest-value items still requiring review, ranked by USD exposure.",
        {"type": "object",
         "properties": {"n": {"type": "integer", "description": "1-25"},
                        "batch_id": _BATCH}}), top_exposure),

    "list_review_queue": (ToolSpec(
        "list_review_queue",
        "Filterable list of scored items and their workflow status.",
        {"type": "object", "properties": {
            "batch_id": _BATCH,
            "risk_level": {"type": "string", "enum": ["Low", "Medium", "High"]},
            "action": {"type": "string",
                       "enum": ["Increase", "Maintain", "Decrease"]},
            "reason_code": {"type": "string"},
            "limit": {"type": "integer"}}}), list_review_queue),

    "batch_summary": (ToolSpec(
        "batch_summary",
        "Counts and pending exposure for a batch.",
        {"type": "object", "properties": {"batch_id": _BATCH}}), batch_summary),

    "explain_rules": (ToolSpec(
        "explain_rules",
        "The active rule thresholds and rule_version the engine is using.",
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
            "batch_id": _BATCH},
         "required": ["item_id"]}), propose_change),
}

READ_ONLY_TOOLS = frozenset(REGISTRY) - {"propose_change"}


def specs(allow_writes: bool) -> list[ToolSpec]:
    return [spec for name, (spec, _) in REGISTRY.items()
            if allow_writes or name != "propose_change"]


def dispatch(ctx: ToolContext, name: str, args: dict) -> str:
    """Run a tool, always returning a JSON string for the tool message."""
    entry = REGISTRY.get(name)
    if entry is None:
        return json.dumps({"error": f"unknown tool {name}"})
    _, fn = entry
    try:
        return json.dumps(fn(ctx, **args), default=str)
    except ToolError as e:
        return json.dumps({"error": str(e)})
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
