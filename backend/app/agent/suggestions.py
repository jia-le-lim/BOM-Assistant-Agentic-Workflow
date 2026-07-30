"""LLM-generated, read-only follow-up suggestions for a first chat turn."""

from __future__ import annotations

import json
import re

from ..llm import Message, Provider, ToolSpec, get_provider

MAX_SUGGESTIONS = 3
ITEM_ID_RE = re.compile(r"\b\d{6,}\b")
MUTATION_RE = re.compile(
    r"^\s*(?:set|change|update|override|approve|reject|confirm|discard|apply|record)\b|"
    r"\b(?:max|maximum|rop|min|minimum)\s*(?:to|=)\s*\d+",
    re.IGNORECASE,
)

SUGGESTION_TOOL = ToolSpec(
    name="suggest_next_steps",
    description=(
        "Return a concise interpretation of the user's intent and likely safe, "
        "read-only next questions they may want to ask."
    ),
    parameters={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {"type": "string", "maxLength": 160},
            "suggestions": {
                "type": "array",
                "minItems": 0,
                "maxItems": MAX_SUGGESTIONS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string", "maxLength": 70},
                        "prompt": {"type": "string", "maxLength": 300},
                    },
                    "required": ["label", "prompt"],
                },
            },
        },
        "required": ["intent", "suggestions"],
    },
)

SYSTEM = """You predict the next useful question in a BOM review conversation.
Interpret the user's first request and the grounded answer, then call
suggest_next_steps exactly once. Do not answer the questions and do not reveal
reasoning. Suggestions must be specific, concise user messages that follow from
the supplied context and use only the supplied read-only capabilities. Never
invent an item ID, stock quantity, decision, or fact. Never suggest changing,
approving, rejecting, confirming, recording, exporting, or uploading anything.
If no grounded next question is useful, return an empty suggestions array."""


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip()


def _payload(content: str, calls: list) -> dict:
    for call in calls:
        if call.name == SUGGESTION_TOOL.name and isinstance(call.arguments, dict):
            return call.arguments
    try:
        parsed = json.loads(content or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalise(payload: dict, question: str, answer: str) -> dict:
    intent = _text(payload.get("intent"), 160)
    raw = payload.get("suggestions")
    if not isinstance(raw, list):
        return {"intent": intent, "suggestions": []}

    known_items = set(ITEM_ID_RE.findall(question + " " + answer))
    original = " ".join(question.split()).casefold()
    seen: set[str] = set()
    suggestions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label"), 70)
        prompt = _text(item.get("prompt"), 300)
        key = prompt.casefold()
        referenced_items = set(ITEM_ID_RE.findall(prompt))
        if (not label or not prompt or key == original or key in seen
                or MUTATION_RE.search(prompt)
                or not referenced_items.issubset(known_items)):
            continue
        suggestions.append({"label": label, "prompt": prompt})
        seen.add(key)
        if len(suggestions) == MAX_SUGGESTIONS:
            break
    return {"intent": intent, "suggestions": suggestions}


def predict_next_steps(question: str, answer: str,
                       capabilities: list[ToolSpec],
                       provider: Provider | None = None) -> dict:
    llm = provider or get_provider()
    capability_context = [
        {"name": capability.name, "description": capability.description}
        for capability in capabilities
    ]
    response = llm.chat([
        Message(role="system", content=SYSTEM),
        Message(role="user", content=json.dumps({
            "first_question": question[:2000],
            "grounded_answer": answer[:6000],
            "read_only_capabilities": capability_context,
        }, ensure_ascii=False)),
    ], [SUGGESTION_TOOL])
    return _normalise(
        _payload(response.content, response.tool_calls), question, answer)
