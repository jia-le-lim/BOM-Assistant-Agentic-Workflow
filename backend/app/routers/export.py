"""GET /export/wings -- approved WINGS update file (PRD section 11, Phase 2)."""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..audit import audit
from ..db import get_conn
from ..export_xlsx import build_workbook
from ..security import EXPORT_ROLES, require_role
from ..services import build_export

XLSX_MEDIA = ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet")

router = APIRouter()

COLUMNS = ["item_id", "stockroom_id", "current_max", "current_rop", "current_min",
           "new_max", "new_rop", "new_min", "decision", "reviewer",
           "senior_approved_by", "reviewed_at", "rule_version"]


def _scored_batch(conn, batch_id: int):
    b = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
    if b is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    if b["status"] != "scored":
        raise HTTPException(409, f"batch {batch_id} is '{b['status']}', not scored")
    return b


@router.get("/export/wings.xlsx")
def export_wings_xlsx(batch_id: int,
                      actor: dict = Depends(require_role(*EXPORT_ROLES))):
    """The workbook with the approved numbers already in their cells.

    Same source of truth and same role gate as the CSV: only rows a human
    reviewed (and a senior approved, where required) carry values.
    """
    conn = get_conn()
    try:
        _scored_batch(conn, batch_id)
        content, counts = build_workbook(conn, batch_id)
        audit(conn, actor, "GET", "/export/wings.xlsx", "batch", batch_id, counts)
        conn.commit()
        return Response(
            content=content, media_type=XLSX_MEDIA,
            headers={
                "Content-Disposition":
                    f"attachment; filename=wings_update_batch{batch_id}.xlsx",
                "X-Rows-Updated": str(counts["updated"]),
                "X-Rows-Acknowledged": str(counts["acknowledged"]),
                "X-Pending-Review": str(counts["pending_review"]),
                "X-Awaiting-Senior": str(counts["awaiting_senior"]),
            })
    finally:
        conn.close()


@router.get("/export/wings")
def export_wings(batch_id: int, actor: dict = Depends(require_role(*EXPORT_ROLES))):
    conn = get_conn()
    try:
        _scored_batch(conn, batch_id)

        result = build_export(conn, batch_id)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(result["rows"])

        audit(conn, actor, "GET", "/export/wings", "batch", batch_id,
              {"rows_exported": len(result["rows"]),
               "pending_review": result["pending_review"],
               "awaiting_senior": result["awaiting_senior"]})
        conn.commit()
        return Response(
            content=buf.getvalue(), media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=wings_update_batch{batch_id}.csv",
                "X-Rows-Exported": str(len(result["rows"])),
                "X-Pending-Review": str(result["pending_review"]),
                "X-Awaiting-Senior": str(result["awaiting_senior"]),
            })
    finally:
        conn.close()
