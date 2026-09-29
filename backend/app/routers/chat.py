"""POST /chat -- NYRA entrypoint (PRD section 8).

The endpoint's guarantees, all enforced structurally rather than by prompt:

  * the agent's only write target is `pending_change`; it has no path to
    `review_history`, so nothing it does can reach a WINGS export file
  * it never generates Min/Max/ROP -- `propose_change` rejects any number the
    engineer did not write themselves
  * with no retrieved source it answers "I don't know" rather than inventing
  * every turn is logged verbatim to `conversation_turn` and to `audit_log`

The verbatim log is not only for audit. Feature_Selection_TCB_Jan26.md section
12.6 calls for mining engineer free text as ML *labels* (as features they leak:
OOF AUC 0.987 for comments, 0.923 for justification). Chat capture is what
accumulates the multi-month, multi-reviewer labels the Jan'26 slice lacks --
one month, 682 positives, 97.5% from a single reviewer.
"""

import json
from queue import Queue
from threading import Thread

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..agent import tools as agent_tools
from ..agent.graph import run_chat
from ..agent.loop import log_turn
from ..agent.suggestions import predict_next_steps
from ..audit import audit
from ..db import get_conn
from ..schemas import ChatRequest
from ..security import REVIEW_ROLES, any_role, can_read_all_workspaces, require_workspace

router = APIRouter()


def _stored_tool_calls(raw: str | None) -> list[dict]:
    try:
        calls = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _latest_scored_batch(conn, actor: dict) -> int | None:
    r = conn.execute(
        "SELECT batch_id FROM batches WHERE status='scored' AND (uploaded_by=? OR ?=1) "
        "ORDER BY batch_id DESC LIMIT 1", (actor["user"], int(can_read_all_workspaces(actor)))).fetchone()
    return r["batch_id"] if r else None


def _resolve_batch(conn, requested: int | None, actor: dict, page_context=None) -> int | None:
    if page_context is not None:
        if requested is not None and requested != page_context.batch_id:
            raise HTTPException(422, "batch_id does not match the current page")
        batch_id = page_context.batch_id
        if batch_id is None and page_context.path == "/chat":
            batch_id = _latest_scored_batch(conn, actor)
    else:
        batch_id = requested if requested is not None else _latest_scored_batch(conn, actor)
    if batch_id is not None:
        require_workspace(conn, batch_id, actor, allow_shared=True)
    return batch_id


def _allow_workspace_writes(conn, batch_id: int | None, actor: dict) -> bool:
    if actor.get("role") not in REVIEW_ROLES:
        return False
    return batch_id is None or require_workspace(
        conn, batch_id, actor, allow_shared=True)["uploaded_by"] == actor["user"]


def _record_turn(conn, result: dict, actor: dict, question: str,
                 batch_id: int | None, endpoint: str = "/chat") -> int:
    turn_id = log_turn(conn, result, actor, question)
    audit(conn, actor, "POST", endpoint, "chat", batch_id or "-",
          {"question": question[:500],
           "answered": bool(result["sources"]),
           "tools": [c["name"] for c in result["tool_calls"]],
           "intent": result.get("intent"),
           "staged_action": bool(result.get("staged_action")),
           "turn_id": turn_id})
    conn.commit()
    return turn_id


def _is_first_turn(conn, session_id: str | None, user: str | None) -> bool:
    if not session_id:
        return True
    return conn.execute(
        "SELECT 1 FROM conversation_turn WHERE session_id=? AND user=? LIMIT 1",
        (session_id, user),
    ).fetchone() is None


def _predict_next_steps(question: str, answer: str) -> dict:
    # allow_writes=False drops propose_change but NOT the gated tools -- a
    # suggestion chip reading "run assist on batch 7" would spend a model call
    # per row on one click. The prompt forbids suggesting an action; this is
    # what enforces it.
    safe = (agent_tools.INTENT_TOOLS["lookup"]
            | agent_tools.INTENT_TOOLS["advisory"])
    return predict_next_steps(
        question,
        answer,
        agent_tools.specs(allow_writes=False, names=safe),
    )


@router.post("/chat")
def chat(body: ChatRequest, actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        batch_id = _resolve_batch(conn, body.batch_id, actor, body.page_context)
        first_turn = _is_first_turn(conn, body.session_id, actor.get("user"))
        # Staging a proposal is a review action. Viewers and auditors get the
        # read-only tool surface, so the write tool is not even offered to the
        # model for them.
        allow_writes = _allow_workspace_writes(conn, batch_id, actor)

        result = run_chat(conn, question=body.question.strip(),
                          batch_id=batch_id, actor=actor,
                          session_id=body.session_id,
                          allow_writes=allow_writes,
                          page_context=body.page_context.model_dump() if body.page_context else None)

        turn_id = _record_turn(conn, result, actor, body.question.strip(), batch_id)

        next_steps = None
        if first_turn:
            try:
                next_steps = _predict_next_steps(body.question.strip(), result["answer"])
            except Exception:
                next_steps = None

        return {"answer": result["answer"],
                "sources": result["sources"],
                "batch_id": batch_id,
                "session_id": result["session_id"],
                "turn_id": turn_id,
                "intent": result["intent"],
                "staged_action": result.get("staged_action"),
                "page_actions": result.get("page_actions", []),
                "next_steps": next_steps}
    finally:
        conn.close()


@router.get("/chat/sessions")
def list_chat_sessions(actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT session_id, question AS title, updated_at, turn_count "
            "FROM (SELECT session_id, question, turn_id, "
            "MAX(ts) OVER (PARTITION BY session_id) AS updated_at, "
            "COUNT(*) OVER (PARTITION BY session_id) AS turn_count, "
            "ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY turn_id) AS position "
            "FROM conversation_turn WHERE user=? AND session_id IS NOT NULL "
            "AND (batch_id IS NULL OR batch_id IN "
            "(SELECT batch_id FROM batches WHERE uploaded_by=? OR ?=1))) saved "
            "WHERE position=1 ORDER BY updated_at DESC, turn_id DESC LIMIT 40",
            (actor["user"], actor["user"], int(can_read_all_workspaces(actor))),
        ).fetchall()
        return {"sessions": [dict(row) for row in rows]}
    finally:
        conn.close()


@router.get("/chat/sessions/{session_id}")
def get_chat_session(session_id: str, actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT turn_id, session_id, batch_id, question, answer, tool_calls, "
            "provider, model, ts FROM conversation_turn "
            "WHERE session_id=? AND user=? AND (batch_id IS NULL OR batch_id IN "
            "(SELECT batch_id FROM batches WHERE uploaded_by=? OR ?=1)) ORDER BY turn_id",
            (session_id, actor["user"], actor["user"], int(can_read_all_workspaces(actor))),
        ).fetchall()
        if not rows:
            raise HTTPException(404, "conversation not found")
        turns = []
        for row in rows:
            turn = dict(row)
            turn["tool_calls"] = _stored_tool_calls(turn.get("tool_calls"))
            turns.append(turn)
        return {"session_id": session_id, "turns": turns}
    finally:
        conn.close()


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, actor: dict = Depends(any_role())):
    # Deny foreign IDs before opening a stream or calling the model.
    conn = get_conn()
    try:
        _resolve_batch(conn, body.batch_id, actor, body.page_context)
    finally:
        conn.close()
    question = body.question.strip()
    events: Queue[dict | None] = Queue()

    def emit(event: dict) -> None:
        events.put(event)

    def worker() -> None:
        conn = None
        try:
            conn = get_conn()
            batch_id = _resolve_batch(conn, body.batch_id, actor, body.page_context)
            first_turn = _is_first_turn(conn, body.session_id, actor.get("user"))
            allow_writes = _allow_workspace_writes(conn, batch_id, actor)
            result = run_chat(
                conn, question=question, batch_id=batch_id, actor=actor,
                session_id=body.session_id, allow_writes=allow_writes,
                on_event=emit,
                page_context=body.page_context.model_dump() if body.page_context else None,
            )
            turn_id = _record_turn(
                conn, result, actor, question, batch_id, "/chat/stream")
            emit({"type": "answer_start"})
            answer = result["answer"]
            for offset in range(0, len(answer), 56):
                emit({"type": "answer_delta",
                      "delta": answer[offset:offset + 56]})
            next_steps = None
            if first_turn:
                emit({
                    "type": "prediction_start",
                    "provider": result["provider"],
                    "model": result["model"],
                })
                try:
                    next_steps = _predict_next_steps(question, answer)
                    emit({"type": "prediction_complete", **next_steps})
                except Exception as prediction_exc:
                    emit({
                        "type": "prediction_error",
                        "message": (str(prediction_exc)
                                    or type(prediction_exc).__name__)[:300],
                    })
            emit({
                "type": "complete",
                "answer": answer,
                "sources": result["sources"],
                "batch_id": batch_id,
                "session_id": result["session_id"],
                "turn_id": turn_id,
                "provider": result["provider"],
                "model": result["model"],
                "tool_calls": result["tool_calls"],
                "intent": result["intent"],
                "staged_action": result.get("staged_action"),
                "page_actions": result.get("page_actions", []),
                "next_steps": next_steps,
            })
        except Exception as exc:
            if conn is not None:
                conn.rollback()
            message = (str(exc.detail) if isinstance(exc, HTTPException)
                       else str(exc) or type(exc).__name__)
            emit({
                "type": "error",
                "stage": "request" if isinstance(exc, HTTPException) else "agent",
                "status": exc.status_code if isinstance(exc, HTTPException) else 500,
                "error_type": type(exc).__name__,
                "message": message[:500],
            })
        finally:
            if conn is not None:
                conn.close()
            events.put(None)

    Thread(target=worker, daemon=True).start()

    def generate():
        while True:
            event = events.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform",
                 "X-Accel-Buffering": "no"},
    )


@router.get("/pending-changes")
def list_pending(batch_id: int | None = None, status: str = "pending",
                 actor: dict = Depends(any_role())):
    """The confirmation tray: what the agent staged, awaiting a human."""
    conn = get_conn()
    try:
        # The unscoped confirmation tray stays owned. An explicit shared
        # workspace may show its proposals, with action controls disabled.
        shared = batch_id is not None and can_read_all_workspaces(actor)
        where = ["status=?", "batch_id IN (SELECT batch_id FROM batches WHERE uploaded_by=? OR ?=1)"]
        params = [status, actor["user"], int(shared)]
        read_only = False
        if batch_id is not None:
            workspace = require_workspace(conn, batch_id, actor, allow_shared=True)
            read_only = workspace["uploaded_by"] != actor["user"]
            where.append("batch_id=?"); params.append(batch_id)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM pending_change WHERE " + " AND ".join(where)
            + " ORDER BY pending_id DESC LIMIT 100", params)]
        return {"pending": rows, "count": len(rows), "read_only": read_only}
    finally:
        conn.close()
