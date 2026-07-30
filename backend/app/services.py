"""Shared workflow logic: review status derivation and WINGS export assembly."""

import json
from typing import Any

from .config import REQUIRE_REVIEW_FOR_ALL_CHANGES
from .db import Conn


def latest_reviews(conn: Conn, batch_id: int) -> dict:
    """Latest review per (item_id, stockroom_id) for a batch."""
    out: dict[tuple, Any] = {}
    for r in conn.execute(
        "SELECT * FROM review_history WHERE batch_id=? ORDER BY review_id", (batch_id,)
    ):
        out[(r["item_id"], r["stockroom_id"])] = r  # later rows overwrite: latest wins
    return out


class AmbiguousItem(LookupError):
    """Item sits in more than one stockroom and the caller did not say which."""

    def __init__(self, item_id: str, stockrooms: list[str]):
        self.item_id, self.stockrooms = item_id, stockrooms
        super().__init__(f"item {item_id} is in stockrooms "
                         f"{', '.join(stockrooms)}; pass stockroom_id")


def resolve_rec(conn: Conn, batch_id: int, item_id: str,
                stockroom_id: str | None = None) -> Any | None:
    """The one recommendation_result row for an item, or None.

    The PK is (batch_id, item_id, stockroom_id). A two-column lookup with
    fetchone() silently picks one row for an item stocked in two stockrooms --
    the Jan'26 extract has such items -- and the other row then has no reachable
    endpoint at all: permanently pending_review, never exported. Ambiguity is
    raised, not guessed.
    """
    sql = "SELECT * FROM recommendation_result WHERE batch_id=? AND item_id=?"
    params: list[Any] = [batch_id, item_id]
    if stockroom_id is not None:
        sql += " AND stockroom_id=?"
        params.append(stockroom_id)
    rows = conn.execute(sql + " ORDER BY stockroom_id", params).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise AmbiguousItem(item_id, [r["stockroom_id"] for r in rows])
    return rows[0]


def derive_status(rec: Any, review: Any | None) -> str:
    """Workflow state of one recommendation row.

    auto_cleared     engine says no change and no risk flag -- no human needed
    pending_review   awaiting an engineer (incl. changed rows the engine did not
                     flag, when REQUIRE_REVIEW_FOR_ALL_CHANGES is on)
    awaiting_senior  reviewed, but override/high-risk needs senior approval
    reviewed         final -- eligible for export
    """
    if review is not None:
        if review["requires_senior_approval"] and not review["senior_approved_by"]:
            return "awaiting_senior"
        return "reviewed"
    if rec["review_required"] == "Y":
        return "pending_review"
    if rec["action"] != "Maintain" and REQUIRE_REVIEW_FOR_ALL_CHANGES:
        return "pending_review"
    return "auto_cleared"


def current_values(conn: Conn, batch_id: int, item_id: str,
                   stockroom_id: str) -> tuple[int, int, int]:
    row = conn.execute(
        "SELECT payload FROM bom_rows WHERE batch_id=? AND item_id=? AND stockroom_id=?",
        (batch_id, item_id, stockroom_id)).fetchone()
    if row is None:
        raise KeyError(f"item {item_id} not in batch {batch_id}")
    p = json.loads(row["payload"])

    def as_int(v) -> int:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    return as_int(p.get("max_qty")), as_int(p.get("rop_qty")), as_int(p.get("min_qty"))


def build_export(conn: Conn, batch_id: int) -> dict:
    """WINGS update rows: only reviewed-final rows whose values actually change.

    PRD section 11 Phase 2: file generated only after approval. Auto-cleared rows
    propose no change, so they never appear. Pending / awaiting-senior rows are
    counted but excluded.
    """
    reviews = latest_reviews(conn, batch_id)
    recs = conn.execute(
        "SELECT * FROM recommendation_result WHERE batch_id=?", (batch_id,)).fetchall()

    lines, pending, awaiting = [], 0, 0
    for rec in recs:
        key = (rec["item_id"], rec["stockroom_id"])
        review = reviews.get(key)
        status = derive_status(rec, review)
        if status == "pending_review":
            pending += 1
            continue
        if status == "awaiting_senior":
            awaiting += 1
            continue
        if status != "reviewed":
            continue
        cur = current_values(conn, batch_id, *key)
        final = (review["final_max"], review["final_rop"], review["final_min"])
        if tuple(final) == cur:
            continue  # reviewed but no change -> nothing to update in WINGS
        lines.append({
            "item_id": rec["item_id"], "stockroom_id": rec["stockroom_id"],
            "current_max": cur[0], "current_rop": cur[1], "current_min": cur[2],
            "new_max": final[0], "new_rop": final[1], "new_min": final[2],
            "decision": review["decision"], "reviewer": review["reviewer"],
            "senior_approved_by": review["senior_approved_by"] or "",
            "reviewed_at": review["reviewed_at"],
            "rule_version": rec["rule_version"],
        })
    return {"rows": lines, "pending_review": pending, "awaiting_senior": awaiting}
