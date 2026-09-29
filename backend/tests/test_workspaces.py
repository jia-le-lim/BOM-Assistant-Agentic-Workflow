"""Workspace lifecycle: persistent drafts, cycle isolation, and upload recovery."""
import json
import pytest

from conftest import ENG, OWNER_VIEWER as VIEWER


def create(client, label="September 2026 TCB"):
    response = client.post("/batches", json={"label": label, "module_filter": "TCB"}, headers=ENG)
    assert response.status_code == 201, response.text
    return response.json()


def attach(client, batch_id, content):
    return client.post("/upload-bom-file", headers=ENG,
                       files={"file": ("review.csv", content, "text/csv")},
                       data={"batch_id": batch_id, "label": "must not rename", "module_filter": "ALL"})


def test_create_without_datasheet_persists_and_cannot_score(client):
    workspace = create(client, "  September review  ")
    assert workspace["label"] == "September review"
    assert workspace["status"] == "draft"
    assert workspace["row_count"] == 0
    assert workspace["source_filename"] is None
    assert workspace in client.get("/batches", headers=VIEWER).json()
    assert client.post("/run-recommendation", params={"batch_id": workspace["batch_id"]}, headers=ENG).status_code == 409


def test_upload_stays_in_its_workspace_and_does_not_replace_a_cycle(client, synth_csv):
    first, second = create(client), create(client, "October review")
    result = attach(client, first["batch_id"], synth_csv)
    assert result.status_code == 200, result.text
    assert result.json()["batch_id"] == first["batch_id"]
    assert result.json()["label"] == first["label"]
    assert result.json()["rows_loaded"] == 10  # the saved TCB scope wins
    workspaces = {w["batch_id"]: w for w in client.get("/batches", headers=VIEWER).json()}
    assert len(workspaces) == 2
    assert workspaces[first["batch_id"]]["status"] == "loaded"
    assert workspaces[second["batch_id"]] == second
    assert attach(client, first["batch_id"], synth_csv).status_code == 409
    assert client.post("/run-recommendation", params={"batch_id": first["batch_id"]}, headers=ENG).status_code == 200
    assert attach(client, first["batch_id"], synth_csv).status_code == 409


def test_invalid_datasheet_leaves_workspace_ready_for_retry(client, synth_csv):
    workspace = create(client)
    assert attach(client, workspace["batch_id"], b"bad,columns\n1,2").status_code == 400
    assert client.get("/batches", headers=VIEWER).json() == [workspace]
    assert attach(client, workspace["batch_id"], synth_csv).status_code == 200
    assert attach(client, 99999, synth_csv).status_code == 404


@pytest.mark.parametrize("label", ["", "   ", "x" * 121])
def test_workspace_name_validation(client, label):
    assert client.post("/batches", json={"label": label}, headers=ENG).status_code == 422


def test_workspace_permissions_and_scope_validation(client):
    assert client.post("/batches", json={"label": "No access"}, headers=VIEWER).status_code == 403
    assert client.post("/batches", json={"label": "Bad module", "module_filter": "UNKNOWN"}, headers=ENG).status_code == 422


def test_rest_upload_claim_and_rows_share_one_transaction(synth_csv):
    from app.ingestion import ingest, IngestionConflict
    from app.rest_conn import RestConn

    conn = RestConn("https://example.invalid", "test-key")
    calls = []
    def capture(sql, params, want_rows):
        calls.append((sql, params, want_rows))
        return [{"batch_id": 42}]
    conn._rpc = capture
    try:
        result = ingest(conn, synth_csv, "Cycle", "review.csv", "TCB", "alice", batch_id=42)
        assert result["batch_id"] == 42
        assert len(calls) == 1  # claim + all rows must not split into HTTP commits
        sql, params, want_rows = calls[0]
        assert "status='draft'" in sql and "INSERT INTO bom_rows" in sql
        assert "$6" in sql and want_rows
        records = json.loads(params[-1])
        assert len(records) == 10
        assert sum(row["quarantined"] for row in records) == 3
        conn._rpc = lambda *_: []  # a competing upload claimed the draft
        with pytest.raises(IngestionConflict):
            ingest(conn, synth_csv, "Cycle", "review.csv", "TCB", "alice", batch_id=42)
    finally:
        conn.close()
