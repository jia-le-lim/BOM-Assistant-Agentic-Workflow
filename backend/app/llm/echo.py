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
from .echo_workspace import browser_context, workspace_calls

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

# -- the graph's branches (agent/graph.py) ---------------------------------
# One regex per intent, plus the words that pick a tool inside it. Same
# contract as the ladder below: this is a stub the routing test measures
# against a hand-written table, not a model reading the classify prompt.
ASSIST_RE = re.compile(
    r"\b(?:assist|verdict|verdicts|flagged|bulk[- ]accept|"
    r"bulk_accept_candidate|flag_for_review|needs[ _]context)\b", re.IGNORECASE)
ADVISORY_RE = re.compile(
    r"\b(?:similar|comparable|peer|peers|analogue|analogues|unusual|outlier|"
    r"outliers|dormant|coverage)\b", re.IGNORECASE)
REVIEW_ACTION_RE = re.compile(
    r"\b(?:confirm|discard|cancel)\b.*\bpending\b|\bpending\s*#?\s*\d+\b",
    re.IGNORECASE)
PENDING_ID_RE = re.compile(r"(?:pending|#)\s*#?\s*(\d+)", re.IGNORECASE)
RUN_RE = re.compile(r"\b(?:run|start|kick off|execute)\b", re.IGNORECASE)
DORMANT_RE = re.compile(r"\bdormant\b", re.IGNORECASE)
OUTLIER_RE = re.compile(r"\b(?:outlier|outliers|unusual)\b", re.IGNORECASE)
VERDICT_NAME_RE = re.compile(
    r"\b(flag_for_review|bulk_accept_candidate|needs_context)\b", re.IGNORECASE)

# Dormant STOCKING RULES, which are a propose question, not the advisory
# coverage read DORMANT_RE above serves. KEEP_RE is what separates the two:
# "how much do the dormant rules cover" asks, "keep dormant parts at 2" tells.
DORMANT_RULE_RE = re.compile(
    r"\b(?:dormant|no consumption|not moving|non[- ]moving)\b", re.IGNORECASE)
KEEP_RE = re.compile(r"\b(?:keep|hold|stock|maintain)\b", re.IGNORECASE)
CATEGORY_AT_RE = re.compile(
    r"\ball\s+(?:the\s+)?([a-z][a-z0-9_-]{2,30})\s+(?:parts?|items?)\b",
    re.IGNORECASE)
HOLD_CURRENT_RE = re.compile(
    r"\b(?:hold|keep)\b[^.]{0,30}\b(?:current|where it is|as is|today)\b",
    re.IGNORECASE)
ZERO_POLICY_RE = re.compile(r"\b(?:zero|nothing|no stock|don'?t stock)\b",
                            re.IGNORECASE)
# "keep them AT 2" is how a rule quantity is actually said, and QTY_RE has no
# `at` form -- it wants "max N", "to N" or "by N". Kept separate rather than
# widened there, because QTY_RE is what propose_change routes on and every
# staging case in test_chat_routing is tuned against its current shape.
RULE_QTY_RE = re.compile(r"\b(?:at|of)\s+(\d+)\b", re.IGNORECASE)

# Every tool `_route` below can emit. `_classify` uses it to tell a question it
# can actually answer from one it cannot -- the branch names come from the
# graph, but which questions are routable is this stub's own knowledge.
ROUTABLE_TOOLS = frozenset({
    "get_procurement_context", "get_similar_parts", "search_similar_reviews",
    "propose_change", "get_recommendation", "get_triage_context",
    "get_item_history", "get_item_notes", "get_current_values", "top_exposure",
    "explain_rules", "list_review_queue", "recall_context", "batch_summary",
    "get_assist_verdict", "list_assist_queue", "run_assist",
    "get_similarity_outliers", "get_dormant_coverage", "stage_review_action"})


class EchoProvider:
    name = "echo"
    model = "echo-stub"

    def chat(self, messages: list[Message],
             tools: list[ToolSpec]) -> Response:
        available = {t.name for t in tools}
        last_user = next((m.content for m in reversed(messages)
                          if m.role == "user"), "")
        tool_results = [m for m in messages if m.role == "tool"]
        page = browser_context(messages)

        # Ahead of the tool_results short-circuit on purpose: the classify node
        # runs on every turn, including the second turn of a conversation whose
        # first turn left tool messages in the history.
        if messages and messages[0].content.startswith(
                "Classify the engineer's"):
            intent = self._classify(last_user)
            if page:
                if page.get("path") in {"/config", "/config/dormant"}:
                    intent = "configure"
                elif ("this page" in last_user.lower() or "where am i" in last_user.lower()
                      or "looking at" in last_user.lower()
                      or re.search(r"\b(?:open|go to|take me to|navigate to)\b", last_user, re.I)):
                    intent = "lookup"
                elif page.get("item_id"):
                    intent = self._classify(last_user + " item " + page["item_id"])
                    if "why" in last_user.lower() and "assist" not in last_user.lower(): intent = "lookup"
            return Response(content=intent,
                            model=self.model, provider=self.name)

        if messages and "triage synthesis specialist" in messages[0].content:
            return Response(content=self._triage_verdict(last_user),
                            model=self.model, provider=self.name)

        if tool_results:
            return Response(content=self._summarise(tool_results),
                            model=self.model, provider=self.name)

        if page:
            calls = workspace_calls(last_user, page, available)
            if calls:
                return Response(tool_calls=calls, model=self.model, provider=self.name)
            if page.get("item_id") and not ITEM_RE.search(last_user):
                last_user += " item " + page["item_id"]

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

    # -- branch classification (agent/graph.py) ----------------------------

    def _classify(self, q: str) -> str:
        """Name one graph branch. Narrowest signal first, same as `_route`.

        `unknown` is returned only when the ladder below could not route the
        question at all -- a branch name is cheap, and a wrong `unknown` costs
        the engineer an answer they could have had.
        """
        item = ITEM_RE.search(q)

        if REVIEW_ACTION_RE.search(q):
            return "action"
        # Ahead of ADVISORY_RE, which also owns the word "dormant": a coverage
        # QUESTION belongs to advisory, an INSTRUCTION to propose, and KEEP_RE
        # is the difference. Ahead of the item+WRITE_RE propose branch too --
        # "keep all filter parts at 2" names a category, not an item id.
        if ((DORMANT_RULE_RE.search(q) or CATEGORY_AT_RE.search(q))
                and KEEP_RE.search(q)):
            return "propose"
        # Before assist/advisory: "set the max on 100005 to 3" carries no branch
        # keyword. No quantity is required here on purpose -- "bump 100005 a
        # bit" is a propose question the engineer has asked badly, and the
        # propose branch is the one whose prompt tells the model to ask for the
        # exact value. Staging it still needs a number; propose_change enforces
        # that, and this stub's ladder will not emit a call without one.
        if item and WRITE_RE.search(q):
            return "propose"
        if ASSIST_RE.search(q):
            return "assist"
        if ADVISORY_RE.search(q):
            return "advisory"
        if self._route(q, set(ROUTABLE_TOOLS)) is not None:
            return "lookup"
        return "unknown"

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

        # -- the graph's non-lookup branches --------------------------------
        # All ahead of the generic branches below, because their trigger words
        # collide with them: "confirm" carries an item id, "show the flagged
        # items" is a LIST_RE phrase, "dormant rule coverage" is a RULES_RE one,
        # and "which items are outliers" is both.
        if "stage_review_action" in available and REVIEW_ACTION_RE.search(q):
            pending = PENDING_ID_RE.search(q)
            # "cancel" is a discard. Defaulting it to confirm_pending would show
            # an engineer who asked to cancel a card whose primary button
            # records the override.
            drop = any(w in ql for w in ("discard", "cancel", "throw away",
                                         "drop it"))
            args: dict = {
                "kind": "discard_pending" if drop else "confirm_pending",
                "item_id": item.group(1) if item else "",
            }
            if pending:
                args["pending_id"] = int(pending.group(1))
            if args["item_id"]:
                return ToolCall("c1", "stage_review_action", args)

        if ASSIST_RE.search(q):
            if "run_assist" in available and RUN_RE.search(q):
                # No `confirm`: the gate must fire on the first ask, so the
                # engineer sees the row count before anything is spent.
                return ToolCall("c1", "run_assist", {})
            if item and "get_assist_verdict" in available:
                return ToolCall("c1", "get_assist_verdict",
                                {"item_id": item.group(1)})
            if "list_assist_queue" in available:
                verdict = VERDICT_NAME_RE.search(q)
                args = {"verdict": verdict.group(1).lower()} if verdict else {}
                if not verdict and "flag" in ql:
                    args = {"verdict": "flag_for_review"}
                return ToolCall("c1", "list_assist_queue", args)

        # Ahead of get_dormant_coverage, which fires on the bare word "dormant".
        # A rule is only emitted when the policy is unambiguous: a scope with no
        # readable policy falls through to the coverage read rather than
        # guessing at a quantity.
        #
        # The dormant signal is REQUIRED, not just KEEP_RE. KEEP_RE matches
        # "stock", so on its own "set the stock max for 100005 at 5" -- an
        # ordinary propose_change on the scored batch -- came out as a standing
        # rule that would size that part on every future run. This mirrors what
        # `_classify` already requires one level up.
        if ("propose_dormant_rule" in available and KEEP_RE.search(q)
                and (DORMANT_RULE_RE.search(q) or CATEGORY_AT_RE.search(q))):
            cat = CATEGORY_AT_RE.search(q)
            rule_args: dict = {}
            if item:
                rule_args = {"scope": "item", "match_key": item.group(1)}
            elif cat:
                rule_args = {"scope": "category",
                             "match_key": cat.group(1).lower()}
            if rule_args:
                qty = RULE_QTY_RE.search(q) or QTY_RE.search(q)
                if HOLD_CURRENT_RE.search(q):
                    rule_args["policy"] = "hold_current"
                elif ZERO_POLICY_RE.search(q):
                    rule_args["policy"] = "zero"
                elif qty:
                    value = int(next(g for g in qty.groups() if g))
                    # Same guard as propose_change: QTY_RE's "max <digits>"
                    # alternative can span words and capture the item id itself.
                    if not item or str(value) != item.group(1):
                        rule_args["policy"] = "fixed_qty"
                        rule_args["fixed_qty"] = value
                if "policy" in rule_args:
                    return ToolCall("c1", "propose_dormant_rule", rule_args)

        if "get_dormant_coverage" in available and DORMANT_RE.search(q):
            return ToolCall("c1", "get_dormant_coverage", {})

        if (not item and "get_similarity_outliers" in available
                and OUTLIER_RE.search(q)):
            return ToolCall("c1", "get_similarity_outliers", {})

        # Before the write branch on purpose: "similar parts for 500005" carries
        # an item id, and WRITE_RE would otherwise claim anything with a verb.
        if (item and "get_similar_parts" in available
                and any(w in ql for w in ("similar", "comparable", "peer",
                                          "unusual", "analogue"))):
            return ToolCall("c1", "get_similar_parts",
                            {"item_id": item.group(1)})

        if ("search_similar_reviews" in available
                and any(w in ql for w in ("what did we say", "past comment",
                                          "previously discussed"))):
            return ToolCall("c1", "search_similar_reviews", {"query": q[:200]})

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

        if tool == "get_page_context":
            page = data["page"]
            detail = page.get("selected_text") or page.get("visible_text", "")[:700]
            focused = page.get("focused_field")
            if focused: detail = f"Focused field: {focused['label']}; draft value: {focused['value']}. " + detail
            return f"You are on {page['title']} ({page['path']}), viewing {page.get('active_section') or page['title']}. {detail}"
        if tool == "get_settings":
            return "Saved settings retrieved. To fill a draft offline, paste a table with item_id, policy, quantity; machine_type, criticality; or use setting_name=value. State the policy and every fixed quantity explicitly."
        if tool == "fill_settings_form":
            draft = data["draft"]
            count = len(draft["rows"]) or len(draft["updates"])
            return f"Prepared {count} entries for {draft['section']}. Review the editable draft on the page, then use Propose or Save. Nothing has been saved yet."
        if tool == "navigate_to_page":
            return f"Requested opening {data['path']}."

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

        if tool == "get_assist_verdict":
            reasons = ", ".join(data.get("reasons") or []) or "no reasons recorded"
            narrative = data.get("narrative") or ""
            return (f"Assist says {data['verdict']} for item "
                    f"{data['item_id']} ({reasons}). {narrative} "
                    f"Advisory only: nothing has been decided.")

        if tool == "list_assist_queue":
            rows = data.get("items", [])
            counts = data.get("counts", {})
            lines = [f"{r['item_id']} {r['verdict']}" for r in rows]
            return (f"Assist queue for batch {data.get('batch_id')} "
                    f"({counts}): " + "; ".join(lines)
                    + " These are advisory verdicts, not decisions.")

        if tool == "get_similarity_outliers":
            rows = data.get("items", [])
            lines = [f"{r['item_id']} (score {r['outlier_score']}, "
                     f"{r['neighbour_count']} peers)" for r in rows]
            return (f"Peer outliers in batch {data.get('batch_id')}: "
                    + "; ".join(lines)
                    + " Advisory evidence; no level follows from it.")

        if tool == "propose_dormant_rule":
            qty = data.get("fixed_qty")
            return (f"Recorded a proposed dormant rule for {data.get('scope')} "
                    f"'{data.get('match_key')}': {data.get('policy')}"
                    f"{'' if qty is None else f' of {qty}'}. It is not active — "
                    f"someone with approval rights must confirm it, and it "
                    f"applies from the next engine run.")

        if tool == "get_dormant_coverage":
            return (f"Dormant coverage for batch {data.get('batch_id')}: "
                    f"{data.get('matched')} of {data.get('dormant_rows')} rows "
                    f"({data.get('pct')}%) matched by {data.get('confirmed_rules')} "
                    f"confirmed rule(s). Book value "
                    f"${data.get('proposed_book_usd', 0):,.0f} against the "
                    f"engine's ${data.get('engine_book_usd', 0):,.0f} "
                    f"(delta ${data.get('delta_usd', 0):,.0f}).")

        if tool == "run_assist":
            if data.get("gated"):
                return (f"Assisting batch {data['batch_id']} covers "
                        f"{data['live_rows']} live rows and spends about "
                        f"{data['estimated_model_calls']} model calls. Nothing "
                        f"has run. Say so explicitly if you want it to.")
            return (f"Assisted {data.get('rows_assisted')} live rows in batch "
                    f"{data.get('batch_id')}: {data.get('counts')}. Every "
                    f"verdict is advisory; no row has been decided.")

        if tool == "stage_review_action":
            action = data.get("staged_action", {})
            return (f"Staged a {action.get('kind')} card for item "
                    f"{action.get('item_id')} (pending "
                    f"{action.get('pending_id')}). Nothing has been recorded — "
                    f"press confirm to record it, and an override still needs "
                    f"senior approval.")

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
