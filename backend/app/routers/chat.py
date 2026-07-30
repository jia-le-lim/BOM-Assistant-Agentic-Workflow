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

from ..agent.loop import log_turn, run_agent
from ..audit import audit
from ..db import get_conn
from ..schemas import ChatRequest
from ..security import REVIEW_ROLES, any_role

router = APIRouter()


def _latest_scored_batch(conn) -> int | None:
    r = conn.execute(
        "SELECT batch_id FROM batches WHERE status='scored' "
        "ORDER BY batch_id DESC LIMIT 1").fetchone()
    return r["batch_id"] if r else None


def _resolve_batch(conn, requested: int | None) -> int | None:
    batch_id = requested or _latest_scored_batch(conn)
    if batch_id is not None and conn.execute(
            "SELECT batch_id FROM batches WHERE batch_id=?",
            (batch_id,)).fetchone() is None:
        raise HTTPException(404, f"no batch {batch_id}")
    return batch_id


def _record_turn(conn, result: dict, actor: dict, question: str,
                 batch_id: int | None, endpoint: str = "/chat") -> int:
    turn_id = log_turn(conn, result, actor, question)
    audit(conn, actor, "POST", endpoint, "chat", batch_id or "-",
          {"question": question[:500],
           "answered": bool(result["sources"]),
           "tools": [c["name"] for c in result["tool_calls"]],
           "turn_id": turn_id})
    conn.commit()
    return turn_id


@router.post("/chat")
def chat(body: ChatRequest, actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        batch_id = _resolve_batch(conn, body.batch_id)
        # Staging a proposal is a review action. Viewers and auditors get the
        # read-only tool surface, so the write tool is not even offered to the
        # model for them.
        allow_writes = actor.get("role") in REVIEW_ROLES

        result = run_agent(conn, question=body.question.strip(),
                           batch_id=batch_id, actor=actor,
                           session_id=body.session_id,
                           allow_writes=allow_writes)

        turn_id = _record_turn(conn, result, actor, body.question.strip(), batch_id)

        return {"answer": result["answer"],
                "sources": result["sources"],
                "batch_id": batch_id,
                "session_id": result["session_id"],
                "turn_id": turn_id}
    finally:
        conn.close()


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, actor: dict = Depends(any_role())):
    question = body.question.strip()
    events: Queue[dict | None] = Queue()

    def emit(event: dict) -> None:
        events.put(event)

    def worker() -> None:
        conn = None
        try:
            conn = get_conn()
            batch_id = _resolve_batch(conn, body.batch_id)
            allow_writes = actor.get("role") in REVIEW_ROLES
            result = run_agent(
                conn, question=question, batch_id=batch_id, actor=actor,
                session_id=body.session_id, allow_writes=allow_writes,
                on_event=emit,
            )
            turn_id = _record_turn(
                conn, result, actor, question, batch_id, "/chat/stream")
            emit({"type": "answer_start"})
            answer = result["answer"]
            for offset in range(0, len(answer), 56):
                emit({"type": "answer_delta",
                      "delta": answer[offset:offset + 56]})
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
        where, params = ["status=?"], [status]
        if batch_id is not None:
            where.append("batch_id=?"); params.append(batch_id)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM pending_change WHERE " + " AND ".join(where)
            + " ORDER BY pending_id DESC LIMIT 100", params)]
        return {"pending": rows, "count": len(rows)}
    finally:
        conn.close()
