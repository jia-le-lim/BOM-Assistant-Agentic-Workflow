"""Batch driver for the advisory triage graph."""

import json

from ..db import active_config
from .graph import TRIAGE_GRAPH, triage_features

MAX_LLM_CALLS_PER_BATCH = 2000


def run_triage(conn, batch_id: int, actor: dict | None = None,
               llm_call_budget: int = MAX_LLM_CALLS_PER_BATCH,
               refresh: bool = False) -> dict:
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
    if refresh:
        conn.execute("DELETE FROM triage_result WHERE batch_id=?", (batch_id,))

    rows = list(conn.execute(
        "SELECT r.*, b.payload FROM recommendation_result r "
        "JOIN bom_rows b ON b.batch_id=r.batch_id AND b.item_id=r.item_id "
        "AND b.stockroom_id=r.stockroom_id "
        "LEFT JOIN triage_result t ON t.batch_id=r.batch_id AND t.item_id=r.item_id "
        "AND t.stockroom_id=r.stockroom_id "
        "LEFT JOIN similarity_result s ON s.batch_id=r.batch_id "
        "AND s.item_id=r.item_id AND s.stockroom_id=r.stockroom_id "
        "WHERE r.batch_id=? AND r.review_required='Y' "
        "AND t.batch_id IS NULL "
        "ORDER BY COALESCE(s.is_outlier, 0) DESC, "
        "CASE WHEN r.risk_level='High' THEN 0 ELSE 1 END, "
        "CASE WHEN r.agreement='diverge' THEN 0 ELSE 1 END, "
        "r.exposure_usd DESC, r.item_id",
        (batch_id,)))

    used = triaged = specialist_used = 0
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
        result = TRIAGE_GRAPH.invoke({
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
        })
        used += result["llm_calls"]
        specialist_used += result["specialist_calls"]
        triaged += 1

    return {"batch_id": batch_id, "candidates": len(rows),
            "triaged": triaged, "llm_calls_used": used,
            "specialist_calls_used": specialist_used,
            "llm_call_budget": llm_call_budget,
            "budget_exhausted": triaged < len(rows)}
