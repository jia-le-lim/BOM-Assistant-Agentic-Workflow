"""Workspace item discovery, using confirmed categories even before peer scoring."""

from __future__ import annotations

import json
import math

from .. import dormant_rules, part_category
from ..services import derive_status, latest_reviews


def _quantity(value):
    try:
        number = float(value)
        return int(number) if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def search_items(ctx, batch_id: int | None = None, query: str | None = None,
                 category: str | None = None, stockroom_id: str | None = None,
                 risk_level: str | None = None, action: str | None = None,
                 reason_code: str | None = None, route: str | None = None,
                 status: str | None = None, uncovered_dormant: bool = False,
                 limit: int = 20, offset: int = 0, *, scored_only: bool = False) -> dict:
    from .tools import ToolError, _require_review_role, _require_workspace

    bid = batch_id if batch_id is not None else ctx.batch_id
    if bid is None:
        raise ToolError("Choose a workspace with @ or state its batch ID.")
    batch = _require_workspace(ctx, bid)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ToolError("Use a page size between 1 and 100.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ToolError("Use a nonnegative page offset.")
    if not isinstance(uncovered_dormant, bool):
        raise ToolError("uncovered_dormant must be true or false.")
    for name, value, allowed in (
        ("risk_level", risk_level, ("Low", "Medium", "High")),
        ("action", action, ("Increase", "Maintain", "Decrease")),
        ("route", route, ("active", "dying", "dormant", "no-data")),
        ("status", status, ("pending_review", "awaiting_senior", "reviewed", "auto_cleared")),
    ):
        if value is not None and value not in allowed:
            raise ToolError(f"Invalid {name}. Choose from: {', '.join(allowed)}.")
    for value in (query, category, stockroom_id, reason_code):
        if value is not None and (not isinstance(value, str) or len(value) > 200):
            raise ToolError("Search text and filters must be strings of at most 200 characters.")
    needs_scores = scored_only or any((risk_level, action, reason_code, route, status, uncovered_dormant))
    if needs_scores and batch["status"] != "scored":
        raise ToolError(f"Workspace #{bid} has not been scored. Run recommendations first; search_items can still search uploaded descriptions and categories.")
    if uncovered_dormant:
        _require_review_role(ctx, "Dormant-rule coverage")
        if route not in (None, "dormant"):
            raise ToolError("Uncovered dormant items require the dormant route.")
        route = "dormant"

    rules, broken = part_category.load_rules(ctx.conn, batch["uploaded_by"])
    known = part_category.categories(rules)
    normalized = {name.casefold().replace("_", " "): name for name in known}
    if category is not None:
        key = category.strip().casefold().replace("_", " ")
        if key == "uncategorised" or key == "uncategorized":
            category = ""
        elif key in normalized:
            category = normalized[key]
        elif key.endswith("s") and key[:-1] in normalized:
            category = normalized[key[:-1]]
        else:
            raise ToolError("Unknown part category. Available categories: " + ", ".join(known) + ", uncategorised.")
    dormant = dormant_rules.load_rules(ctx.conn, batch["uploaded_by"]) if uncovered_dormant else []
    reviews = latest_reviews(ctx.conn, bid)
    where, params = ["b.batch_id=?", "b.quarantined=0"], [bid]
    if stockroom_id is not None:
        where.append("b.stockroom_id=?")
        params.append(stockroom_id)
    if needs_scores:
        where.append("r.item_id IS NOT NULL")
    for column, value in (("risk_level", risk_level), ("action", action), ("route", route)):
        if value is not None:
            where.append(f"r.{column}=?")
            params.append(value)
    # All joins use the complete row identity. Category/text filtering must run
    # before pagination; cached similarity categories may be absent or stale.
    records = ctx.conn.execute(
        "SELECT b.item_id, b.stockroom_id, b.payload, r.item_id AS scored_item, "
        "r.review_required, r.action, r.risk_level, r.reason_code, r.route, "
        "r.new_max, r.new_rop, r.new_min, r.exposure_usd "
        "FROM bom_rows b LEFT JOIN recommendation_result r ON "
        "r.batch_id=b.batch_id AND r.item_id=b.item_id AND r.stockroom_id=b.stockroom_id "
        "WHERE " + " AND ".join(where) +
        " ORDER BY COALESCE(r.exposure_usd,0) DESC, b.item_id, b.stockroom_id", params)
    matched = []
    total = 0
    for row in records:
        payload = json.loads(row["payload"])
        description = str(payload.get("item_desc") or "")
        cat = part_category.categorise(description, rules)
        if category is not None and cat != category:
            continue
        if query and query.strip().casefold() not in (str(row["item_id"]) + " " + description).casefold():
            continue
        if reason_code and reason_code.casefold() not in str(row["reason_code"] or "").casefold():
            continue
        state = derive_status(row, reviews.get((row["item_id"], row["stockroom_id"]))) if row["scored_item"] else "unscored"
        if status and state != status:
            continue
        if uncovered_dormant:
            rule = dormant_rules.resolve(dormant, row["item_id"], cat)
            if dormant_rules.apply(rule, payload.get("max_qty")) is not None:
                continue
        total += 1
        if total <= offset or len(matched) >= limit:
            continue
        matched.append({key: row[key] for key in (
            "item_id", "stockroom_id", "action", "risk_level", "reason_code",
            "route", "new_max", "new_rop", "new_min", "exposure_usd")}
            | {"item_desc": description, "part_category": cat, "status": state,
               "current_max": _quantity(payload.get("max_qty")),
               "current_rop": _quantity(payload.get("rop_qty")),
               "current_min": _quantity(payload.get("min_qty"))})
    ctx.sources.append({"type": "recommendation_result" if needs_scores else "bom_rows",
                        "batch_id": bid, "count": total})
    return {"batch_id": bid, "items": matched, "total_count": total,
            "returned_count": len(matched), "offset": offset, "limit": limit,
            "has_more": offset + len(matched) < total,
            "category_basis": "current confirmed category rules",
            "category_rules_skipped": len(broken),
            "scope": "non-quarantined uploaded rows",
            "filters": {"query": query, "category": category, "stockroom_id": stockroom_id,
                        "risk_level": risk_level, "action": action, "reason_code": reason_code,
                        "route": route, "status": status, "uncovered_dormant": uncovered_dormant}}


SEARCH_PROPERTIES = {
    "batch_id": {"type": "integer", "description": "Workspace ID; omit to use the selected workspace."},
    "query": {"type": "string", "description": "Literal substring of item ID or description, e.g. tubing. Do not put the full question here."},
    "category": {"type": "string", "description": "Part category, e.g. cable, sensor, hose_tube, or uncategorised. Uses confirmed rules, not a keyword guess."},
    "stockroom_id": {"type": "string"},
    "risk_level": {"type": "string", "enum": ["Low", "Medium", "High"]},
    "action": {"type": "string", "enum": ["Increase", "Maintain", "Decrease"]},
    "reason_code": {"type": "string"},
    "route": {"type": "string", "enum": ["active", "dying", "dormant", "no-data"]},
    "status": {"type": "string", "enum": ["pending_review", "awaiting_senior", "reviewed", "auto_cleared"]},
    "uncovered_dormant": {"type": "boolean", "description": "True to list dormant rows with no applicable confirmed stocking rule. Requires a review role."},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    "offset": {"type": "integer", "minimum": 0},
}
