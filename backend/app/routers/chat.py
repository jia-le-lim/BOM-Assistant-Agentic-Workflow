"""POST /chat -- NYRA entrypoint (PRD section 8), scaffolded WITHOUT an LLM.

The PRD's hard controls are enforced by construction here:
  * this endpoint only ever reads (SELECTs + audit insert)
  * it never generates Min/Max/ROP values -- it retrieves what the engine stored
  * when no tool matches, it answers "I don't know" rather than inventing

The real NYRA/RAG layer will replace intent parsing with an LLM, but must keep
this same read-only tool surface. Flagged in Backend_Scaffold_Notes.md.
"""

import re

from fastapi import APIRouter, Depends

from ..audit import audit
from ..db import get_conn
from ..schemas import ChatRequest
from ..security import any_role

router = APIRouter()

ITEM_RE = re.compile(r"\b(\d{6,})\b")


def _latest_scored_batch(conn) -> int | None:
    r = conn.execute(
        "SELECT batch_id FROM batches WHERE status='scored' "
        "ORDER BY batch_id DESC LIMIT 1").fetchone()
    return r["batch_id"] if r else None


@router.post("/chat")
def chat(body: ChatRequest, actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        q = body.question.strip()
        ql = q.lower()
        batch_id = body.batch_id or _latest_scored_batch(conn)
        item_match = ITEM_RE.search(q)
        answer, sources = None, []

        if item_match and any(w in ql for w in ("why", "explain", "reason")):
            item_id = item_match.group(1)
            r = conn.execute(
                "SELECT * FROM recommendation_result WHERE batch_id=? AND item_id=?",
                (batch_id, item_id)).fetchone()
            if r:
                answer = (f"Item {item_id}: {r['explanation']} "
                          f"(action: {r['action']}, review required: {r['review_required']}, "
                          f"risk: {r['risk_level']}, confidence: {r['confidence']:.2f})")
                sources = [{"type": "recommendation_result", "batch_id": batch_id,
                            "item_id": item_id, "reason_code": r["reason_code"],
                            "rule_version": r["rule_version"]}]

        elif item_match and "history" in ql:
            item_id = item_match.group(1)
            rows = conn.execute(
                "SELECT reviewer, decision, final_max, final_rop, final_min, reviewed_at "
                "FROM review_history WHERE item_id=? ORDER BY review_id DESC LIMIT 5",
                (item_id,)).fetchall()
            if rows:
                lines = [f"{r['reviewed_at']}: {r['reviewer']} {r['decision']} -> "
                         f"max {r['final_max']}/rop {r['final_rop']}/min {r['final_min']}"
                         for r in rows]
                answer = f"Review history for {item_id}: " + "; ".join(lines)
                sources = [{"type": "review_history", "item_id": item_id, "count": len(rows)}]

        elif "top" in ql and any(w in ql for w in ("risk", "exposure", "value")):
            rows = conn.execute(
                "SELECT item_id, exposure_usd, risk_level, reason_code "
                "FROM recommendation_result WHERE batch_id=? AND review_required='Y' "
                "ORDER BY exposure_usd DESC LIMIT 5", (batch_id,)).fetchall()
            if rows:
                lines = [f"{r['item_id']} (${r['exposure_usd']:,.0f}, {r['risk_level']})"
                         for r in rows]
                answer = "Top review items by exposure: " + "; ".join(lines)
                sources = [{"type": "recommendation_result", "batch_id": batch_id}]

        if answer is None:
            # PRD section 8 hard control: no source -> say so. Never invent.
            answer = ("I don't know — no data source matches that question. "
                      "I can explain a recommendation ('why item <id>'), show review "
                      "history ('history <id>'), or list top review items by exposure. "
                      "(NYRA/RAG integration pending; this endpoint is read-only by design.)")

        audit(conn, actor, "POST", "/chat", "chat", batch_id or "-",
              {"question": q[:500], "answered": bool(sources)})
        conn.commit()
        return {"answer": answer, "sources": sources, "batch_id": batch_id}
    finally:
        conn.close()
