"""The fixed four-step assist chain, built on `langchain_core` runnables only.

    resolve -> evidence (parallel fan-out) -> evaluate -> narrate -> persist

Fixed order is the requirement, not a simplification: free-form tool calling
gives a different evidence set on every run, and an evidence set that moves
cannot support a verdict that has to be reproducible.

Only `langchain_core` is imported. The `langchain` umbrella is NOT installed
and must not be added -- pulling it in needs an offline-mirror install on the
Intel network, the risk docs/ML_Implementation_Plan_TCB.md section 4 flags.

The LangGraph triage in agent/graph.py is untouched and keeps running. It does
a different job (conditional routing to save specialist calls on the expensive
path); this chain runs every live row through the same five sources.
"""

from __future__ import annotations

import json

from langchain_core.runnables import RunnableLambda, RunnableParallel

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

# Every branch of the fan-out, in a fixed order. The count is asserted in
# test_assist_chain: a branch quietly dropped would change the verdict without
# changing the rules.
EVIDENCE_KEYS = ("recommendation", "agreement", "notes", "peers", "procurement")


def _ctx(state: dict) -> ToolContext:
    """A fresh ToolContext per branch.

    RunnableParallel branches receive the same input and may run concurrently,
    so they must not share one -- `ctx.sources` is a plain list and two threads
    appending to the same one is how provenance goes missing. Sources are
    merged in `_merge` instead.
    """
    return ToolContext(conn=state["conn"], actor=state["actor"],
                       batch_id=state["batch_id"], question="assist")


def _empty(value) -> bool:
    return not value or value.get("_empty") is True


def _fetch_recommendation(state: dict) -> dict:
    ctx = _ctx(state)
    data = get_triage_context(ctx, state["item_id"], state["batch_id"],
                              state["stockroom_id"])
    return {"data": data, "sources": ctx.sources}


def _fetch_agreement(state: dict) -> dict:
    ctx = _ctx(state)
    data = get_agreement_history(ctx, state["item_id"], state["stockroom_id"])
    return {"data": data, "sources": ctx.sources}


def _fetch_notes(state: dict) -> dict:
    ctx = _ctx(state)
    data = get_item_notes(ctx, state["item_id"])
    return {"data": data, "sources": ctx.sources}


def _fetch_peers(state: dict) -> dict:
    ctx = _ctx(state)
    data = get_similar_parts(ctx, state["item_id"], state["batch_id"],
                             state["stockroom_id"])
    return {"data": data, "sources": ctx.sources}


def _fetch_procurement(state: dict) -> dict:
    ctx = _ctx(state)
    data = get_procurement_context(ctx, state["item_id"], state["batch_id"],
                                   state["stockroom_id"])
    return {"data": data, "sources": ctx.sources}


def _resolve_row(state: dict) -> dict:
    """Step 1. Nothing is fetched here; this only fixes what we are talking about."""
    return dict(state)


def _merge(state: dict) -> dict:
    """Collapse the fan-out back into one state, keeping every branch's sources."""
    evidence, sources = {}, []
    for key in EVIDENCE_KEYS:
        branch = state.get(key) or {}
        data = branch.get("data")
        evidence[key] = {} if _empty(data) else data
        sources.extend(branch.get("sources") or [])
    base = state[EVIDENCE_KEYS[0]]["state"]
    return {**base, "evidence": evidence, "sources": sources}


def _peer_override_rate(peers: dict) -> float:
    """How often the engineer overrode the engine on this part's peers.

    Derived from the retrieved neighbours rather than stored: similarity_result
    keeps the peers, not a rate, and a rate computed over the peers actually
    shown is the one that matches the evidence the reviewer sees.
    """
    rows = peers.get("neighbours") or []
    decided = [str(r.get("neighbour_decision") or "").lower() for r in rows]
    decided = [d for d in decided if d]
    if not decided:
        return 0.0
    return sum(1 for d in decided if d == "override") / len(decided)


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
        "last_final_max": agr.get("last_final_max"),
        "exposure_usd": rec.get("exposure_usd"),
        "criticality": proc.get("criticality"),
        "peer_override_rate": _peer_override_rate(peers),
        "open_notes": len(notes),
    }


def _evaluate(state: dict) -> dict:
    """Step 3. DETERMINISTIC -- no model runs here, and none may."""
    facts = _facts(state["evidence"])
    # engine_max is not on get_triage_context -- that tool deliberately omits
    # stock quantities -- so the scored row's own value is carried through from
    # the batch query rather than asked of a model.
    facts["engine_max"] = state.get("engine_max")
    if state.get("exposure_usd") is not None:
        facts["exposure_usd"] = state["exposure_usd"]
    return {**state, "facts": facts,
            "decision": rules.evaluate(facts, state["cfg"])}


def _narrate(state: dict) -> dict:
    """Step 4. The only model call, and it is handed the verdict as an input.

    Returns the narrative alongside the state; it never writes state["decision"].
    A provider that is down, or that argues for a different verdict, changes
    nothing except how much prose the reviewer gets.
    """
    decision = state["decision"]
    provider = get_provider()
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
        "reasons_json, narrative, evidence_json, model_version, provider, model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(batch_id, item_id, stockroom_id) DO UPDATE SET "
        "verdict=excluded.verdict, reasons_json=excluded.reasons_json, "
        "narrative=excluded.narrative, evidence_json=excluded.evidence_json, "
        "model_version=excluded.model_version, provider=excluded.provider, "
        "model=excluded.model, assisted_at=datetime('now')",
        (state["batch_id"], state["item_id"], state["stockroom_id"],
         decision["verdict"], json.dumps(decision["reasons"]),
         state["narrative"] or None,
         json.dumps({"inputs": decision["inputs"],
                     "sources": state["sources"]}, default=str),
         rules.MODEL_VERSION, state["provider"], state["model"]))
    return state


# Each branch is handed the whole state and returns its own slice, plus the
# state itself once, so _merge can rebuild without a closure over mutable data.
evidence = RunnableParallel(
    recommendation=RunnableLambda(
        lambda s: {**_fetch_recommendation(s), "state": s}),
    agreement=RunnableLambda(_fetch_agreement),
    notes=RunnableLambda(_fetch_notes),
    peers=RunnableLambda(_fetch_peers),
    procurement=RunnableLambda(_fetch_procurement),
)

assist_chain = (
    RunnableLambda(_resolve_row)        # step 1 -- fix the row
    | evidence                          # step 2 -- the same five sources, always
    | RunnableLambda(_merge)
    | RunnableLambda(_evaluate)         # step 3 -- DETERMINISTIC, no model
    | RunnableLambda(_narrate)          # step 4 -- the only LLM call
    | RunnableLambda(_persist)
)


def live_rows(conn, batch_id: int) -> list[dict]:
    """The active/dying rows of a batch. Dormant rows belong to Layer 1."""
    marks = ",".join("?" for _ in LIVE_ROUTES)
    return [dict(r) for r in conn.execute(
        f"SELECT item_id, stockroom_id, new_max, exposure_usd, route "
        f"FROM recommendation_result WHERE batch_id=? AND route IN ({marks}) "
        f"ORDER BY exposure_usd DESC", (batch_id, *LIVE_ROUTES))]


def run_batch(conn, batch_id: int, actor: dict, cfg: dict | None = None) -> dict:
    """Assist every live row in a batch. A batch with none is a clean no-op."""
    rows = live_rows(conn, batch_id)
    counts = {verdict: 0 for verdict in rules.VERDICTS}
    for row in rows:
        state = assist_chain.invoke({
            "conn": conn, "actor": actor, "batch_id": batch_id,
            "item_id": row["item_id"], "stockroom_id": row["stockroom_id"],
            "engine_max": row["new_max"], "exposure_usd": row["exposure_usd"],
            "cfg": cfg or {},
        })
        counts[state["decision"]["verdict"]] += 1
    return {"batch_id": batch_id, "rows_assisted": len(rows), "counts": counts,
            "model_version": rules.MODEL_VERSION}
