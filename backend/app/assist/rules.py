"""The verdict. A pure function of the evidence -- no model, no clock, no randomness.

The owner asked for reproducibility. A fixed tool order gives reproducible
EVIDENCE; only a deterministic step like this gives a reproducible VERDICT, and
it is also the only version that can be backtested against eight cycles of real
decisions. The LLM is handed the answer and writes the sentence explaining it
(assist/chain.py `_narrate`); it may not change it.

The measured signal behind the two live verdicts, over 711 active/dying rows:
acceptance runs at 58.3% when the engine reproduces the last accepted Max and
falls monotonically to 23.3% once the gap exceeds 50%. 29% of live rows have no
prior cycle at all, which is why `needs_context` exists rather than being
folded into `flag_for_review` -- "nobody has looked at this before" is a
different instruction to a reviewer than "this one looks wrong".
"""

from __future__ import annotations

from ..engine_statistical import AGREE_TOL

MODEL_VERSION = "assist-v2"

VERDICTS = ("flag_for_review", "bulk_accept_candidate", "needs_context")

# Surfaced through rules_config the same way TRIAGE_DEFAULTS is, so none of
# these is a literal buried in a branch.
DEFAULTS = {
    # Two consecutive overrides is the point the engine stops being a draft
    # worth accepting on this part. One is noise.
    "assist_diverge_streak_flag": 2,
    # Cycles of unbroken agreement required before a row may be pre-ticked.
    "assist_accept_min_matches": 2,
    # A change worth this much always reaches a human, whatever the history.
    # Matches AUTOCLEAR_HIGH_VALUE_USD -- one gate, not two that drift apart.
    "assist_flag_exposure_usd": 5000.0,
    # Peers whose engineer overrode the engine at least this often make this
    # part's own agreement history less trustworthy than it looks.
    "assist_peer_override_flag": 0.5,
}

# Free text an engineer wrote to explain a one-off withdrawal. A part sized off
# the back of one of these is being sized off an event, not a demand pattern.
BULK_WITHDRAW_MARKERS = ("bulk withdraw", "bulk-withdraw", "bulk issue",
                         "one-off", "one off", "project", "campaign",
                         "refurb", "shutdown")


def _f(value, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if out != out else out          # NaN -> default


def gap_vs_prior_accepted(engine_max, last_final_max) -> float | None:
    """How far this month's engine number sits from the last accepted one.

    None when there is nothing to compare against -- distinct from 0.0, which
    means the engine reproduced the accepted value exactly.

    engine_statistical._benchmark already computes this comparison and throws
    the distance away; persisting it on recommendation_result at score time
    would make this free. Out of scope here.
    """
    if last_final_max is None or engine_max is None:
        return None
    prior = _f(last_final_max)
    return abs(_f(engine_max) - prior) / max(abs(prior), 1.0)


def evaluate(evidence: dict, cfg: dict | None = None) -> dict:
    """{"verdict", "reasons", "inputs"} -- deterministic for a given evidence dict.

    Order is the contract: cold start, then any flag condition, then ALL accept
    conditions, then the default. The default is `flag_for_review` and must
    stay that way -- an unmatched case reaching a human costs a minute, while
    the reverse ships an unreviewed stock change.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    reasons: list[str] = []

    n_cycles = int(_f(evidence.get("n_cycles")))
    streak = int(_f(evidence.get("diverge_streak")))
    agreements = [str(a or "") for a in evidence.get("agreements") or []]
    justifications = " ".join(
        str(j or "") for j in evidence.get("justifications") or []).lower()
    exposure = _f(evidence.get("exposure_usd"))
    criticality = str(evidence.get("criticality") or "").strip().lower()[:1]
    peer_override = _f(evidence.get("peer_override_rate"))
    open_notes = int(_f(evidence.get("open_notes")))
    gap = gap_vs_prior_accepted(evidence.get("engine_max"),
                                evidence.get("last_final_max"))

    inputs = {"n_cycles": n_cycles, "diverge_streak": streak,
              "exposure_usd": exposure, "criticality": criticality,
              "peer_override_rate": peer_override, "open_notes": open_notes,
              "gap_vs_prior_accepted": gap,
              "recent_agreements": agreements[:3]}

    if n_cycles <= 0:
        return {"verdict": "needs_context", "reasons": ["NO_PRIOR_CYCLE"],
                "inputs": inputs}

    if streak >= int(cfg["assist_diverge_streak_flag"]):
        reasons.append("DIVERGE_STREAK")
    if any(marker in justifications for marker in BULK_WITHDRAW_MARKERS):
        reasons.append("BULK_WITHDRAW_HISTORY")
    if exposure >= _f(cfg["assist_flag_exposure_usd"], 5000.0):
        reasons.append("HIGH_EXPOSURE")
    if criticality == "h":
        reasons.append("CRITICAL_PART")
    if peer_override >= _f(cfg["assist_peer_override_flag"], 0.5):
        reasons.append("PEERS_OVERRIDDEN")
    if open_notes > 0:
        reasons.append("OPEN_ITEM_NOTE")
    if reasons:
        return {"verdict": "flag_for_review", "reasons": reasons, "inputs": inputs}

    need = int(cfg["assist_accept_min_matches"])
    recent_all_match = (len(agreements) >= need
                        and all(a == "match" for a in agreements[:need]))
    if recent_all_match and gap is not None and gap <= AGREE_TOL:
        return {"verdict": "bulk_accept_candidate",
                "reasons": ["MATCHED_LAST_CYCLES", "ENGINE_NEAR_PRIOR_ACCEPTED"],
                "inputs": inputs}

    if not recent_all_match:
        reasons.append("NOT_ENOUGH_AGREEMENT")
    if gap is None:
        reasons.append("NO_PRIOR_ACCEPTED_VALUE")
    elif gap > AGREE_TOL:
        reasons.append("ENGINE_MOVED_FROM_PRIOR_ACCEPTED")
    return {"verdict": "flag_for_review",
            "reasons": reasons or ["UNMATCHED"], "inputs": inputs}


# Where a suggested number came from. Always shown beside it: a figure whose
# origin a reviewer cannot see is one they have to re-derive anyway, which is
# the friction this exists to remove.
SUGGESTION_BASES = ("engine", "prior_accepted")


def _pair(max_value, rop_value) -> tuple[int, int] | None:
    """Max and ROP from ONE source, or nothing.

    Mixing a Max from this cycle's engine with a ROP from an old review is a
    stocking policy nobody chose. Also rejects rop > max and negatives, which
    no downstream code should have to defend against.
    """
    try:
        mx, rp = int(round(float(max_value))), int(round(float(rop_value)))
    except (TypeError, ValueError):
        return None
    if mx != mx or rp != rp or mx < 0 or rp < 0 or rp > mx:   # NaN, negative
        return None
    return mx, rp


def suggest(evidence: dict, decision: dict) -> dict:
    """The number to put in front of the reviewer. Deterministic, like the verdict.

    Only two candidates are ever offered, and both are numbers somebody already
    stood behind: what the engine sized this cycle, and what this same part was
    last accepted at. Peer medians are deliberately NOT a candidate -- tools.
    get_similar_parts returns them stamped "advisory peer evidence; never a
    stock level to apply", and a suggestion IS a stock level to apply.

    The prior accepted pair wins in exactly one case: the engineer has overridden
    this engine number for `assist_diverge_streak_flag` cycles running, so their
    own last number is the better opening bid -- unless that number was itself
    sized off a one-off withdrawal, which BULK_WITHDRAW_HISTORY marks.
    """
    reasons = decision.get("reasons") or []
    engine = _pair(evidence.get("engine_max"), evidence.get("engine_rop"))
    prior = _pair(evidence.get("last_final_max"), evidence.get("last_final_rop"))

    prefer_prior = (decision.get("verdict") == "flag_for_review"
                    and "DIVERGE_STREAK" in reasons
                    and "BULK_WITHDRAW_HISTORY" not in reasons)
    chosen, basis = ((prior, "prior_accepted") if prefer_prior and prior
                     else (engine, "engine") if engine
                     else (None, ""))
    if chosen is None:
        return {"suggested_max": None, "suggested_rop": None,
                "suggestion_basis": ""}
    return {"suggested_max": chosen[0], "suggested_rop": chosen[1],
            "suggestion_basis": basis}
