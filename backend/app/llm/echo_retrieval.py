"""Small deterministic retrieval examples for the offline provider contract."""

import re

from .provider import ToolCall


def retrieval_call(question: str, available: set[str]) -> ToolCall | None:
    q = question.lower()
    args = {}
    batch = re.search(r"\bbatch\s*#?\s*(\d+)\b", q)
    if batch:
        args["batch_id"] = int(batch[1])
    limit = re.search(r"\b(?:first|show(?: me)?(?: the)?)\s+(\d+)\b", q)
    if limit:
        args["limit"] = int(limit[1])
    listing = re.search(r"\b(show|list|find|which|how many)\b", q)
    if listing and "dormant" in q and re.search(r"\b(rows|items|parts)\b", q) and "list_review_queue" in available:
        args["route"] = "dormant"
        if any(word in q for word in ("uncovered", "no confirmed", "without a confirmed")):
            args["uncovered_dormant"] = True
        return ToolCall("c1", "list_review_queue", args)
    if listing and "search_items" in available:
        explicit = re.search(r"\bcategory\s+[\"']?([a-z_]+)", q)
        if explicit and explicit[1] not in {"is", "of", "in", "rules"}:
            args["category"] = explicit[1]
        elif re.search(r"\bcables?\b", q):
            args["category"] = "cable"
        description = re.search(r"\bdescription\s+contains\s+[\"']?([^\"'.?]+)", q)
        if description:
            args.pop("category", None)
            args["query"] = description[1].strip()
        elif "tubing" in q and "category" not in args:
            args["query"] = "tubing"
        if "category" in args or "query" in args:
            return ToolCall("c1", "search_items", args)
    if "clarify_request" in available:
        if re.search(r"\b(spend|spending|expenditure)\b", q):
            return ToolCall("c1", "clarify_request", {"kind": "spending"})
        if re.search(r"\bhistory\b", q) and not re.search(r"\d{6,}", q):
            return ToolCall("c1", "clarify_request", {"kind": "item"})
    return None
