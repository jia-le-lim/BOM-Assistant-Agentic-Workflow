"""Driver for the advisory triage graph.

Triage is the ONLY part of this system that spends model calls -- 3 to 7 per
item, depending on how many specialists the routing fires. Triaging a whole
2,800-row batch therefore costs thousands of calls, most of them on rows nobody
ever opens. The normal path is one item at a time, from the button on the item
detail page: an engineer pays for the explanation only when actually reading it.

Passing no item_id still triages the whole batch. That is deliberate -- the
resume/refresh semantics and the call budget need it, and a considered bulk run
stays available through the API -- but the console no longer offers it.
"""

import json

from ..db import active_config
from .graph import TRIAGE_GRAPH, route_specialists, triage_features

MAX_LLM_CALLS_PER_BATCH = 2000

# What each graph node is doing, in the engineer's words. Streamed to the
# console so a 30-second run reads as progress instead of a frozen button.
PHASE_LABELS = {
    "intake": "Reading the row and its peer evidence",
    "history_standard": "History specialist",
    "history_full": "History specialist",
    "demand_only": "Demand specialist",
    "demand_standard": "Demand specialist",
    "demand_full": "Demand specialist",
    "procurement": "Procurement specialist",
    "synthesis_only": "Weighing it into one conclusion",
    "synthesis_standard": "Weighing it into one conclusion",
    "synthesis_full": "Weighing it into one conclusion",
    "persist": "Saving the advisory result",
}


def _planned_nodes(features: dict) -> list[str]:
    """The phases this row will run, known before the first model call.

    Derived from the graph's own router rather than restated here, so the plan
    the console draws cannot drift from the path that actually executes.
    """
    branch = route_specialists({"features": features})
    fired = [branch] if isinstance(branch, str) else list(branch)
    suffix = fired[0].split("_", 1)[1]          # only | standard | full
    return ["intake", *fired, f"synthesis_{suffix}", "persist"]


def iter_triage(conn, batch_id: int, actor: dict | None = None,
                llm_call_budget: int = MAX_LLM_CALLS_PER_BATCH,
                refresh: bool = False, item_id: str | None = None,
                stockroom_id: str | None = None):
    """Run triage, yielding one progress event per graph phase.

    Same work and same result as run_triage -- the events are observations of
    the run, never an extra pass over it.
    """
    batch = conn.execute("SELECT status FROM batches WHERE batch_id=?",
                         (batch_id,)).fetchone()
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    if batch["status"] != "scored":
        raise ValueError(f"batch {batch_id} has not been scored")
    cfg = active_config(conn)
    exposure_threshold = float(cfg.get("value_gate_usd", 1000))
    clear_confidence_threshold = float(
        cfg.get("triage_clear_min_confidence", 0.8))

    where = ["r.batch_id=?", "t.batch_id IS NULL"]
    params: list = [batch_id]
    if item_id is None:
        where.append("r.review_required='Y'")     # bulk: engine-flagged only
    else:
        # One item, asked for explicitly. review_required is NOT applied here:
        # the engineer opened this row and pressed the button, which is a
        # stronger signal than the engine's own flag.
        where.append("r.item_id=?")
        params.append(item_id)
        if stockroom_id is not None:
            where.append("r.stockroom_id=?")
            params.append(stockroom_id)

    if refresh:
        # Scope the delete exactly as the run is scoped. Unscoped, a per-item
        # re-run would silently discard every other row's triage in the batch --
        # thousands of model calls thrown away by one button press.
        sql = "DELETE FROM triage_result WHERE batch_id=?"
        del_params: list = [batch_id]
        if item_id is not None:
            sql += " AND item_id=?"
            del_params.append(item_id)
            if stockroom_id is not None:
                sql += " AND stockroom_id=?"
                del_params.append(stockroom_id)
        conn.execute(sql, del_params)

    rows = list(conn.execute(
        "SELECT r.*, b.payload FROM recommendation_result r "
        "JOIN bom_rows b ON b.batch_id=r.batch_id AND b.item_id=r.item_id "
        "AND b.stockroom_id=r.stockroom_id "
        "LEFT JOIN triage_result t ON t.batch_id=r.batch_id AND t.item_id=r.item_id "
        "AND t.stockroom_id=r.stockroom_id "
        "LEFT JOIN similarity_result s ON s.batch_id=r.batch_id "
        "AND s.item_id=r.item_id AND s.stockroom_id=r.stockroom_id "
        "WHERE " + " AND ".join(where) + " "
        "ORDER BY COALESCE(s.is_outlier, 0) DESC, "
        "CASE WHEN r.risk_level='High' THEN 0 ELSE 1 END, "
        "CASE WHEN r.agreement='diverge' THEN 0 ELSE 1 END, "
        "r.exposure_usd DESC, r.item_id",
        params))

    used = triaged = specialist_used = 0
    yield {"type": "start", "batch_id": batch_id, "candidates": len(rows),
           "item_id": item_id}

    for row in rows:
        remaining = llm_call_budget - used
        rec = dict(row)
        payload = json.loads(rec.pop("payload"))
        rec["sfm_criticality"] = payload.get("sfm_criticality", "")
        features = triage_features(rec, exposure_threshold)
        specialist_budget = (1 if features["demand_only"] else
                             3 if features["needs_procurement"] else 2)
        required_calls = 2 * specialist_budget + 1
        if remaining < required_calls:
            break

        planned = _planned_nodes(features)
        yield {"type": "item_start", "item_id": rec["item_id"],
               "stockroom_id": rec["stockroom_id"],
               "phases": [{"node": n, "label": PHASE_LABELS.get(n, n)}
                          for n in planned]}

        # stream_mode=["updates", "values"] gives both the node that just
        # finished (for the live phase list) and the accumulated state (for the
        # totals) from one pass -- no second invoke, no duplicated model calls.
        result: dict = {}
        for mode, chunk in TRIAGE_GRAPH.stream({
            "conn": conn,
            "actor": actor or {"user": "triage", "role": "engineer"},
            "recommendation": rec,
            "exposure_threshold": exposure_threshold,
            "clear_confidence_threshold": clear_confidence_threshold,
            "model_call_limit": 2,
            "sources": [],
            "providers": [],
            "models": [],
            "specialist_calls": 0,
            "llm_calls": 0,
        }, stream_mode=["updates", "values"]):
            if mode == "updates":
                for node in chunk:
                    yield {"type": "phase", "node": node,
                           "label": PHASE_LABELS.get(node, node)}
            else:
                result = chunk

        used += result["llm_calls"]
        specialist_used += result["specialist_calls"]
        triaged += 1
        yield {"type": "item_done", "item_id": rec["item_id"],
               "stockroom_id": rec["stockroom_id"],
               "triage_tier": result["triage_tier"],
               "priority_score": result["priority_score"],
               "confidence": result["confidence"],
               "llm_calls_used": result["llm_calls"]}

    yield {"type": "complete",
           "summary": {"batch_id": batch_id, "candidates": len(rows),
                       "triaged": triaged, "llm_calls_used": used,
                       "specialist_calls_used": specialist_used,
                       "llm_call_budget": llm_call_budget,
                       "budget_exhausted": triaged < len(rows)}}


def run_triage(conn, batch_id: int, actor: dict | None = None,
               llm_call_budget: int = MAX_LLM_CALLS_PER_BATCH,
               refresh: bool = False, item_id: str | None = None,
               stockroom_id: str | None = None) -> dict:
    """Blocking form: drain the stream and hand back the closing summary."""
    summary: dict = {}
    for event in iter_triage(conn, batch_id, actor, llm_call_budget, refresh,
                             item_id, stockroom_id):
        if event["type"] == "complete":
            summary = event["summary"]
    return summary
