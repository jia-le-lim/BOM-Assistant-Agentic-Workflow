"""Deterministic offline provider.

This is not a toy. It is how the whole agent path stays testable on a network
where every connection must traverse a proxy and where the approved LLM
endpoint is still an open governance question (PRD section 15 question 2).
`get_provider()` returns this whenever LLM_BASE_URL is unset, so CI needs no
secrets and no egress.

It reproduces the intents the pre-agent `/chat` regex router supported, but
expresses them as *tool calls* -- so it exercises the real loop, the real tool
registry, and the real audit path. Swapping in NyraProvider changes which
component picks the tool, nothing else.
"""

from __future__ import annotations

import json
import re

from .provider import Message, Response, ToolCall, ToolSpec

ITEM_RE = re.compile(r"\b(\d{6,})\b")
QTY_RE = re.compile(
    r"\b(?:max|maximum)\b\D{0,20}?(\d+)|\bto\s+(\d+)\b|\bby\s+(\d+)\b",
    re.IGNORECASE)
# Word-bounded, not a substring test: `"set" in "what is the max setting"` is
# true, which used to turn a plain read into a staged proposal.
WRITE_RE = re.compile(
    r"\b(?:increase|decrease|raise|lower|set|change|bump|reduce)\b",
    re.IGNORECASE)

# Word-bounded on purpose: a plain `"now" in ql` substring test also fires on
# "know", which appears in every question that says "I don't know".
CURRENT_RE = re.compile(
    r"\b(?:current|currently|right now|now|today|at the moment)\b",
    re.IGNORECASE)
LEVEL_RE = re.compile(r"\b(?:max|maximum|rop|min|minimum|level|levels|stock)\b",
                      re.IGNORECASE)
RANK_RE = re.compile(r"\b(?:top|highest|biggest|most|first|exposure)\b",
                     re.IGNORECASE)
RANK_SUBJECT_RE = re.compile(r"\b(?:risk|exposure|value|expensive|first)\b",
                            re.IGNORECASE)
LIST_RE = re.compile(r"\b(?:queue|list|show|which items|how many)\b",
                     re.IGNORECASE)
RISK_RE = re.compile(r"\b(High|Medium|Low)\b", re.IGNORECASE)
ACTION_RE = re.compile(r"\b(Increase|Maintain|Decrease)\b", re.IGNORECASE)
RECALL_RE = re.compile(r"\b(?:remember|recall|prefer|usually|last time)\b",
                       re.IGNORECASE)
SUMMARY_RE = re.compile(r"\b(?:summary|summarise|summarize)\b", re.IGNORECASE)
RULES_RE = re.compile(r"\b(?:threshold|thresholds|rule|rules|config)\b",
                      re.IGNORECASE)


class EchoProvider:
    name = "echo"
    model = "echo-stub"

    def chat(self, messages: list[Message],
             tools: list[ToolSpec]) -> Response:
        available = {t.name for t in tools}
        last_user = next((m.content for m in reversed(messages)
                          if m.role == "user"), "")
        tool_results = [m for m in messages if m.role == "tool"]

        if messages and "triage synthesis specialist" in messages[0].content:
            return Response(content=self._triage_verdict(last_user),
                            model=self.model, provider=self.name)

        if tool_results:
            return Response(content=self._summarise(tool_results),
                            model=self.model, provider=self.name)

        item = ITEM_RE.search(last_user)
        if (item and "history" in last_user.lower() and "note" in last_user.lower()
                and {"get_item_history", "get_item_notes"} <= available):
            item_id = item.group(1)
            return Response(tool_calls=[
                ToolCall("c1", "get_item_history", {"item_id": item_id}),
                ToolCall("c2", "get_item_notes", {"item_id": item_id}),
            ], model=self.model, provider=self.name)

        call = self._route(last_user, available)
        if call is None:
            # No content and no tool call -> the loop emits the canonical
            # "I don't know". The hard control lives there, not here.
            return Response(model=self.model, provider=self.name)
        return Response(tool_calls=[call], model=self.model,
                        provider=self.name)

    # -- intent routing ----------------------------------------------------

    def _route(self, q: str, available: set[str]) -> ToolCall | None:
        """First match wins, narrowest signal first.

        Ordering is the whole contract, so it is worth stating: the branches
        that need an item id AND a specific keyword come before the ones that
        need only a keyword, and `batch_summary` is deliberately last -- its
        trigger words ("how many", "status") are exactly the ones a filtered
        list question uses, so any earlier position lets it swallow
        list_review_queue.
        """
        ql = q.lower()
        item = ITEM_RE.search(q)

        if (item and "get_procurement_context" in available
                and any(w in ql for w in
                        ("procurement", "criticality", "lead time", "moq",
                         "order multiple", "ownership"))):
            return ToolCall("c1", "get_procurement_context",
                            {"item_id": item.group(1)})

        if item and WRITE_RE.search(q):
            qty = QTY_RE.search(q)
            if qty and "propose_change" in available:
                value = int(next(g for g in qty.groups() if g))
                # QTY_RE's "max <digits>" alternative happily spans the words
                # between "max" and the item id ("the max for item 100005"), so
                # the id itself can be captured as the quantity. An engineer
                # never states a stock level equal to the part number, and
                # propose_change cannot catch this itself -- the number really is
                # in their message.
                if str(value) != item.group(1):
                    return ToolCall("c1", "propose_change", {
                        "item_id": item.group(1),
                        "proposed_max": value,
                        "rationale": q.strip(),
                    })

        if item and any(w in ql for w in ("why", "explain", "reason")):
            if "get_recommendation" in available:
                return ToolCall("c1", "get_recommendation",
                                {"item_id": item.group(1)})
            if "get_triage_context" in available:
                return ToolCall("c1", "get_triage_context",
                                {"item_id": item.group(1)})

        if item and "history" in ql:
            return ToolCall("c1", "get_item_history",
                            {"item_id": item.group(1)})

        if item and any(w in ql for w in ("note", "context", "said", "told")):
            return ToolCall("c1", "get_item_notes", {"item_id": item.group(1)})

        # "what is item X max right now" -- the item's present levels, which is
        # a different question from what the engine recommends changing them to.
        if (item and not WRITE_RE.search(q)
                and CURRENT_RE.search(q) and LEVEL_RE.search(q)
                and "get_current_values" in available):
            return ToolCall("c1", "get_current_values",
                            {"item_id": item.group(1)})

        if RANK_RE.search(q) and RANK_SUBJECT_RE.search(q):
            return ToolCall("c1", "top_exposure", {"n": 5})

        # Ahead of the list branch: LIST_RE covers "show" and "list", which is
        # how an engineer asks for the rule set too ("show me the thresholds").
        if RULES_RE.search(q):
            return ToolCall("c1", "explain_rules", {})

        if (LIST_RE.search(q) and not SUMMARY_RE.search(q)
                and "list_review_queue" in available):
            args: dict = {}
            risk = RISK_RE.search(q)
            action = ACTION_RE.search(q)
            # The schema enums are title-cased (tools.py list_review_queue);
            # passing "high" would be silently dropped as an unrecognised filter.
            if risk:
                args["risk_level"] = risk.group(1).title()
            if action:
                args["action"] = action.group(1).title()
            return ToolCall("c1", "list_review_queue", args)

        # No `not item` guard: recall takes free text and is item-agnostic, so
        # excluding questions that name a part only costs recall.
        if RECALL_RE.search(q) and "recall_context" in available:
            return ToolCall("c1", "recall_context", {"query": q.strip()})

        if SUMMARY_RE.search(q) or any(w in ql for w in ("how many", "status")):
            return ToolCall("c1", "batch_summary", {})

        return None

    # -- answer rendering --------------------------------------------------

    def _summarise(self, tool_results: list[Message]) -> str:
        parts: list[str] = []
        for m in tool_results:
            try:
                data = json.loads(m.content)
            except json.JSONDecodeError:
                parts.append(m.content)
                continue
            parts.append(self._render(m.name or "", data))
        return " ".join(p for p in parts if p)

    def _render(self, tool: str, data) -> str:
        if isinstance(data, dict) and data.get("_empty"):
            return ""
        # A rejected tool call is a normal outcome, not a crash: propose_change
        # refuses invented numbers and unknown items by design, and the
        # engineer needs to be told why.
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])

        if tool == "get_recommendation":
            return (f"Item {data['item_id']}: {data['explanation']} "
                    f"(action: {data['action']}, review required: "
                    f"{data['review_required']}, risk: {data['risk_level']}, "
                    f"confidence: {data['confidence']:.2f})")

        if tool == "get_current_values":
            return (f"Item {data['item_id']} current: "
                    f"max {data['current_max']}/rop {data['current_rop']}/"
                    f"min {data['current_min']} "
                    f"(stockroom {data['stockroom_id']})")

        if tool == "get_triage_context":
            return (f"Item {data['item_id']} is {data['route'] or 'unrouted'} / "
                    f"{data['consumable'] or 'unclassified'} with "
                    f"{data['agreement'] or 'no'} benchmark agreement. "
                    f"{data['explanation']} Reason: {data['reason_code']}; "
                    f"risk {data['risk_level']}; exposure "
                    f"${data['exposure_usd'] or 0:,.0f}.")

        if tool == "get_procurement_context":
            return (f"Item {data['item_id']} procurement context: criticality "
                    f"{data['criticality']} ({data['criticality_source']}), "
                    f"ownership {data['ownership']}, contractual lead time "
                    f"{data['contractual_lead_time_days'] or 'unknown'} days, "
                    f"order multiple {data['order_qty_multiple'] or 'unknown'}.")

        if tool == "get_item_history":
            rows = data.get("reviews", [])
            lines = [f"{r['reviewed_at']}: {r['reviewer']} {r['decision']} -> "
                     f"max {r['final_max']}/rop {r['final_rop']}/"
                     f"min {r['final_min']}" for r in rows]
            return f"Review history for {data['item_id']}: " + "; ".join(lines)

        if tool == "get_item_notes":
            notes = data.get("notes", [])
            lines = [f"{n['created_at']} {n['author']}: {n['note']}"
                     for n in notes]
            return (f"Carried-forward notes for {data['item_id']}: "
                    + "; ".join(lines))

        if tool == "top_exposure":
            rows = data.get("items", [])
            lines = [f"{r['item_id']} (${r['exposure_usd']:,.0f}, "
                     f"{r['risk_level']})" for r in rows]
            return "Top review items by exposure: " + "; ".join(lines)

        if tool == "list_review_queue":
            rows = data.get("items", [])
            # exposure_usd is nullable in recommendation_result, and "$None"
            # would crash the format spec rather than degrade.
            lines = [f"{r['item_id']} {r['status']} {r['action']} "
                     f"{r['risk_level']} (${r['exposure_usd'] or 0:,.0f})"
                     for r in rows]
            return (f"Review queue for batch {data['batch_id']} "
                    f"({len(rows)} items): " + "; ".join(lines))

        if tool == "recall_context":
            hits = data.get("results", [])
            return ("Recalled working context (not a system record): "
                    + "; ".join(str(h) for h in hits))

        if tool == "propose_change":
            return (f"Staged a proposed change for item {data['item_id']}: "
                    f"max -> {data['proposed_max']}. This is not applied yet — "
                    f"confirm it in the review queue and it will go through the "
                    f"normal approval path (pending_id {data['pending_id']}).")

        if tool == "explain_rules":
            cfg = data.get("config", {})
            shown = ", ".join(f"{k}={v}" for k, v in list(cfg.items())[:8])
            return f"Active rule set {data.get('rule_version')}: {shown}"

        if tool == "batch_summary":
            return (f"Batch {data.get('batch_id')}: {data.get('scored')} scored, "
                    f"statuses {data.get('statuses')}, "
                    f"pending exposure ${data.get('exposure_pending_usd', 0):,.0f}")

        return json.dumps(data)[:400]

    def _triage_verdict(self, payload: str) -> str:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return "{}"
        features = data.get("features", {})
        risk = data.get("risk_level")
        agreement = data.get("agreement")
        engine_confidence = float(data.get("engine_confidence") or 0)
        critical = bool(features.get("critical"))
        high_exposure = bool(features.get("high_exposure"))

        score = {"High": 40, "Medium": 25, "Low": 10}.get(risk, 20)
        score += 30 if agreement == "diverge" else -10 if agreement == "match" else 10
        score += 20 if high_exposure else 0
        score += 25 if critical else 0
        score += 20 * (1 - engine_confidence)
        score = round(min(100, max(0, score)), 1)

        safe_clear = (agreement == "match" and risk == "Low"
                      and not high_exposure and not critical
                      and engine_confidence >= 0.8)
        tier = ("escalate" if critical or risk == "High" or score >= 75 else
                "clear_candidate" if safe_clear else "review")
        signals = [risk or "unknown risk", agreement or "no benchmark agreement"]
        if high_exposure:
            signals.append("high exposure")
        if critical:
            signals.append("critical part")
        return json.dumps({
            "tier": tier,
            "priority_score": score,
            "rationale": "Triage signals: " + ", ".join(signals) + ".",
            "confidence": round(min(0.99, max(0.1, engine_confidence)), 2),
            "focus_question": ("Which procurement or demand assumption needs "
                               "human confirmation before approval?"),
        })
