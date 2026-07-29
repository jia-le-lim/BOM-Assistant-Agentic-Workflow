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

from fastapi import APIRouter, Depends

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


@router.post("/chat")
def chat(body: ChatRequest, actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        batch_id = body.batch_id or _latest_scored_batch(conn)
        # Staging a proposal is a review action. Viewers and auditors get the
        # read-only tool surface, so the write tool is not even offered to the
        # model for them.
        allow_writes = actor.get("role") in REVIEW_ROLES

        result = run_agent(conn, question=body.question.strip(),
                           batch_id=batch_id, actor=actor,
                           session_id=body.session_id,
                           allow_writes=allow_writes)

        turn_id = log_turn(conn, result, actor, body.question.strip())
        audit(conn, actor, "POST", "/chat", "chat", batch_id or "-",
              {"question": body.question[:500],
               "answered": bool(result["sources"]),
               "tools": [c["name"] for c in result["tool_calls"]],
               "turn_id": turn_id})
        conn.commit()

        return {"answer": result["answer"],
                "sources": result["sources"],
                "batch_id": batch_id,
                "session_id": result["session_id"],
                "turn_id": turn_id}
    finally:
        conn.close()


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
