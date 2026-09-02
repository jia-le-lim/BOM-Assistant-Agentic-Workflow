"""The fixed four-step assist chain.

    gather evidence -> evaluate -> narrate -> persist

Fixed order is the requirement, not a simplification: free-form tool calling
gives a different evidence set on every run, and an evidence set that moves
cannot support a verdict that has to be reproducible.

Plain function calls carry that order. No agent framework is imported here --
not `langchain`, not `langchain_core`: the steps never branch, stream or retry,
so a pipe operator would add a dependency and hide which step runs when. Which
step may see a model is the safety property, and four named calls state it.

The LangGraph triage in agent/graph.py is untouched and keeps running. It does
a different job (conditional routing to save specialist calls on the expensive
path); this chain runs every live row through the same five sources.
"""

from __future__ import annotations

import json

from ..agent.specialists import _plain
from ..agent.tools import (ToolContext, get_agreement_history, get_item_notes,
                           get_procurement_context, get_similar_parts,
                           get_triage_context)
from ..llm import Message, get_provider
from . import rules
from .prompts import ASSIST_NARRATE_SYSTEM

# The routes this layer speaks to. Dormant rows are Layer 1's problem and are
# sized by an engineer's rule; running them through here would spend a model
# call to say "a rule set this".
LIVE_ROUTES = ("active", "dying")

# Every source, in a fixed order. The count is asserted in test_assist_chain:
# a source quietly dropped would change the verdict without changing the rules.
EVIDENCE_KEYS = ("recommendation", "agreement", "notes", "peers", "procurement")


def _ctx(state: dict) -> ToolContext:
    """A fresh ToolContext per source, so each one's provenance stays its own.

    `ctx.sources` is a plain list; one shared context would make it impossible
    to say which source produced which entry. `_gather` concatenates them.
    """
    return ToolContext(conn=state["conn"], actor=state["actor"],
                       batch_id=state["batch_id"], question="assist")


def _empty(value) -> bool:
    return not value or value.get("_empty") is True


EVIDENCE_FETCHERS = {
    "recommendation": lambda ctx, s: get_triage_context(
        ctx, s["item_id"], s["batch_id"], s["stockroom_id"]),
    "agreement": lambda ctx, s: get_agreement_history(
        ctx, s["item_id"], s["stockroom_id"]),
    "notes": lambda ctx, s: get_item_notes(ctx, s["item_id"]),
    "peers": lambda ctx, s: get_similar_parts(
        ctx, s["item_id"], s["batch_id"], s["stockroom_id"]),
    "procurement": lambda ctx, s: get_procurement_context(
        ctx, s["item_id"], s["batch_id"], s["stockroom_id"]),
}


def _gather(state: dict) -> dict:
    """Step 2. Every source, every row, in a fixed order.

    Sequential on purpose. These share one `Conn`, so fanning them out over a
    thread pool would serialise on its lock anyway -- the fixed SET of sources
    is what the verdict needs, not concurrent fetching of them.
    """
    evidence, sources = {}, []
    for key in EVIDENCE_KEYS:
        ctx = _ctx(state)
        data = EVIDENCE_FETCHERS[key](ctx, state)
        evidence[key] = {} if _empty(data) else data
        sources.extend(ctx.sources)
    return {**state, "evidence": evidence, "sources": sources}


def _facts(evidence: dict) -> dict:
    """Flatten the five sources into the flat dict rules.evaluate() reads."""
    rec = evidence.get("recommendation") or {}
    agr = evidence.get("agreement") or {}
    peers = evidence.get("peers") or {}
    proc = evidence.get("procurement") or {}
    notes = (evidence.get("notes") or {}).get("notes") or []
    cycles = agr.get("cycles") or []
    return {
        "n_cycles": agr.get("n_cycles", 0),
        "diverge_streak": agr.get("diverge_streak", 0),
        "agreements": [c.get("agreement") for c in cycles],
        "justifications": [c.get("justification") for c in cycles],
        "engine_max": None,
        "engine_rop": None,
        "last_final_max": agr.get("last_final_max"),
        "last_final_rop": agr.get("last_final_rop"),
        "exposure_usd": rec.get("exposure_usd"),
        "criticality": proc.get("criticality"),
        "peer_override_rate": peers.get("historical_override_rate"),
        "open_notes": len(notes),
    }


def _evaluate(state: dict) -> dict:
    """Step 3. DETERMINISTIC -- no model runs here, and none may."""
    facts = _facts(state["evidence"])
    # engine_max is not on get_triage_context -- that tool deliberately omits
    # stock quantities -- so the scored row's own value is carried through from
    # the batch query rather than asked of a model.
    facts["engine_max"] = state.get("engine_max")
    facts["engine_rop"] = state.get("engine_rop")
    if state.get("exposure_usd") is not None:
        facts["exposure_usd"] = state["exposure_usd"]
    decision = rules.evaluate(facts, state["cfg"])
    # The suggested number is part of the deterministic step for the same
    # reason the verdict is: a reviewer accepts it with one click, so it must
    # be reproducible and backtestable, and the model must not author it.
    decision.update(rules.suggest(facts, decision))
    return {**state, "facts": facts, "decision": decision}


def _narrate(state: dict) -> dict:
    """Step 4. The only model call, and it is handed the verdict as an input.

    Returns the narrative alongside the state; it never writes state["decision"].
    A provider that is down, or that argues for a different verdict, changes
    nothing except how much prose the reviewer gets.
    """
    decision = state["decision"]
    provider = get_provider()
    # The suggested numbers are deliberately NOT in this payload. The prompt
    # bans the model from proposing a Min/ROP/Max, and the cheapest way to keep
    # that true is to never put one in front of it. The UI shows the suggestion
    # beside the sentence.
    payload = {"item_id": state["item_id"], "verdict": decision["verdict"],
               "reasons": decision["reasons"], "evidence": state["facts"]}
    messages = [Message(role="system", content=ASSIST_NARRATE_SYSTEM),
                Message(role="user",
                        content=json.dumps(payload, default=str)[:4000])]
    try:
        response = provider.chat(messages, [])
        narrative = _plain(response.content)
        model, name = response.model, response.provider or provider.name
    except Exception:
        # The verdict is the product; the sentence is a convenience. A dead
        # endpoint must not cost the reviewer their triage.
        narrative, model, name = "", "", provider.name
    return {**state, "narrative": narrative, "model": model, "provider": name}


def _persist(state: dict) -> dict:
    decision = state["decision"]
    state["conn"].execute(
        "INSERT INTO assist_result (batch_id, item_id, stockroom_id, verdict, "
        "reasons_json, narrative, suggested_max, suggested_rop, "
        "suggestion_basis, evidence_json, model_version, provider, model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(batch_id, item_id, stockroom_id) DO UPDATE SET "
        "verdict=excluded.verdict, reasons_json=excluded.reasons_json, "
        "narrative=excluded.narrative, suggested_max=excluded.suggested_max, "
        "suggested_rop=excluded.suggested_rop, "
        "suggestion_basis=excluded.suggestion_basis, "
        "evidence_json=excluded.evidence_json, "
        "model_version=excluded.model_version, provider=excluded.provider, "
        "model=excluded.model, assisted_at=datetime('now')",
        (state["batch_id"], state["item_id"], state["stockroom_id"],
         decision["verdict"], json.dumps(decision["reasons"]),
         state["narrative"] or None, decision["suggested_max"],
         decision["suggested_rop"], decision["suggestion_basis"],
         json.dumps({"inputs": decision["inputs"],
                     "sources": state["sources"]}, default=str),
         rules.MODEL_VERSION, state["provider"], state["model"]))
    return state


def assist_row(state: dict) -> dict:
    """The chain. Fixed order, and the order IS the contract.

    Written as four calls rather than composed runnables: nothing here streams,
    batches, retries, or carries a run config, so a framework's pipe operator
    would only hide which step runs when -- and which step is allowed to see a
    model is the whole safety property (`_evaluate` decides, `_narrate` writes).
    """
    state = _gather(state)      # step 2 -- the same five sources, always
    state = _evaluate(state)    # step 3 -- DETERMINISTIC, no model
    state = _narrate(state)     # step 4 -- the only LLM call
    return _persist(state)


def live_rows(conn, batch_id: int) -> list[dict]:
    """The active/dying rows of a batch. Dormant rows belong to Layer 1."""
    marks = ",".join("?" for _ in LIVE_ROUTES)
    return [dict(r) for r in conn.execute(
        f"SELECT item_id, stockroom_id, new_max, new_rop, exposure_usd, route "
        f"FROM recommendation_result WHERE batch_id=? AND route IN ({marks}) "
        f"ORDER BY exposure_usd DESC", (batch_id, *LIVE_ROUTES))]


def run_batch(conn, batch_id: int, actor: dict, cfg: dict | None = None) -> dict:
    """Assist every live row in a batch. A batch with none is a clean no-op."""
    rows = live_rows(conn, batch_id)
    counts = {verdict: 0 for verdict in rules.VERDICTS}
    for row in rows:
        state = assist_row({
            "conn": conn, "actor": actor, "batch_id": batch_id,
            "item_id": row["item_id"], "stockroom_id": row["stockroom_id"],
            "engine_max": row["new_max"], "engine_rop": row["new_rop"],
            "exposure_usd": row["exposure_usd"],
            "cfg": cfg or {},
        })
        counts[state["decision"]["verdict"]] += 1
    return {"batch_id": batch_id, "rows_assisted": len(rows), "counts": counts,
            "model_version": rules.MODEL_VERSION}
