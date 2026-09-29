"""POST /upload-bom-file -- CSV upload, normalize, quarantine, persist (PRD section 7)."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from ..audit import audit
from ..config import DEFAULT_MODULE_FILTER, MAX_UPLOAD_BYTES
from ..db import get_conn
from ..ingestion import IngestionConflict, IngestionError, ingest
from ..security import UPLOAD_ROLES, require_role, require_workspace

router = APIRouter()


class WorkspaceCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    module_filter: str = Field(default="TCB", pattern="^(TCB|Epoxy|ALL)$")

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a workspace name")
        return value


@router.post("/batches", status_code=201)
def create_workspace(body: WorkspaceCreate,
                     actor: dict = Depends(require_role(*UPLOAD_ROLES))):
    """One persisted draft batch is one BOM review workspace, before upload."""
    conn = get_conn()
    try:
        batch_id = conn.insert_returning(
            "INSERT INTO batches (label, module_filter, uploaded_by, status, "
            "row_count, quarantined_count) VALUES (?,?,?,?,?,?)",
            (body.label, body.module_filter, actor["user"], "draft", 0, 0), "batches")
        audit(conn, actor, "POST", "/batches", "batch", batch_id, body.model_dump())
        conn.commit()
        return dict(conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone())
    finally:
        conn.close()


@router.post("/upload-bom-file")
async def upload_bom_file(
    file: UploadFile = File(...),
    label: str = Form(...),
    module_filter: str = Form(default=DEFAULT_MODULE_FILTER),
    module_match: str = Form(default="exact"),
    batch_id: int | None = Form(default=None),
    actor: dict = Depends(require_role(*UPLOAD_ROLES)),
):
    if not (file.filename or "").lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "Only .csv, .xlsx or .xls BOM review files are accepted")
    if module_match not in ("exact", "tag"):
        raise HTTPException(400, "module_match must be 'exact' or 'tag'")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File exceeds upload limit")

    conn = get_conn()
    try:
        if batch_id is not None:
            workspace = require_workspace(conn, batch_id, actor)
            if workspace["status"] != "draft":
                raise HTTPException(409, "This workspace already has a datasheet. Create a workspace for a new review cycle.")
            # Workspace identity and scope are set at creation, not by upload fields.
            label, module_filter = workspace["label"], workspace["module_filter"]
        try:
            summary = ingest(conn, content, label, file.filename, module_filter,
                             actor["user"], match_mode=module_match, batch_id=batch_id)
        except IngestionConflict as e:
            raise HTTPException(409, str(e)) from e
        except IngestionError as e:
            raise HTTPException(400, str(e)) from e
        audit(conn, actor, "POST", "/upload-bom-file", "batch",
              summary["batch_id"], summary)
        conn.commit()
        return summary
    finally:
        conn.close()
