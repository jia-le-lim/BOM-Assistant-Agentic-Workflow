"""Read-only specialist nodes used by the triage graph."""

import json
import re

from ..llm import Message, get_provider
from .loop import run_agent
from .prompts import (TRIAGE_DEMAND_SYSTEM, TRIAGE_HISTORY_SYSTEM,
                      TRIAGE_PROCUREMENT_SYSTEM, TRIAGE_SYNTHESIS_SYSTEM)

TIERS = {"clear_candidate", "review", "escalate"}

# TRIAGE_FORMAT asks for one sentence of implication -- roughly 150-200
# characters. The cap leaves headroom for a long one without letting a model that
# ignores the instruction recite the whole stored record back. The prompt asks,
# this enforces: prompts.py's own header rule, never rely on wording alone.
MAX_NARRATIVE_CHARS = 320

# The conclusion the reviewer reads first. Uncapped it came back at 521
# characters stitching the three specialist lines together verbatim -- two
# sentences by punctuation, a paragraph by eye. Two short sentences is ~300.
MAX_RATIONALE_CHARS = 360
MAX_FOCUS_CHARS = 200

_TABLE_ROW = re.compile(r"^\s*\|.*$", re.MULTILINE)      # GFM row + separator
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
# The model also writes headings mid-sentence ("for item 500794916: ## Review
# History"), which the line-anchored pattern above cannot see. Require a run of
# 2+ hashes AND a trailing space so ingestion's duplicate suffix (item#dup1)
# and ordinary "#3" survive untouched.
_INLINE_HEADING = re.compile(r"#{2,6}\s+")
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")
_BLANKS = re.compile(r"\n{2,}")


def _plain(text: str, limit: int = MAX_NARRATIVE_CHARS) -> str:
    """Markdown source rendered as literal text is worse than no formatting.

    Strips what the specialists are told not to emit, then caps at a sentence
    boundary so a truncation does not read as a thought cut in half.
    """
    out = _TABLE_ROW.sub("", str(text or ""))
    out = _HEADING.sub("", out)
    out = _INLINE_HEADING.sub("", out)
    out = _BULLET.sub("", out)
    out = _EMPHASIS.sub("", out)
    out = _BLANKS.sub("\n", out).strip()
    if len(out) <= limit:
        return out
    clipped = out[:limit]
    stop = max(clipped.rfind(". "), clipped.rfind("! "), clipped.rfind("? "))
    return clipped[:stop + 1] if stop > limit // 2 else clipped.rstrip() + "…"


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
    narrative = (_plain(result["answer"]) if result["sources"] else
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
    return {"demand_narrative": _plain(result["answer"]),
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
    return {"procurement_narrative": _plain(result["answer"]),
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
        "peer_evidence": state.get("similarity", {}),
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
    # Strong benchmark only: the engineer's own number this cycle, or their last
    # decision on this same part. An analogue median agreeing is advisory -- s15
    # put that class near 71% precision, which is not a bar to clear money over.
    safe_clear = (rec.get("agreement") == "match"
                  and rec.get("agreement_source") in ("factory", "prior_review")
                  and rec.get("risk_level") == "Low"
                  and not state["features"]["high_exposure"] and not critical
                  and float(rec.get("confidence") or 0)
                  >= state["clear_confidence_threshold"])
    if tier == "clear_candidate" and not safe_clear:
        tier = fallback_tier

    # Peer evidence is asymmetric by design: it may surface additional risk, it
    # may never cancel a rule-based signal. An outlier has no comparable
    # precedent, so it cannot be a clear candidate whatever the model said.
    priority = _number(verdict.get("priority_score"), 0, 100,
                       90 if fallback_tier == "escalate" else 50)
    if state["features"].get("similarity_outlier") and tier == "clear_candidate":
        tier = "review"
    if state["features"].get("similarity_elevated"):
        priority = min(100.0, priority + 15)
        if tier == "clear_candidate":
            tier = "review"

    return {
        "triage_tier": tier,
        "priority_score": priority,
        "rationale": _plain(verdict.get("rationale") or
                            "Conservative fallback: synthesis returned no valid "
                            "verdict.", MAX_RATIONALE_CHARS),
        "confidence": _number(verdict.get("confidence"), 0, 1, 0),
        "focus_question": _plain(verdict.get("focus_question") or
                                 "Does the stored evidence justify this change?",
                                 MAX_FOCUS_CHARS),
        "providers": [provider.name], "models": [provider.model],
        "llm_calls": 1,
    }
