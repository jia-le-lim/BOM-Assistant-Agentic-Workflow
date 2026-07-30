"""The reviewed batch as a workbook, with the decision columns already filled.

The point of this file is to remove the step where an engineer opens the
monthly workbook and retypes numbers they have already approved in the console
-- the step where a transposed digit becomes a stock level.

Deliberately not an agent. An LLM here would put a nondeterministic component
in the one artifact PRD section 10 requires to be reproducible, and would cross
the boundary `agent/tools.py` exists to hold: the agent's only write target is
`pending_change`, and nothing it does reaches an export. This is a table
transformation -- for each reviewed row, copy three integers the engineer
already approved into three named columns.

Two sheets:
  BOM             every ingested row, original columns in the original order,
                  values exactly as they were read. Only the review columns are
                  written, so what WINGS receives is what it sent plus the
                  decisions.
  Review status   what happened to each row, including the ones still waiting.
                  It lives on its own sheet so sheet 1 keeps the input's shape
                  and an unexpected column cannot break a WINGS import.

Which rows get numbers mirrors build_export(): a reviewed row whose final
values equal the current ones is an acknowledgement, not an update, so it gets
the reviewer stamp but no new quantities.
"""

from __future__ import annotations

import io
import json
from typing import Any

from .db import Conn
from .ingestion import COLUMN_RENAMES
from .services import derive_status, latest_reviews

# Ingestion normalises two misspelled headers. The file has to go back with the
# names WINGS sent, or the columns no longer line up on import.
UNRENAME = {v: k for k, v in COLUMN_RENAMES.items()}

# Where a decision goes. These are the workbook's own output columns -- the
# same ones engine.py refuses to read (its FORBIDDEN list), because they are
# what the review produces rather than what it consumes.
COL_MAX = "factory_recommended_new_max"
COL_ROP = "factory_recommended_new_rop"
COL_MIN = "factory_recommended_new_min"
COL_ACK = "review_acknowledge"
COL_USER = "modified_user"
COL_DATE = "modified_date"
COL_JUSTIFICATION = "justification"
COL_COMMENTS = "comments"

FILLED_COLUMNS = (COL_MAX, COL_ROP, COL_MIN, COL_ACK, COL_USER, COL_DATE,
                  COL_JUSTIFICATION, COL_COMMENTS)

# Cleared on every row: each of these asserts "approved NOW", so last cycle's
# value would present a stale approval in the cell WINGS reads.
STALE_COLUMNS = (COL_MAX, COL_ROP, COL_MIN, COL_ACK, COL_USER, COL_DATE)

# Written only where a review actually produced text. These are commentary, not
# approval signals, so blanking them on a row nobody reviewed would discard what
# WINGS sent -- and sheet 1 promises the input back as it was read.
REVIEW_TEXT_COLUMNS = (COL_JUSTIFICATION, COL_COMMENTS)

# Excel treats a leading =, +, - or @ as the start of a formula, and a leading
# tab or CR as a cell break.
FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")

STATUS_COLUMNS = ["item_id", "stockroom_id", "status", "action", "risk_level",
                  "current_max", "final_max", "decision", "reviewer",
                  "senior_approved_by", "reviewed_at", "reason_code", "note"]


def _safe_text(value: Any) -> str:
    """Reviewer free text, neutralised so Excel cannot execute it.

    This workbook is emailed around and re-imported, so a justification that
    starts with `=` is a live formula the next person to open the file runs. A
    leading apostrophe is Excel's own "treat this as text" marker and is not
    shown as part of the value.

    Applied only to the cells this module writes. Payload passthrough is left
    exactly as read -- prefixing WINGS' own data would corrupt the round trip,
    and "-5" is a legitimate value there.
    """
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in FORMULA_LEADERS else s


def _payload_columns(payloads: list[dict]) -> list[str]:
    """Original column order, from the first row, plus any key a later row adds.

    Ingestion stores each record as read (`read_csv(dtype=str).to_dict`), so key
    order is the workbook's column order.
    """
    cols: list[str] = []
    seen: set[str] = set()
    for p in payloads:
        for k in p:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    for c in FILLED_COLUMNS:            # present even if the input lacked them
        if c not in seen:
            seen.add(c)
            cols.append(c)
    return cols


def build_workbook(conn: Conn, batch_id: int) -> tuple[bytes, dict[str, int]]:
    """Returns (xlsx bytes, counts)."""
    from openpyxl import Workbook

    reviews = latest_reviews(conn, batch_id)
    recs = {(r["item_id"], r["stockroom_id"]): r for r in conn.execute(
        "SELECT * FROM recommendation_result WHERE batch_id=?", (batch_id,))}

    # Row order: the ingested order is recorded nowhere, so this is sorted by
    # key rather than "as uploaded".
    rows = conn.execute(
        "SELECT item_id, stockroom_id, quarantined, quarantine_reason, payload "
        "FROM bom_rows WHERE batch_id=? ORDER BY item_id, stockroom_id",
        (batch_id,)).fetchall()

    payloads = [json.loads(r["payload"]) for r in rows]
    columns = _payload_columns(payloads)

    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append([UNRENAME.get(c, c) for c in columns])
    status_rows: list[list[Any]] = []
    counts = {"rows": len(rows), "updated": 0, "acknowledged": 0,
              "pending_review": 0, "awaiting_senior": 0, "quarantined": 0}

    for row, payload in zip(rows, payloads):
        key = (row["item_id"], row["stockroom_id"])
        rec = recs.get(key)
        review = reviews.get(key)
        note = ""

        # The input arrives with these already populated -- last cycle's
        # recommendation and last cycle's engineer. Carrying them through would
        # present a stale number as this month's approval, in the cell WINGS
        # reads. Cleared unconditionally: a value here means approved NOW.
        # justification/comments are deliberately NOT cleared here -- they are
        # commentary rather than an approval, and a row nobody reviewed should
        # go back carrying whatever WINGS sent.
        for c in STALE_COLUMNS:
            payload[c] = ""

        if row["quarantined"]:
            status = "quarantined"
            note = row["quarantine_reason"] or ""
            counts["quarantined"] += 1
        elif rec is None:
            status = "not_scored"
        else:
            status = derive_status(rec, review)
            if status == "pending_review":
                counts["pending_review"] += 1
            elif status == "awaiting_senior":
                counts["awaiting_senior"] += 1

        if status == "reviewed" and review is not None:
            final = (review["final_max"], review["final_rop"], review["final_min"])
            current = (review["current_max"], review["current_rop"],
                       review["current_min"])
            payload[COL_ACK] = "Y"
            payload[COL_USER] = _safe_text(review["reviewer"])
            payload[COL_DATE] = review["reviewed_at"] or ""
            payload[COL_JUSTIFICATION] = _safe_text(review["justification"])
            payload[COL_COMMENTS] = _safe_text(review["comment"])
            if tuple(final) != tuple(current):
                payload[COL_MAX], payload[COL_ROP], payload[COL_MIN] = final
                counts["updated"] += 1
            else:
                # Reviewed, but nothing to change. Writing the same numbers back
                # would present a no-op to WINGS as a stock update.
                note = "reviewed, no change"
                counts["acknowledged"] += 1

        ws.append([payload.get(c, "") for c in columns])
        status_rows.append([
            row["item_id"], row["stockroom_id"], status,
            rec["action"] if rec else "", rec["risk_level"] if rec else "",
            review["current_max"] if review else "",
            review["final_max"] if review else "",
            review["decision"] if review else "",
            _safe_text(review["reviewer"]) if review else "",
            _safe_text(review["senior_approved_by"]) if review else "",
            review["reviewed_at"] if review else "",
            rec["reason_code"] if rec else "", _safe_text(note),
        ])

    st = wb.create_sheet("Review status")
    st.append(STATUS_COLUMNS)
    for r in status_rows:
        st.append(r)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), counts
