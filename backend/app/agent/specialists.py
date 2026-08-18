"""Read-only specialist nodes used by the triage graph."""

import json

from ..llm import Message, get_provider
from .loop import run_agent
from .prompts import (TRIAGE_DEMAND_SYSTEM, TRIAGE_HISTORY_SYSTEM,
                      TRIAGE_PROCUREMENT_SYSTEM, TRIAGE_SYNTHESIS_SYSTEM)

TIERS = {"clear_candidate", "review", "escalate"}


def history(state: dict) -> dict:
    item_id = state["recommendation"]["item_id"]
    result = run_agent(
        state["conn"],
        f"Summarize review history and active notes for item {item_id}.",
        state["recommendation"]["batch_id"],
        state["actor"],
        allow_writes=False,
        system_prompt=TRIAGE_HISTORY_SYSTEM,
        tool_names={"get_item_history", "get_item_notes"},
        max_model_calls=state["model_call_limit"],
    )
    narrative = (result["answer"] if result["sources"] else
                 "No recorded review history or active engineer notes.")
    return {"history_narrative": narrative, "sources": result["sources"],
            "providers": [result["provider"]], "models": [result["model"]],
            "specialist_calls": 1, "llm_calls": result["model_calls"]}


def demand(state: dict) -> dict:
    item_id = state["recommendation"]["item_id"]
    result = run_agent(
        state["conn"],
        f"Explain why item {item_id} was flagged for review.",
        state["recommendation"]["batch_id"],
        state["actor"],
        allow_writes=False,
        system_prompt=TRIAGE_DEMAND_SYSTEM,
        tool_names={"get_triage_context"},
        max_model_calls=state["model_call_limit"],
    )
    return {"demand_narrative": result["answer"],
            "sources": result["sources"],
            "providers": [result["provider"]], "models": [result["model"]],
            "specialist_calls": 1, "llm_calls": result["model_calls"]}


def procurement(state: dict) -> dict:
    item_id = state["recommendation"]["item_id"]
    result = run_agent(
        state["conn"],
        f"Interpret procurement context for item {item_id}.",
        state["recommendation"]["batch_id"],
        state["actor"],
        allow_writes=False,
        system_prompt=TRIAGE_PROCUREMENT_SYSTEM,
        tool_names={"get_procurement_context"},
        max_model_calls=state["model_call_limit"],
    )
    return {"procurement_narrative": result["answer"],
            "sources": result["sources"],
            "providers": [result["provider"]], "models": [result["model"]],
            "specialist_calls": 1, "llm_calls": result["model_calls"]}


def _number(value, low: float, high: float, fallback: float) -> float:
    try:
        return min(high, max(low, float(value)))
    except (TypeError, ValueError):
        return fallback


def synthesis(state: dict) -> dict:
    rec = state["recommendation"]
    evidence = {
        "item_id": rec["item_id"],
        "risk_level": rec.get("risk_level"),
        "agreement": rec.get("agreement"),
        "engine_confidence": rec.get("confidence"),
        "exposure_usd": rec.get("exposure_usd"),
        "features": state["features"],
        "history": state.get("history_narrative", "")[:2000],
        "demand": state.get("demand_narrative", "")[:2000],
        "procurement": state.get("procurement_narrative", "")[:2000],
    }
    provider = get_provider()
    response = provider.chat([
        Message(role="system", content=TRIAGE_SYNTHESIS_SYSTEM),
        Message(role="user", content=json.dumps(evidence, default=str)),
    ], [])
    try:
        text = response.content or ""
        verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except (ValueError, TypeError, json.JSONDecodeError):
        verdict = {}

    critical = state["features"]["critical"]
    high_risk = rec.get("risk_level") == "High"
    fallback_tier = "escalate" if critical or high_risk else "review"
    tier = verdict.get("tier") if verdict.get("tier") in TIERS else fallback_tier
    safe_clear = (rec.get("agreement") == "match"
                  and rec.get("risk_level") == "Low"
                  and not state["features"]["high_exposure"] and not critical
                  and float(rec.get("confidence") or 0)
                  >= state["clear_confidence_threshold"])
    if tier == "clear_candidate" and not safe_clear:
        tier = fallback_tier

    return {
        "triage_tier": tier,
        "priority_score": _number(verdict.get("priority_score"), 0, 100,
                                  90 if fallback_tier == "escalate" else 50),
        "rationale": str(verdict.get("rationale") or
                         "Conservative fallback: synthesis returned no valid verdict.")[:4000],
        "confidence": _number(verdict.get("confidence"), 0, 1, 0),
        "focus_question": str(verdict.get("focus_question") or
                              "Does the stored evidence justify this change?")[:1000],
        "providers": [provider.name], "models": [provider.model],
        "llm_calls": 1,
    }
