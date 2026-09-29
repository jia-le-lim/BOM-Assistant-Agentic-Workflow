"""The @ workspace picker uses the same explicit context on both chat paths."""

import json

import pytest

from conftest import ENG, upload


def scoped_chat(client, batch_id, *, stream, session_id=None, question="top exposure items"):
    response = client.post("/chat/stream" if stream else "/chat", headers=ENG, json={
        "question": question,
        "batch_id": batch_id,
        "session_id": session_id,
        "page_context": {"path": "/chat", "title": "Ask NYRA · Selected workspace", "batch_id": batch_id},
    })
    assert response.status_code == 200, response.text
    if stream:
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "complete", events[-1]
        return events[-1]
    return response.json()


@pytest.mark.parametrize("stream", [False, True])
def test_selected_workspace_overrides_latest_and_survives_followups(client, synth_csv, stream):
    first = upload(client, synth_csv, label="January review").json()["batch_id"]
    second = upload(client, synth_csv, label="February review").json()["batch_id"]
    for batch_id in (first, second):
        assert client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG).status_code == 200

    result = scoped_chat(client, first, stream=stream)
    followup = scoped_chat(client, first, stream=stream, session_id=result["session_id"])
    switched = scoped_chat(client, second, stream=stream, session_id=result["session_id"])
    for answer, expected in ((result, first), (followup, first), (switched, second)):
        assert answer["batch_id"] == expected
        assert answer["sources"]
        assert {source["batch_id"] for source in answer["sources"] if source.get("batch_id")} == {expected}

    saved = client.get(f"/chat/sessions/{result['session_id']}", headers=ENG).json()
    assert [turn["batch_id"] for turn in saved["turns"]] == [first, first, second]
    assert all(turn["question"] == "top exposure items" for turn in saved["turns"])


@pytest.mark.parametrize("stream", [False, True])
def test_draft_workspace_does_not_fall_back_to_scored_workspace(client, synth_csv, stream):
    scored = upload(client, synth_csv, label="Scored workspace").json()["batch_id"]
    assert client.post(f"/run-recommendation?batch_id={scored}", headers=ENG).status_code == 200
    draft = client.post("/batches", headers=ENG, json={"label": "New review"}).json()["batch_id"]
    result = scoped_chat(client, draft, stream=stream)
    assert result["batch_id"] == draft
    assert all(source.get("batch_id") != scored for source in result["sources"])
