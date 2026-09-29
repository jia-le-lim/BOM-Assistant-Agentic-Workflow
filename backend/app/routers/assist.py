"""Review assistance endpoints -- a verdict per live row, and the rows back.

Advisory only, on the same terms as triage and similarity: this never changes
Min/ROP/Max and never records a decision. The verdict says which rows deserve
attention first; a human still presses every button.
"""

import json

from fastapi import APIRouter, Depends, HTTPException

from ..assist import rules
from ..assist.chain import run_batch
from ..audit import audit
from ..db import active_config, get_conn
from ..security import REVIEW_ROLES, any_role, require_role, require_workspace
from ..similarity import run_similarity

router = APIRouter()


@router.post("/assist/run")
def assist_batch(batch_id: int, refresh_peers: bool = False,
                 actor: dict = Depends(require_role(*REVIEW_ROLES))):
    """Assist the active/dying rows of a scored batch.

    Peer similarity runs first when this batch has none: `peers` is one of the
    chain's five evidence sources (assist/chain.py EVIDENCE_KEYS) and reads
    similarity_result, so assisting without it silently drops a source and
    changes the verdict. `refresh_peers` rebuilds peers that already exist.

    Dormant rows are Layer 1's problem -- an engineer's rule sizes those, and
    running them through here would spend a model call to say so. A batch with
    no live rows is a clean no-op, not an error.
    """
    conn = get_conn()
    try:
        require_workspace(conn, batch_id, actor)
        has_peers = conn.execute(
            "SELECT 1 FROM similarity_result WHERE batch_id=? LIMIT 1",
            (batch_id,)).fetchone() is not None
        similarity = None
        if refresh_peers or not has_peers:
            try:
                similarity = run_similarity(conn, batch_id, refresh_peers)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            audit(conn, actor, "POST", "/assist/run", "similarity",
                  batch_id, similarity)
        summary = run_batch(conn, batch_id, actor, active_config(conn, actor["user"]))
        summary["similarity"] = similarity
        audit(conn, actor, "POST", "/assist/run", "assist", batch_id, summary)
        conn.commit()
        return summary
    finally:
        conn.close()


@router.get("/assist/{batch_id}")
def get_assist(batch_id: int, verdict: str | None = None,
               item_id: str | None = None, stockroom_id: str | None = None,
               actor: dict = Depends(any_role())):
    """Every assisted row: verdict, why, and the sentence explaining it.

    `item_id` narrows it to one part, which is how the item page reads its own
    verdict without pulling a 2.8k-row batch to render one card.
    """
    if verdict is not None and verdict not in rules.VERDICTS:
        raise HTTPException(400, f"verdict must be one of {list(rules.VERDICTS)}")
    conn = get_conn()
    try:
        require_workspace(conn, batch_id, actor, allow_shared=True)
        sql = ("SELECT batch_id, item_id, stockroom_id, verdict, reasons_json, "
               "narrative, suggested_max, suggested_rop, suggestion_basis, "
               "model_version, assisted_at FROM assist_result WHERE batch_id=?")
        params: list = [batch_id]
        if verdict:
            sql += " AND verdict=?"
            params.append(verdict)
        if item_id:
            sql += " AND item_id=?"
            params.append(item_id)
        if stockroom_id is not None:
            sql += " AND stockroom_id=?"
            params.append(stockroom_id)
        items = []
        for row in conn.execute(sql + " ORDER BY item_id", params):
            out = dict(row)
            out["reasons"] = json.loads(out.pop("reasons_json") or "[]")
            items.append(out)
        counts = {v: sum(1 for i in items if i["verdict"] == v)
                  for v in rules.VERDICTS}
        return {"batch_id": batch_id, "items": items, "counts": counts,
                "model_version": rules.MODEL_VERSION}
    finally:
        conn.close()
