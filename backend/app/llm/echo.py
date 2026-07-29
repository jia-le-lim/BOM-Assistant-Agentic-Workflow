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
WRITE_VERBS = ("increase", "decrease", "raise", "lower", "set", "change",
               "bump", "reduce")


class EchoProvider:
    name = "echo"
    model = "echo-stub"

    def chat(self, messages: list[Message],
             tools: list[ToolSpec]) -> Response:
        available = {t.name for t in tools}
        last_user = next((m.content for m in reversed(messages)
                          if m.role == "user"), "")
        tool_results = [m for m in messages if m.role == "tool"]

        if tool_results:
            return Response(content=self._summarise(tool_results),
                            model=self.model, provider=self.name)

        call = self._route(last_user, available)
        if call is None:
            # No content and no tool call -> the loop emits the canonical
            # "I don't know". The hard control lives there, not here.
            return Response(model=self.model, provider=self.name)
        return Response(tool_calls=[call], model=self.model,
                        provider=self.name)

    # -- intent routing ----------------------------------------------------

    def _route(self, q: str, available: set[str]) -> ToolCall | None:
        ql = q.lower()
        item = ITEM_RE.search(q)

        if item and any(v in ql for v in WRITE_VERBS):
            qty = QTY_RE.search(q)
            if qty and "propose_change" in available:
                value = int(next(g for g in qty.groups() if g))
                return ToolCall("c1", "propose_change", {
                    "item_id": item.group(1),
                    "proposed_max": value,
                    "rationale": q.strip(),
                })

        if item and any(w in ql for w in ("why", "explain", "reason")):
            return ToolCall("c1", "get_recommendation",
                            {"item_id": item.group(1)})

        if item and "history" in ql:
            return ToolCall("c1", "get_item_history",
                            {"item_id": item.group(1)})

        if item and any(w in ql for w in ("note", "context", "said", "told")):
            return ToolCall("c1", "get_item_notes", {"item_id": item.group(1)})

        if "top" in ql and any(w in ql for w in ("risk", "exposure", "value")):
            return ToolCall("c1", "top_exposure", {"n": 5})

        if any(w in ql for w in ("threshold", "rule", "config")):
            return ToolCall("c1", "explain_rules", {})

        if any(w in ql for w in ("summary", "summarise", "summarize",
                                 "how many", "status")):
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
