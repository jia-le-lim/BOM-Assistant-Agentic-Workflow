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
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from ..llm import Message, get_provider
from ..redact import redact_for_prompt
from . import tools as T
from .prompts import DONT_KNOW, SYSTEM

MAX_TOOL_CALLS = 5


@dataclass
class AgentState:
    question: str
    batch_id: int | None
    actor: dict
    session_id: str
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    calls_made: int = 0


def run_agent(conn, question: str, batch_id: int | None, actor: dict,
              session_id: str | None = None,
              allow_writes: bool = True) -> dict:
    provider = get_provider()
    state = AgentState(question=question, batch_id=batch_id, actor=actor,
                       session_id=session_id or str(uuid.uuid4()))
    ctx = T.ToolContext(conn=conn, actor={**actor,
                                          "_parsed_by": f"{provider.name}:{provider.model}"},
                        batch_id=batch_id, question=question)

    state.messages = [Message(role="system", content=SYSTEM),
                      Message(role="user", content=question)]
    specs = T.specs(allow_writes=allow_writes)
    answer = ""

    while state.calls_made <= MAX_TOOL_CALLS:
        # Past the cap, drop the tools so the model has to answer with what it
        # already has rather than looping.
        offered = specs if state.calls_made < MAX_TOOL_CALLS else []
        resp = provider.chat(state.messages, offered)

        if not resp.tool_calls:
            answer = (resp.content or "").strip()
            break

        state.messages.append(Message(role="assistant", content=resp.content,
                                      tool_calls=resp.tool_calls))
        for call in resp.tool_calls:
            state.calls_made += 1
            args = redact_for_prompt(call.arguments) or {}
            result = T.dispatch(ctx, call.name, call.arguments)
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
    if not ctx.sources or not answer:
        answer = answer if (answer and ctx.sources) else DONT_KNOW

    return {
        "answer": answer,
        "sources": ctx.sources,
        "batch_id": batch_id,
        "session_id": state.session_id,
        "tool_calls": state.tool_calls,
        "provider": provider.name,
        "model": provider.model,
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
