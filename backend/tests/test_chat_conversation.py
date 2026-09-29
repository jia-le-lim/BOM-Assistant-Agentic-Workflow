"""Social replies work without inference; factual and write requests keep their tools."""

import json

import pytest

from conftest import ENG, VIEWER, upload
from app.agent.graph import run_chat, synthesize
from app.agent.prompts import DONT_KNOW
from app.llm.provider import Response, ToolCall


@pytest.fixture()
def unavailable_model(monkeypatch):
    class Unavailable:
        name, model = "unavailable", "test"
        calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            raise RuntimeError("Model is unavailable")

    provider = Unavailable()
    monkeypatch.setattr("app.agent.graph.get_provider", lambda: provider)
    monkeypatch.setattr("app.agent.loop.get_provider", lambda: provider)
    monkeypatch.setattr("app.agent.suggestions.get_provider", lambda: provider)
    return provider


@pytest.mark.parametrize("question", [
    "hi", " HI!!! ", "hello NYRA", "hey there", "good morning",
    "thanks", "Thank you very much!", "okay", "got it", "bye",
    "How are you?", "are you there?", "help", "Can you help me?",
    "what can you do?", "who are you?", "how do I get started?",
])
def test_social_reply_without_workspace_or_model(question, unavailable_model):
    events = []
    result = run_chat(None, question, None, {"user": "alice", "role": "viewer"},
                      allow_writes=False, on_event=events.append)
    assert result["intent"] == "conversation"
    assert result["answer"] and result["answer"] != DONT_KNOW
    assert result["fallback"] is False
    assert result["response_status"] == "answered"
    assert result["sources"] == result["tool_calls"] == []
    assert result["model_calls"] == unavailable_model.calls == 0
    assert result["staged_action"] is None
    assert not any(event["type"] == "fallback" for event in events)
    complete = next(event for event in events if event["type"] == "agent_complete")
    assert complete["fallback"] is False
    assert "no record lookup" in complete["response_reason"]


@pytest.mark.parametrize("stream", [False, True])
def test_conversation_status_survives_stream_and_history(client, unavailable_model, stream):
    response = client.post("/chat/stream" if stream else "/chat", headers=ENG,
                           json={"question": "hi", "page_context": {"path": "/config", "title": "Settings"}})
    assert response.status_code == 200, response.text
    if stream:
        events = [json.loads(line) for line in response.text.splitlines()]
        assert not any(event["type"] in {"fallback", "prediction_start", "tool_start"}
                       for event in events)
        result = events[-1]
        assert result["type"] == "complete"
    else:
        result = response.json()
    assert result["intent"] == "conversation"
    assert result["answer"].startswith("Hi!")
    assert result["fallback"] is False
    assert result["response_status"] == "answered"
    assert unavailable_model.calls == 0
    saved = client.get(f"/chat/sessions/{result['session_id']}", headers=ENG).json()["turns"][0]
    assert saved["answer"] == result["answer"]
    assert saved["response_status"] == "answered"
    assert saved["fallback"] is False
    assert saved["response_reason"] == result["response_reason"]
    assert client.get(f"/chat/sessions/{result['session_id']}", headers=VIEWER).status_code == 404


@pytest.mark.parametrize(("question", "intent", "tool"), [
    ("Hi, current max for item 100001?", "lookup", "get_current_values"),
    ("thanks, show high risk items", "lookup", "list_review_queue"),
    ("hello, set item 100005 max to 3", "propose", "propose_change"),
])
def test_greeting_with_a_request_still_uses_tools(client, synth_csv, question, intent, tool):
    bid = upload(client, synth_csv).json()["batch_id"]
    assert client.post(f"/run-recommendation?batch_id={bid}", headers=ENG).status_code == 200
    result = client.post("/chat", headers=ENG,
                         json={"question": question, "batch_id": bid}).json()
    assert result["intent"] == intent
    assert tool in [call["name"] for call in result["tool_calls"]]
    assert result["sources"]
    assert result["answer"] != DONT_KNOW


def test_model_classified_conversation_uses_only_app_owned_help(monkeypatch):
    class Classifier:
        name, model = "classifier", "test"

        def chat(self, messages, tools):
            return Response(content="conversation", tool_calls=[
                ToolCall("bad", "propose_change", {"item_id": "100001", "proposed_max": 99})])

    monkeypatch.setattr("app.agent.graph.get_provider", lambda: Classifier())
    result = run_chat(None, "Please introduce yourself and the ways you can assist me",
                      None, {"user": "alice", "role": "engineer"})
    assert result["intent"] == "conversation"
    assert result["fallback"] is False
    assert "I can help you review your BOM" in result["answer"]
    assert result["tool_calls"] == result["sources"] == []
    assert result["staged_action"] is None
    assert "99" not in result["answer"]
    assert result["model_calls"] == 1


def test_conversation_label_cannot_bypass_factual_grounding():
    result = synthesize({"intent": "conversation", "response_status": "answered",
                         "sources": [], "answer": "Item 100001 should have Max 99."})
    assert result["answer"] == DONT_KNOW
