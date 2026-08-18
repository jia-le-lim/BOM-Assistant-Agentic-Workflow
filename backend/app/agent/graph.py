"""Stateless LangGraph for advisory BOM triage."""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import specialists


class TriageState(TypedDict, total=False):
    conn: Any
    actor: dict
    recommendation: dict
    exposure_threshold: float
    clear_confidence_threshold: float
    model_call_limit: int
    features: dict
    history_narrative: str
    demand_narrative: str
    procurement_narrative: str
    sources: Annotated[list[dict], operator.add]
    providers: Annotated[list[str], operator.add]
    models: Annotated[list[str], operator.add]
    specialist_calls: Annotated[int, operator.add]
    llm_calls: Annotated[int, operator.add]
    triage_tier: str
    priority_score: float
    rationale: str
    confidence: float
    focus_question: str


def triage_features(rec: dict, exposure_threshold: float) -> dict:
    exposure = float(rec.get("exposure_usd") or 0)
    high_exposure = exposure >= exposure_threshold
    critical = str(rec.get("sfm_criticality") or "").lower().startswith("h")
    demand_only = (rec.get("agreement") == "match" and not high_exposure
                   and not critical and rec.get("risk_level") != "High")
    return {"high_exposure": high_exposure, "critical": critical,
            "demand_only": demand_only,
            "needs_procurement": (critical or high_exposure
                                  or rec.get("agreement") == "diverge"
                                  or rec.get("risk_level") == "High")}


def intake(state: TriageState) -> dict:
    return {"features": triage_features(state["recommendation"],
                                        state["exposure_threshold"])}


def route_specialists(state: TriageState) -> str | list[str]:
    if state["features"]["demand_only"]:
        return "demand_only"
    if state["features"]["needs_procurement"]:
        return ["history_full", "demand_full", "procurement"]
    return ["history_standard", "demand_standard"]


def persist(state: TriageState) -> dict:
    rec = state["recommendation"]
    history = state.get("history_narrative", "")
    demand = state.get("demand_narrative", "")
    procurement = state.get("procurement_narrative", "")
    state["conn"].execute(
        "INSERT INTO triage_result (batch_id, item_id, stockroom_id, "
        "triage_tier, priority_score, rationale, confidence, focus_question, "
        "history_narrative, demand_narrative, procurement_narrative, "
        "sources_json, provider, model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(batch_id, item_id, stockroom_id) DO UPDATE SET "
        "triage_tier=excluded.triage_tier, "
        "priority_score=excluded.priority_score, rationale=excluded.rationale, "
        "confidence=excluded.confidence, focus_question=excluded.focus_question, "
        "history_narrative=excluded.history_narrative, "
        "demand_narrative=excluded.demand_narrative, "
        "procurement_narrative=excluded.procurement_narrative, "
        "sources_json=excluded.sources_json, provider=excluded.provider, "
        "model=excluded.model, triaged_at=datetime('now')",
        (rec["batch_id"], rec["item_id"], rec["stockroom_id"],
         state["triage_tier"], state["priority_score"], state["rationale"],
         state["confidence"], state["focus_question"], history or None,
         demand or None, procurement or None,
         json.dumps(state.get("sources", []), default=str),
         ",".join(dict.fromkeys(state.get("providers", []))),
         ",".join(dict.fromkeys(state.get("models", [])))),
    )
    return {}


def build_graph():
    builder = StateGraph(TriageState)
    builder.add_node("intake", intake)
    builder.add_node("demand_only", specialists.demand)
    builder.add_node("history_standard", specialists.history)
    builder.add_node("demand_standard", specialists.demand)
    builder.add_node("history_full", specialists.history)
    builder.add_node("demand_full", specialists.demand)
    builder.add_node("procurement", specialists.procurement)
    builder.add_node("synthesis_only", specialists.synthesis)
    builder.add_node("synthesis_standard", specialists.synthesis)
    builder.add_node("synthesis_full", specialists.synthesis)
    builder.add_node("persist", persist)
    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", route_specialists)
    builder.add_edge("demand_only", "synthesis_only")
    builder.add_edge(["history_standard", "demand_standard"],
                     "synthesis_standard")
    builder.add_edge(["history_full", "demand_full", "procurement"],
                     "synthesis_full")
    for node in ("synthesis_only", "synthesis_standard", "synthesis_full"):
        builder.add_edge(node, "persist")
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=False)


TRIAGE_GRAPH = build_graph()
