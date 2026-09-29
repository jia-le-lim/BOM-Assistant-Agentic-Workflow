"""Ownership is enforced for all pilot users, including Administrator accounts."""
import json

import pytest

from app.agent.tools import REGISTRY, ToolContext, dispatch
from app.db import get_conn, init_db
from conftest import make_row, rows_to_csv, upload


TCB = {"X-User": "tcb-1", "X-Role": "admin"}
EPOXY = {"X-User": "epoxy-1", "X-Role": "admin"}


@pytest.fixture()
def workspaces(client):
    ids = []
    for headers, module in ((TCB, "TCB"), (EPOXY, "Epoxy")):
        data = rows_to_csv([make_row(item_id="100005", module=module)])
        result = upload(client, data, headers=headers, label=headers["X-User"], module=module)
        assert result.status_code == 200, result.text
        bid = result.json()["batch_id"]
        assert client.post("/run-recommendation", params={"batch_id": bid}, headers=headers).status_code == 200
        assert client.post(f"/review/100005?batch_id={bid}", headers=headers,
                           json={"decision": "accept", "comment": headers["X-User"] + " private"}).status_code == 200
        ids.append(bid)
    return ids


def test_lists_only_owned_workspaces_including_same_module(client, workspaces):
    same_module = client.post("/batches", headers=EPOXY,
                              json={"label": "Another TCB", "module_filter": "TCB"}).json()
    for actor, expected in ((TCB, {workspaces[0]}), (EPOXY, {workspaces[1], same_module["batch_id"]})):
        rows = client.get("/batches", headers=actor).json()
        assert {r["batch_id"] for r in rows} == expected
        assert all(r["uploaded_by"] == actor["X-User"] for r in rows)
    assert client.get("/batches").status_code == 401
    assert client.get("/batches", headers={"X-User": "new-user", "X-Role": "admin"}).json() == []


@pytest.mark.parametrize("owner,other,index", [(TCB, EPOXY, 0), (EPOXY, TCB, 1)])
def test_direct_read_and_write_paths_cannot_cross_owners(client, workspaces, owner, other, index):
    bid = workspaces[index]
    reads = [f"/batches/{bid}/summary", f"/recommendations?batch_id={bid}",
             f"/recommendations/100005?batch_id={bid}", f"/export/wings?batch_id={bid}",
             f"/export/wings.xlsx?batch_id={bid}", f"/assist/{bid}", f"/similarity/{bid}",
             f"/similarity/{bid}/100005", f"/pending-changes?batch_id={bid}",
             f"/config/part-categories/coverage?batch_id={bid}",
             f"/config/dormant-rules/coverage?batch_id={bid}"]
    for path in reads:
        denied = client.get(path, headers=other)
        assert denied.status_code == 404, (path, denied.text)
        if "/similarity/" not in path:
            assert client.get(path, headers=owner).status_code == 200, path
    writes = [(f"/run-recommendation?batch_id={bid}", None),
              (f"/assist/run?batch_id={bid}", None),
              ("/similarity/run", {"batch_id": bid}),
              (f"/review/100005?batch_id={bid}", {"decision": "accept"}),
              ("/review/bulk", {"batch_id": bid, "decision": "accept", "items": [{"item_id": "100005"}]}),
              (f"/review/100005/approve?batch_id={bid}", None)]
    for path, body in writes:
        denied = client.post(path, headers=other, json=body)
        assert denied.status_code == 404, (path, denied.text)


def test_draft_upload_cannot_take_over_another_account(client):
    draft = client.post("/batches", headers=TCB, json={"label": "Private draft"}).json()
    data = rows_to_csv([make_row(item_id="100005")])
    for headers, expected in ((EPOXY, 404), (TCB, 200)):
        response = client.post("/upload-bom-file", headers=headers,
                               files={"file": ("test.csv", data, "text/csv")},
                               data={"batch_id": draft["batch_id"], "label": "spoofed", "module_filter": "ALL"})
        assert response.status_code == expected, response.text
    saved = client.get("/batches", headers=TCB).json()[0]
    assert saved["uploaded_by"] == "tcb-1" and saved["label"] == "Private draft"


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
def test_chat_scope_and_latest_workspace_are_private(client, workspaces, endpoint):
    for body in ({"batch_id": workspaces[1]}, {"page_context": {"path": "/chat", "title": "Chat", "batch_id": workspaces[1]}}):
        denied = client.post(endpoint, headers=TCB, json={"question": "Summarize this workspace", **body})
        assert denied.status_code == 404, denied.text
    for actor, expected in ((TCB, workspaces[0]), (EPOXY, workspaces[1]),
                            ({"X-User": "new-user", "X-Role": "admin"}, None)):
        response = client.post(endpoint, headers=actor, json={"question": "Summarize this workspace"})
        assert response.status_code == 200, response.text
        result = response.json() if endpoint == "/chat" else json.loads(response.text.splitlines()[-1])
        assert result["batch_id"] == expected


def test_every_batch_tool_rejects_foreign_explicit_and_implicit_context(client, workspaces):
    conn = get_conn()
    try:
        for name, (spec, _) in REGISTRY.items():
            if "batch_id" not in spec.parameters.get("properties", {}):
                continue
            for selected, args in ((workspaces[0], {"batch_id": workspaces[1]}), (workspaces[1], {})):
                ctx = ToolContext(conn, {"user": "tcb-1", "role": "admin"}, selected, "Read the other workspace")
                result = json.loads(dispatch(ctx, name, args))
                assert result["response_status"] == "permission_denied", (name, result)
                assert not ctx.sources and not ctx.page_actions
    finally:
        conn.close()


def test_history_notes_pending_and_peers_stay_private(client, workspaces):
    conn = get_conn()
    try:
        foreign = workspaces[1]
        pending = conn.insert_returning(
            "INSERT INTO pending_change (batch_id,item_id,source_utterance,created_by) VALUES (?,?,?,?)",
            (foreign, "100005", "epoxy-1 private", "epoxy-1"), "pending_change")
        for author, bid in (("epoxy-1", foreign), ("tcb-1", workspaces[0])):
            conn.execute("INSERT INTO item_note (item_id,note,author,origin_batch_id) VALUES (?,?,?,?)",
                         ("100005", author + " private", author, bid))
        conn.commit()
        assert client.get("/pending-changes", headers=TCB).json()["pending"] == []
        assert len(client.get("/pending-changes", headers=EPOXY).json()["pending"]) == 1
        for suffix, kwargs in (("confirm-pending", {"json": {"pending_id": pending}}),
                               ("discard-pending", {"params": {"pending_id": pending}})):
            assert client.post(f"/review/100005/{suffix}", headers=TCB, **kwargs).status_code == 404
        history = client.get("/history/100005", headers=TCB).json()["reviews"]
        assert len(history) == 1 and history[0]["batch_id"] == workspaces[0]
        ctx = ToolContext(conn, {"user": "tcb-1", "role": "admin"}, workspaces[0], "private")
        for name, args in (("get_item_history", {"item_id": "100005"}),
                           ("get_agreement_history", {"item_id": "100005"}),
                           ("get_item_notes", {"item_id": "100005"}),
                           ("search_similar_reviews", {"query": "private"})):
            result = dispatch(ctx, name, args)
            assert "epoxy-1" not in result, (name, result)
            assert "error" not in json.loads(result), (name, result)
        result = json.loads(dispatch(ctx, "stage_review_action", {
            "kind": "confirm_pending", "item_id": "wrong-item", "pending_id": pending}))
        assert result["response_status"] == "permission_denied"
        from app.similarity import _load_pool
        pool = _load_pool(conn, {}, owner="tcb-1")
        assert {r["batch_id"] for r in pool} == {workspaces[0]}
    finally:
        conn.close()


def test_old_shared_advisory_caches_are_invalidated(client, workspaces):
    conn = get_conn()
    try:
        bid = workspaces[0]
        conn.execute("INSERT INTO assist_result (batch_id,item_id,stockroom_id,verdict,narrative,model_version) "
                     "VALUES (?,?,?,?,?,?)", (bid, "100005", "24", "needs_context", "foreign private evidence", "assist-v2"))
        conn.execute("INSERT INTO similarity_result (batch_id,item_id,stockroom_id,similarity_model_version) "
                     "VALUES (?,?,?,?)", (bid, "100005", "24", "knn-v1"))
        conn.commit()
        init_db()
        assert client.get(f"/assist/{bid}", headers=TCB).json()["items"] == []
        assert client.get(f"/similarity/{bid}", headers=TCB).json()["items"] == []
        assert conn.execute("SELECT COUNT(*) AS n FROM review_history").fetchone()["n"] == 2
    finally:
        conn.close()


def test_saved_conversations_do_not_replay_previously_shared_workspaces(client, workspaces):
    from app.agent.graph import _history
    conn = get_conn()
    try:
        for bid in workspaces:
            conn.execute("INSERT INTO conversation_turn (session_id,batch_id,user,question,answer) "
                         "VALUES (?,?,?,?,?)", ("old-shared-session", bid, "tcb-1", f"batch {bid}", "saved answer"))
        conn.commit()
        saved = client.get("/chat/sessions/old-shared-session", headers=TCB).json()
        assert [turn["batch_id"] for turn in saved["turns"]] == [workspaces[0]]
        assert client.get("/chat/sessions", headers=TCB).json()["sessions"][0]["turn_count"] == 1
        assert client.get("/chat/sessions/old-shared-session", headers=EPOXY).status_code == 404
        assert [turn["question"] for turn in _history(conn, "old-shared-session", "tcb-1")] == [f"batch {workspaces[0]}"]
    finally:
        conn.close()
