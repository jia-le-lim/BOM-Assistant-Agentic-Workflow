"""Image capture and engineer-owned reminders. Never writes review decisions."""

import base64
from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from ..audit import audit
from ..db import get_conn
from ..llm import get_provider
from ..reminders import (MAX_IMAGE_BYTES, REMINDER_COLUMNS, extract_image,
                         present_reminder, reminder_today, validate_image)
from ..security import REVIEW_ROLES, any_role, require_role, require_workspace

router = APIRouter(prefix="/reminders")
writers = require_role(*REVIEW_ROLES)


class ReminderDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=160)
    item_id: str = Field(default="", max_length=100)
    stockroom_id: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=10000)
    timing: Literal["next_cycle", "date"] = "next_cycle"
    due_date: date | None = None
    origin_batch_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_timing(self):
        if self.timing == "date" and self.due_date is None:
            raise ValueError("Choose a reminder date.")
        if self.timing == "next_cycle":
            self.due_date = None
        return self


class ReminderStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "completed", "dismissed"]


def _owned(conn, reminder_id: str, actor: dict):
    row = conn.execute(
        f"SELECT {REMINDER_COLUMNS} FROM engineer_reminder r "
        "WHERE r.reminder_id=? AND r.owner_user=?", (reminder_id, actor["user"])).fetchone()
    if row is None:
        raise HTTPException(404, "Reminder not found")
    return row


async def _image(file: UploadFile) -> tuple[str, str]:
    try:
        content = await file.read(MAX_IMAGE_BYTES + 1)
    finally:
        await file.close()
    return await run_in_threadpool(validate_image, content)


@router.post("/extract")
async def read_screenshot(file: UploadFile = File(...), actor: dict = Depends(writers)):
    mime, encoded = await _image(file)
    # No database or tool access during extraction. Nothing is saved until Save.
    return await run_in_threadpool(extract_image, get_provider(), mime, encoded)


@router.get("")
def list_reminders(status: Literal["open", "completed", "dismissed", "all"] = "open",
                   batch_id: int | None = None, item_id: str | None = None,
                   stockroom_id: str | None = None, due_only: bool = False,
                   limit: int = Query(default=50, ge=1, le=100),
                   offset: int = Query(default=0, ge=0), actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        where, params = ["r.owner_user=?"], [actor["user"]]
        if status != "all":
            where.append("r.status=?")
            params.append(status)
        if item_id is not None:
            where.append("r.item_id=?")
            params.append(item_id)
        if stockroom_id is not None:
            where.append("r.stockroom_id=?")
            params.append(stockroom_id)
        if batch_id is not None:
            require_workspace(conn, batch_id, actor, allow_shared=True)
            where.append("EXISTS (SELECT 1 FROM bom_rows br WHERE br.batch_id=? "
                         "AND br.item_id=r.item_id AND br.stockroom_id=r.stockroom_id "
                         "AND br.quarantined=0)")
            params.append(batch_id)
        if due_only:
            where.append("r.status='open' AND ((r.timing='date' AND r.due_date<=?) "
                         "OR (r.timing='next_cycle' AND r.matched_batch_id IS NOT NULL))")
            params.append(reminder_today())
        condition = " AND ".join(where)
        total = conn.execute(f"SELECT COUNT(*) c FROM engineer_reminder r WHERE {condition}",
                             params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT {REMINDER_COLUMNS} FROM engineer_reminder r WHERE {condition} "
            "ORDER BY CASE WHEN r.status='open' AND ((r.timing='date' AND r.due_date<=?) "
            "OR (r.timing='next_cycle' AND r.matched_batch_id IS NOT NULL)) THEN 0 ELSE 1 END, "
            "r.created_at DESC, r.reminder_id LIMIT ? OFFSET ?",
            (*params, reminder_today(), limit, offset)).fetchall()
        return {"reminders": [present_reminder(r) for r in rows], "total": total}
    finally:
        conn.close()


@router.post("", status_code=201)
async def create_reminder(payload: str = Form(..., max_length=15000),
                          file: UploadFile | None = File(default=None),
                          actor: dict = Depends(writers)):
    try:
        body = ReminderDraft.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(e["msg"] for e in exc.errors())) from exc
    mime, encoded = await _image(file) if file else (None, None)
    return await run_in_threadpool(_save_reminder, body, actor, mime, encoded)


def _save_reminder(body: ReminderDraft, actor: dict, mime, encoded):
    conn = get_conn()
    try:
        if body.origin_batch_id is not None:
            require_workspace(conn, body.origin_batch_id, actor)
        reminder_id = str(body.request_id)
        conn.execute(
            "INSERT INTO engineer_reminder (reminder_id, owner_user, title, item_id, "
            "stockroom_id, note, timing, due_date, origin_batch_id, image_mime, image_data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (reminder_id) DO NOTHING",
            (reminder_id, actor["user"], body.title, body.item_id, body.stockroom_id,
             body.note, body.timing, body.due_date.isoformat() if body.due_date else None,
             body.origin_batch_id, mime, encoded))
        row = _owned(conn, reminder_id, actor)
        audit(conn, actor, "POST", "/reminders", "engineer_reminder", reminder_id,
              {"item_id": row["item_id"], "stockroom_id": row["stockroom_id"], "timing": row["timing"]})
        conn.commit()
        return present_reminder(row)
    finally:
        conn.close()


@router.post("/{reminder_id}/edit")
def edit_reminder(reminder_id: UUID, body: ReminderDraft, actor: dict = Depends(writers)):
    conn = get_conn()
    try:
        old = _owned(conn, str(reminder_id), actor)
        # Changing the matching key or timing starts a fresh wait for a future upload.
        reset = (old["item_id"], old["stockroom_id"], old["timing"]) != (
            body.item_id, body.stockroom_id, body.timing)
        conn.execute(
            "UPDATE engineer_reminder SET title=?, item_id=?, stockroom_id=?, note=?, "
            "timing=?, due_date=?, matched_batch_id=?, updated_at=datetime('now') "
            "WHERE reminder_id=? AND owner_user=?",
            (body.title, body.item_id, body.stockroom_id, body.note, body.timing,
             body.due_date.isoformat() if body.due_date else None,
             None if reset else old["matched_batch_id"], str(reminder_id), actor["user"]))
        audit(conn, actor, "POST", f"/reminders/{reminder_id}/edit", "engineer_reminder",
              str(reminder_id), {"matching_reset": reset})
        conn.commit()
        return present_reminder(_owned(conn, str(reminder_id), actor))
    finally:
        conn.close()


@router.post("/{reminder_id}/status")
def change_status(reminder_id: UUID, body: ReminderStatus, actor: dict = Depends(writers)):
    conn = get_conn()
    try:
        _owned(conn, str(reminder_id), actor)
        conn.execute("UPDATE engineer_reminder SET status=?, updated_at=datetime('now') "
                     "WHERE reminder_id=? AND owner_user=?",
                     (body.status, str(reminder_id), actor["user"]))
        audit(conn, actor, "POST", f"/reminders/{reminder_id}/status", "engineer_reminder",
              str(reminder_id), {"status": body.status})
        conn.commit()
        return present_reminder(_owned(conn, str(reminder_id), actor))
    finally:
        conn.close()


@router.get("/{reminder_id}/image")
def reminder_image(reminder_id: UUID, actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        row = conn.execute("SELECT image_mime, image_data FROM engineer_reminder "
                           "WHERE reminder_id=? AND owner_user=?", (str(reminder_id), actor["user"])).fetchone()
        if not row or not row["image_mime"]:
            raise HTTPException(404, "Screenshot not found")
        return Response(base64.b64decode(row["image_data"]), media_type=row["image_mime"],
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    finally:
        conn.close()
