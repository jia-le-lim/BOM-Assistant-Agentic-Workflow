"""An explicitly configured observer can read all workspaces, never edit them."""

import json

import pytest

from conftest import make_row, rows_to_csv, upload
from app.agent import tools as T
from app.agent.graph import _history
from app.db import get_conn
from app.security import can_read_all_workspaces

PILOT = {"X-User": "pilot-1", "X-Role": "admin"}
OWNER = {"X-User": "tcb-1", "X-Role": "admin"}
OTHER = {"X-User": "epoxy-1", "X-Role": "admin"}
ACTOR = {"user": "pilot-1", "role": "admin"}


@pytest.fixture()
def shared(client, monkeypatch):
    monkeypatch.setenv("BOM_WORKSPACE_READ_ALL_USERS", "pilot-1")
    ids = {}
    for headers in (PILOT, OWNER, OTHER):
        user = headers["X-User"]
        result = upload(client, rows_to_csv([make_row(item_id="100005", item_desc="CABLE", max_qty=4)]),
                        headers=headers, label=user)
        assert result.status_code == 200, result.text
        bid = result.json()["batch_id"]
        assert client.post(f"/run-recommendation?batch_id={bid}", headers=headers).status_code == 200
        assert client.post(f"/review/100005?batch_id={bid}", headers=headers,
                           json={"decision": "accept", "comment": user + " evidence"}).status_code == 200
        ids[user] = bid
    return ids


def test_grant_is_explicit_exact_and_read_only_by_default(monkeypatch):
    assert not can_read_all_workspaces(ACTOR)
    monkeypatch.setenv("BOM_WORKSPACE_READ_ALL_USERS", " pilot-1, another-reader ")
    assert can_read_all_workspaces(ACTOR)
    for user in ("pilot", "pilot-10", "PILOT-1", "tcb-1", "anonymous", ""):
        assert not can_read_all_workspaces({"user": user, "role": "admin"})


def test_observer_lists_existing_and_future_workspaces(client, shared):
    new = client.post("/batches", headers=OTHER, json={"label": "Future workspace"}).json()
    visible = client.get("/batches", headers=PILOT).json()
    assert {batch["batch_id"] for batch in visible} == {*shared.values(), new["batch_id"]}
    for headers in (OWNER, OTHER):
        assert all(batch["uploaded_by"] == headers["X-User"]
                   for batch in client.get("/batches", headers=headers).json())


def test_observer_can_read_workspace_details_and_history(client, shared):
    bid = shared["tcb-1"]
    for path in (f"/batches/{bid}/summary", f"/recommendations?batch_id={bid}",
                 f"/recommendations/100005?batch_id={bid}", f"/assist/{bid}",
                 f"/similarity/{bid}", f"/pending-changes?batch_id={bid}",
                 f"/config/part-categories/coverage?batch_id={bid}",
                 f"/config/dormant-rules/coverage?batch_id={bid}",
                 f"/export/wings?batch_id={bid}", f"/export/wings.xlsx?batch_id={bid}"):
        result = client.get(path, headers=PILOT)
        assert result.status_code == 200, (path, result.text)
    assert client.get(f"/batches/{bid}/summary", headers=PILOT).json()["read_only"] is True
    assert client.get(f"/recommendations/100005?batch_id={bid}", headers=PILOT).json()["read_only"] is True
    own = client.get(f"/batches/{shared['pilot-1']}/summary", headers=PILOT).json()
    assert own["read_only"] is False
    history = client.get("/history/100005", headers=PILOT).json()["reviews"]
    assert {row["batch_id"] for row in history} == set(shared.values())
    assert client.get("/batches/999999/summary", headers=PILOT).status_code == 404


def test_observer_cannot_modify_foreign_workspaces(client, shared):
    bid = shared["tcb-1"]
    writes = [(f"/run-recommendation?batch_id={bid}", None),
              (f"/assist/run?batch_id={bid}", None),
              ("/similarity/run", {"batch_id": bid}),
              (f"/review/100005?batch_id={bid}", {"decision": "accept"}),
              ("/review/bulk", {"batch_id": bid, "decision": "accept", "items": [{"item_id": "100005"}]}),
              (f"/review/100005/approve?batch_id={bid}", None)]
    for path, body in writes:
        denied = client.post(path, headers=PILOT, json=body)
        assert denied.status_code == 404, (path, denied.text)
    draft = client.post("/batches", headers=OWNER, json={"label": "Owner's draft"}).json()["batch_id"]
    response = client.post("/upload-bom-file", headers=PILOT,
                           files={"file": ("test.csv", rows_to_csv([make_row(item_id="100005")]), "text/csv")},
                           data={"batch_id": draft, "label": "Attempted foreign upload"})
    assert response.status_code == 404, response.text
    own = client.post("/batches", headers=PILOT, json={"label": "Pilot can still create"})
    assert own.status_code == 201 and own.json()["uploaded_by"] == "pilot-1"
    proposal = client.post("/chat", headers=PILOT,
                           json={"question": "/propose 100005 max 3", "batch_id": shared["pilot-1"]})
    assert proposal.status_code == 200, proposal.text
    assert client.get("/pending-changes", headers=PILOT).json()["count"] == 1


@pytest.mark.parametrize("stream", [False, True])
def test_chat_reads_shared_workspace_but_keeps_conversations_private(client, shared, stream):
    bid = shared["tcb-1"]
    response = client.post("/chat/stream" if stream else "/chat", headers=PILOT,
                           json={"question": "/explain 100005", "batch_id": bid})
    assert response.status_code == 200, response.text
    result = json.loads(response.text.splitlines()[-1]) if stream else response.json()
    assert result["sources"] and result["batch_id"] == bid
    saved = client.get(f"/chat/sessions/{result['session_id']}", headers=PILOT).json()
    assert saved["turns"][0]["batch_id"] == bid
    assert client.get("/chat/sessions", headers=PILOT).json()["sessions"][0]["session_id"] == result["session_id"]
    assert client.get(f"/chat/sessions/{result['session_id']}", headers=OWNER).status_code == 404
    owner_chat = client.post("/chat", headers=OWNER,
                             json={"question": "hi", "batch_id": bid}).json()
    assert client.get(f"/chat/sessions/{owner_chat['session_id']}", headers=PILOT).status_code == 404
    conn = get_conn()
    try:
        assert _history(conn, result["session_id"], "pilot-1")[0]["question"] == "/explain 100005"
    finally:
        conn.close()
    proposal = client.post("/chat", headers=PILOT,
                           json={"question": "/propose 100005 max 3", "batch_id": bid})
    assert proposal.status_code == 403, proposal.text


def test_chat_tools_cannot_bypass_shared_read_permission(client, shared):
    conn = get_conn()
    try:
        bid = shared["tcb-1"]
        for name, args in (("propose_change", {"item_id": "100005", "proposed_max": 3}),
                           ("run_assist", {"confirm": True}),
                           ("stage_review_action", {"kind": "confirm_pending", "item_id": "100005"})):
            ctx = T.ToolContext(conn, ACTOR, shared["pilot-1"], "set item 100005 max to 3")
            denied = json.loads(T.dispatch(ctx, name, {"batch_id": bid, **args}))
            assert denied["response_status"] == "permission_denied", (name, denied)
            assert not ctx.sources and not ctx.page_actions
            with pytest.raises(T.ToolError):
                T.REGISTRY[name][1](ctx, batch_id=bid, **args)
        assert conn.execute("SELECT COUNT(*) AS n FROM pending_change").fetchone()["n"] == 0
        ctx = T.ToolContext(conn, ACTOR, bid, "show history")
        history = json.loads(T.dispatch(ctx, "get_item_history", {"item_id": "100005"}))
        assert {row["batch_id"] for row in history["reviews"]} == set(shared.values())
    finally:
        conn.close()


def test_category_search_uses_workspace_owner_rules(client, shared):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO user_part_category_config "
                     "(owner_user,pattern,category,priority,confirmed) VALUES (?,?,?,?,?)",
                     ("tcb-1", "CABLE", "owner_category", 0, 1))
        conn.commit()
        ctx = T.ToolContext(conn, ACTOR, shared["tcb-1"], "find owner_category items")
        result = json.loads(T.dispatch(ctx, "search_items", {"category": "owner_category"}))
        assert result["total_count"] == 1, result
        assert ctx.sources
    finally:
        conn.close()


def test_revoking_read_grant_hides_shared_workspaces(client, shared, monkeypatch):
    monkeypatch.setenv("BOM_WORKSPACE_READ_ALL_USERS", "")
    assert {row["batch_id"] for row in client.get("/batches", headers=PILOT).json()} == {shared["pilot-1"]}
    assert client.get(f"/batches/{shared['tcb-1']}/summary", headers=PILOT).status_code == 404
