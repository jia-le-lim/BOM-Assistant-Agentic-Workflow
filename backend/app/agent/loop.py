"""Bounded tool-calling loop.

Why this and not LangGraph
--------------------------
The deterministic workflow this agent fronts already exists and is already
deterministic: analysis/engine/engine.py (rule_version 0.2.0-tcb), which drops
its FORBIDDEN columns outright and is proven byte-identical across runs by
analysis/s8_verify.py. Wrapping that in an LLM graph would add nondeterminism
around the one component PRD section 10 requires to have none, and buys nothing
-- it is a single function call.

The human-in-the-loop state a graph checkpointer would manage also already
lives in SQL: review_history.requires_senior_approval / senior_approved_by, and
services.derive_status()'s four states. A checkpointer would make that two
sources of truth for approval state, which the audit requirement rules out.

So: one branch (call a tool or answer), depth capped, state in an explicit
AgentState. That shape is LangGraph-compatible if this ever grows into a real
multi-step planner -- the tool registry would not change, only the driver.

What changed
------------
agent/graph.py now sits in front of this loop and decides which SUBSET of tools
it is given, because the registry outgrew a single flat list -- 21 specs on
every model call is more than a routing layer can discriminate. It was the
driver that changed, exactly as the paragraph above predicted; run_agent's
signature already carried system_prompt and tool_names, so nothing here moved.

The rest of the argument stands and is not up for revisiting: that graph does
not wrap the engine or the assist chain, and it does not checkpoint.
Conversation state stays in `conversation_turn` and approval state stays in
`review_history`, for the reasons given above.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Callable

from ..llm import Message, get_provider
from ..redact import redact_for_prompt
from . import tools as T
from .prompts import DONT_KNOW, SYSTEM

MAX_TOOL_CALLS = 5
AgentEventSink = Callable[[dict], None]


@dataclass
class AgentState:
    question: str
    batch_id: int | None
    actor: dict
    session_id: str
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    calls_made: int = 0


def _emit(sink: AgentEventSink | None, event: dict) -> None:
    if sink is not None:
        sink(event)


def _tool_outcome(result: str) -> tuple[str, str]:
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return "error", "The tool returned an unreadable response."
    if not isinstance(payload, dict):
        return "ok", "Retrieved a result."
    if payload.get("error"):
        return "error", str(payload["error"])
    if payload.get("_empty"):
        return "empty", "No matching records were found."
    for key in ("items", "reviews", "notes", "results"):
        if isinstance(payload.get(key), list):
            count = len(payload[key])
            return "ok", f"Retrieved {count} matching record{'s' if count != 1 else ''}."
    return "ok", "Retrieved a matching record."


def run_agent(conn, question: str, batch_id: int | None, actor: dict,
              session_id: str | None = None,
              allow_writes: bool = True,
              on_event: AgentEventSink | None = None,
              system_prompt: str = SYSTEM,
              tool_names: Collection[str] | None = None,
              max_model_calls: int | None = None) -> dict:
    provider = get_provider()
    state = AgentState(question=question, batch_id=batch_id, actor=actor,
                       session_id=session_id or str(uuid.uuid4()))
    ctx = T.ToolContext(conn=conn, actor={**actor,
                                          "_parsed_by": f"{provider.name}:{provider.model}"},
                        batch_id=batch_id, question=question)

    state.messages = [Message(role="system", content=system_prompt),
                      Message(role="user", content=question)]
    specs = T.specs(allow_writes=allow_writes, names=tool_names)
    answer = ""
    model_attempt = 0

    _emit(on_event, {
        "type": "request",
        "query": question,
        "batch_id": batch_id,
        "provider": provider.name,
        "model": provider.model,
    })

    while (state.calls_made <= MAX_TOOL_CALLS
           and (max_model_calls is None or model_attempt < max_model_calls)):
        # Past the cap, drop the tools so the model has to answer with what it
        # already has rather than looping.
        offered = specs if state.calls_made < MAX_TOOL_CALLS else []
        offered_names = {spec.name for spec in offered}
        model_attempt += 1
        phase = ("Selecting the right data source"
                 if state.calls_made == 0
                 else "Composing an answer from retrieved records")
        _emit(on_event, {
            "type": "model_start",
            "attempt": model_attempt,
            "phase": phase,
            "provider": provider.name,
            "model": provider.model,
        })
        resp = provider.chat(state.messages, offered)

        if not resp.tool_calls:
            answer = (resp.content or "").strip()
            _emit(on_event, {
                "type": "model_complete",
                "attempt": model_attempt,
                "summary": "Response ready",
            })
            break

        _emit(on_event, {
            "type": "model_complete",
            "attempt": model_attempt,
            "summary": f"Requested {len(resp.tool_calls)} data "
                       f"source{'s' if len(resp.tool_calls) != 1 else ''}",
        })

        state.messages.append(Message(role="assistant", content=resp.content,
                                      tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            state.calls_made += 1
            args = redact_for_prompt(call.arguments) or {}
            _emit(on_event, {
                "type": "tool_start",
                "sequence": state.calls_made,
                "name": call.name,
                "args": args,
            })
            result = (T.dispatch(ctx, call.name, call.arguments)
                      if call.name in offered_names else
                      json.dumps({"error": f"tool {call.name} was not offered"}))
            status, summary = _tool_outcome(result)
            _emit(on_event, {
                "type": "tool_result",
                "sequence": state.calls_made,
                "name": call.name,
                "status": status,
                "summary": summary,
            })
            state.tool_calls.append({
                "name": call.name,
                "args": args,
                "ok": '"error"' not in result[:40],
            })
            state.messages.append(Message(role="tool", content=result,
                                          tool_call_id=call.id,
                                          name=call.name))

    # PRD section 8 hard control: no retrieved source -> say so. The model does
    # not get to decide this; an empty tool result set means "I don't know"
    # regardless of what it wanted to say.
    used_fallback = not ctx.sources or not answer
    if used_fallback:
        reason = ("No authoritative data source matched this query."
                  if not ctx.sources
                  else "The model returned no usable answer from the retrieved records.")
        _emit(on_event, {"type": "fallback", "reason": reason})
        answer = answer if (answer and ctx.sources) else DONT_KNOW

    _emit(on_event, {
        "type": "agent_complete",
        "source_count": len(ctx.sources),
        "tool_count": len(state.tool_calls),
        "fallback": used_fallback,
    })

    return {
        "answer": answer,
        "sources": ctx.sources,
        "batch_id": batch_id,
        "session_id": state.session_id,
        "tool_calls": state.tool_calls,
        "provider": provider.name,
        "model": provider.model,
        "model_calls": model_attempt,
    }


def log_turn(conn, state_result: dict, actor: dict, question: str) -> int:
    """Persist the verbatim turn. This is the ML label corpus
    (Feature_Selection_TCB_Jan26.md section 12.6) as much as it is an audit
    record, so the question and answer are stored unmodified."""
    return conn.insert_returning(
        "INSERT INTO conversation_turn (session_id, batch_id, user, role, "
        "question, answer, tool_calls, provider, model) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (state_result["session_id"], state_result.get("batch_id"),
         actor.get("user"), actor.get("role"), question,
         state_result["answer"], json.dumps(state_result["tool_calls"]),
         state_result["provider"], state_result["model"]),
        "conversation_turn")
