"""GET /export/wings -- approved WINGS update file (PRD section 11, Phase 2)."""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..audit import audit
from ..db import get_conn
from ..security import EXPORT_ROLES, require_role
from ..services import build_export

router = APIRouter()

COLUMNS = ["item_id", "stockroom_id", "current_max", "current_rop", "current_min",
           "new_max", "new_rop", "new_min", "decision", "reviewer",
           "senior_approved_by", "reviewed_at", "rule_version"]


@router.get("/export/wings")
def export_wings(batch_id: int, actor: dict = Depends(require_role(*EXPORT_ROLES))):
    conn = get_conn()
    try:
        b = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if b is None:
            raise HTTPException(404, f"batch {batch_id} not found")
        if b["status"] != "scored":
            raise HTTPException(409, f"batch {batch_id} is '{b['status']}', not scored")

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
