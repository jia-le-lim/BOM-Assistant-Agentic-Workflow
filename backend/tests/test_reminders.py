"""Reminder persistence, ownership, extraction boundaries, and future-cycle matching."""

import io
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from conftest import ENG, OWNER_VIEWER, VIEWER, make_row, rows_to_csv, upload

OTHER = {"X-User": "another", "X-Role": "admin"}


@pytest.fixture
def screenshot():
    out = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(out, format="PNG")
    return out.getvalue()


def create(client, *, headers=ENG, image=None, **values):
    payload = {"title": "Check ROP request", "item_id": "100001", "stockroom_id": "24",
               "note": "2:1 means what? Confirm with requester.", **values}
    files = {"file": ("screenshot.png", image, "image/png")} if image else None
    return client.post("/reminders", data={"payload": json.dumps(payload)}, files=files, headers=headers)


def listing(client, headers=ENG, query=""):
    response = client.get("/reminders" + query, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_screenshot_persists_privately_without_stocking_decision(client, screenshot):
    response = create(client, image=screenshot)
    assert response.status_code == 201, response.text
    saved = response.json()
    rid = saved["reminder_id"]
    assert saved["owner_user"] == "alice"
    assert saved["has_image"] and not saved["is_due"]
    assert "image_data" not in saved
    assert listing(client)["reminders"][0]["reminder_id"] == rid
    assert listing(client, OTHER)["reminders"] == []
    image = client.get(f"/reminders/{rid}/image", headers=ENG)
    assert image.content == screenshot
    assert image.headers["content-type"] == "image/png"
    assert "no-store" in image.headers["cache-control"]
    assert client.get(f"/reminders/{rid}/image", headers=OTHER).status_code == 404
    assert client.post(f"/reminders/{rid}/status", json={"status": "completed"}, headers=OTHER).status_code == 404
    assert client.post(f"/reminders/{rid}/status", json={"status": "completed"}, headers=OWNER_VIEWER).status_code == 403
    assert create(client, headers=VIEWER).status_code == 403
    from app.db import get_conn
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM review_history").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM pending_change").fetchone()["c"] == 0
    finally:
        conn.close()


def test_status_lifecycle_and_edits_preserve_evidence(client, screenshot):
    saved = create(client, image=screenshot).json()
    rid = saved["reminder_id"]
    edited = client.post(f"/reminders/{rid}/edit", json={
        "title": "Clarified request", "item_id": "100001", "stockroom_id": "24",
        "note": "Ask engineer before changing anything", "timing": "date", "due_date": "2020-01-02",
    }, headers=ENG)
    assert edited.status_code == 200, edited.text
    assert edited.json()["has_image"] and edited.json()["is_due"]
    assert client.get(f"/reminders/{rid}/image", headers=ENG).content == screenshot
    assert listing(client, query="?due_only=true")["total"] == 1
    for status in ("completed", "open", "dismissed"):
        response = client.post(f"/reminders/{rid}/status", json={"status": status}, headers=ENG)
        assert response.status_code == 200
        assert response.json()["status"] == status
        assert listing(client, query="?status=" + status)["total"] == 1
    assert listing(client)["total"] == 0


def test_invalid_values_and_unowned_origin_are_rejected(client):
    assert create(client, title="   ").status_code == 422
    assert create(client, timing="date").status_code == 422
    assert create(client, timing="date", due_date="2026-13-42").status_code == 422
    assert create(client, owner_user="another").status_code == 422
    assert create(client, origin_batch_id=99999).status_code == 404
    origin = upload(client, rows_to_csv([make_row(item_id="100001")]), headers=OTHER).json()["batch_id"]
    assert create(client, origin_batch_id=origin).status_code == 404


def test_retries_do_not_duplicate_and_cannot_overwrite_another_owner(client):
    request_id = str(uuid4())
    one = create(client, request_id=request_id).json()
    two = create(client, request_id=request_id, title="Retry").json()
    assert one["reminder_id"] == two["reminder_id"]
    assert two["title"] == one["title"]
    assert listing(client)["total"] == 1
    assert create(client, headers=OTHER, request_id=request_id).status_code == 404


def test_exact_future_cycle_match_and_owner_isolation(client):
    csv = rows_to_csv([make_row(item_id="100001")])
    old = upload(client, csv).json()["batch_id"]
    saved = create(client, origin_batch_id=old).json()
    rid = saved["reminder_id"]
    assert not saved["is_due"]
    assert listing(client, query=f"?batch_id={old}")["total"] == 1
    assert not listing(client)["reminders"][0]["is_due"], "An existing upload is not a future cycle"
    upload(client, rows_to_csv([make_row(item_id="100001", stockroom_id="33")]))
    upload(client, csv, headers=OTHER)
    upload(client, rows_to_csv([make_row(item_id="100001", last_30_day_cnsmptn_qty=-5)]))
    assert not listing(client)["reminders"][0]["is_due"]
    future = upload(client, csv).json()["batch_id"]
    ready = listing(client)["reminders"][0]
    assert ready["reminder_id"] == rid
    assert ready["matched_batch_id"] == future and ready["is_due"]
    upload(client, csv)
    assert listing(client)["reminders"][0]["matched_batch_id"] == future


def test_upload_into_preexisting_draft_triggers_reminder(client):
    draft = client.post("/batches", json={"label": "Next review"}, headers=ENG).json()
    create(client)
    response = client.post("/upload-bom-file", files={
        "file": ("next.csv", rows_to_csv([make_row(item_id="100001")]), "text/csv")},
        data={"label": "ignored", "batch_id": draft["batch_id"]}, headers=ENG)
    assert response.status_code == 200, response.text
    assert listing(client)["reminders"][0]["matched_batch_id"] == draft["batch_id"]


def test_incomplete_and_closed_reminders_never_auto_match(client):
    create(client, stockroom_id="")
    create(client, item_id="")
    closed = create(client).json()
    client.post(f'/reminders/{closed["reminder_id"]}/status', json={"status": "completed"}, headers=ENG)
    upload(client, rows_to_csv([make_row(item_id="100001")]))
    assert all(r["matched_batch_id"] is None for r in listing(client, query="?status=all")["reminders"])


def test_edit_matching_details_resets_wait_and_reminders_survive_workspace_deletion(client):
    origin = upload(client, rows_to_csv([make_row(item_id="100001")])).json()["batch_id"]
    saved = create(client, origin_batch_id=origin).json()
    upload(client, rows_to_csv([make_row(item_id="100001")]))
    rid = saved["reminder_id"]
    edited = client.post(f"/reminders/{rid}/edit", json={
        "title": saved["title"], "item_id": "100001", "stockroom_id": "33",
    }, headers=ENG)
    assert edited.json()["matched_batch_id"] is None
    from app.db import get_conn
    conn = get_conn()
    try:
        conn.execute("DELETE FROM batches WHERE batch_id=?", (origin,))
        conn.commit()
    finally:
        conn.close()
    row = listing(client)["reminders"][0]
    assert row["origin_batch_id"] is None and row["reminder_id"] == rid


def test_pagination_and_filters_hide_foreign_workspaces(client):
    for i in range(3):
        create(client, title=f"Reminder {i}", item_id=str(i))
    assert listing(client, query="?limit=2")["total"] == 3
    assert len(listing(client, query="?limit=2&offset=2")["reminders"]) == 1
    assert listing(client, query="?item_id=1")["total"] == 1
    foreign = upload(client, rows_to_csv([make_row(item_id="1")]), headers=OTHER).json()["batch_id"]
    assert client.get(f"/reminders?batch_id={foreign}", headers=ENG).status_code == 404


def test_extraction_has_no_persistence_or_tools(client, screenshot, monkeypatch):
    from app.routers import reminders
    prompts = []
    class Vision:
        model = "test-vision"
        def read_image(self, image, prompt):
            assert image.startswith("data:image/png;base64,")
            prompts.append(prompt)
            return json.dumps({"title": "Review ROP", "current_max": 1, "current_rop": 0,
                "proposed_change_text": "2:1", "stockroom_id": None,
                "uncertainties": ["Confirm what 2:1 means"], "due_date": "2026-07-26"})
    monkeypatch.setattr(reminders, "get_provider", Vision)
    response = client.post("/reminders/extract", files={"file": ("s.png", screenshot, "image/png")}, headers=ENG)
    assert response.status_code == 200
    result = response.json()
    assert result["warning"] is None
    assert result["extraction"]["stockroom_id"] is None
    assert result["extraction"]["current_max"] == 1
    assert "due_date" not in result["extraction"]
    assert "untrusted" in prompts[0] and "NOT a review-cycle date" in prompts[0]
    assert listing(client)["total"] == 0
    assert client.post("/reminders/extract", files={"file": ("s.png", screenshot, "image/png")}, headers=VIEWER).status_code == 403


@pytest.mark.parametrize("reply", ["not json", "[]", '{"current_max": "guess"}'])
def test_failed_extraction_keeps_manual_entry_available(client, screenshot, monkeypatch, reply):
    from app.routers import reminders
    monkeypatch.setattr(reminders, "get_provider",
                        lambda: SimpleNamespace(model="test", read_image=lambda *_: reply))
    result = client.post("/reminders/extract", files={"file": ("s.png", screenshot, "image/png")}, headers=ENG).json()
    assert result["warning"]
    assert result["extraction"]["item_id"] is None
    assert create(client, image=screenshot).status_code == 201


def test_offline_provider_offers_manual_entry(client, screenshot):
    result = client.post("/reminders/extract", files={"file": ("s.png", screenshot, "image/png")}, headers=ENG).json()
    assert result["warning"] and result["model"] is None


def test_invalid_images_and_size_are_rejected_before_model_call(client, monkeypatch):
    from app.routers import reminders
    from app.reminders import MAX_IMAGE_BYTES
    def unexpected():
        pytest.fail("Invalid images must not call the model")
    monkeypatch.setattr(reminders, "get_provider", unexpected)
    for data, expected in [(b"<svg>untrusted</svg>", 415), (b"x" * (MAX_IMAGE_BYTES + 1), 413),
                           (b"\x89PNG\r\n\x1a\ntruncated", 415)]:
        response = client.post("/reminders/extract", files={"file": ("s.png", data, "image/png")}, headers=ENG)
        assert response.status_code == expected
        assert create(client, image=data).status_code == expected


def test_nyra_sends_image_content_without_tool_access():
    from app.llm.nyra import NyraProvider
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"title":"Check ROP"}'))])
    provider = object.__new__(NyraProvider)
    provider.model = "test-vision"
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert provider.read_image("data:image/png;base64,TEST", "Read evidence") == '{"title":"Check ROP"}'
    request = calls[0]
    assert "tools" not in request and "tool_choice" not in request
    assert request["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64,TEST"
