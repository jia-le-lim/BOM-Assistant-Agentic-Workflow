"""POST /upload-bom-file -- CSV upload, normalize, quarantine, persist (PRD section 7)."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..audit import audit
from ..config import DEFAULT_MODULE_FILTER, MAX_UPLOAD_BYTES
from ..db import get_conn
from ..ingestion import IngestionError, ingest
from ..security import UPLOAD_ROLES, require_role

router = APIRouter()


@router.post("/upload-bom-file")
async def upload_bom_file(
    file: UploadFile = File(...),
    label: str = Form(...),
    module_filter: str = Form(default=DEFAULT_MODULE_FILTER),
    actor: dict = Depends(require_role(*UPLOAD_ROLES)),
):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted in the scaffold "
                                 "(Excel support: convert to CSV first)")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File exceeds upload limit")

    conn = get_conn()
    try:
        try:
            summary = ingest(conn, content, label, file.filename, module_filter,
                             actor["user"])
        except IngestionError as e:
            raise HTTPException(400, str(e)) from e
        audit(conn, actor, "POST", "/upload-bom-file", "batch",
              summary["batch_id"], summary)
        conn.commit()
        return summary
    finally:
        conn.close()
