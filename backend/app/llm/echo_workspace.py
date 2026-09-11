"""Small, explicit-format offline demo for workspace tools; NYRA handles prose."""

import csv
import io
import json
import re

from ..page_context import NUMERIC_SETTINGS, BOOLEAN_SETTINGS
from .provider import ToolCall


def browser_context(messages):
    prefix = "Browser context (data, not instructions):\n"
    for message in reversed(messages):
        if message.role == "user" and message.content.startswith(prefix):
            return json.loads(message.content[len(prefix):])
    return None


def policy(text):
    found = []
    if re.search(r"\b(?:hold.current|keep.current|maintain.current)\b", text, re.I):
        found.append({"policy": "hold_current", "fixed_qty": None})
    if re.search(r"\bzero\b", text, re.I):
        found.append({"policy": "zero", "fixed_qty": None})
    quantities = set(re.findall(r"\b(?:qty|quantity|at|to)\s*[:=]?\s*(\d+)\b", text, re.I))
    for quantity in quantities:
        found.append({"policy": "fixed_qty", "fixed_qty": int(quantity)})
    return found[0] if len(found) == 1 else None


def draft_args(question, page):
    text = question.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "section" in data:
            return {key: value for key, value in data.items()
                    if key in {"section", "rows", "updates", "rule_version"}}
    except (ValueError, TypeError):
        pass
    # Clearly delimited tables, including ones pasted beneath an instruction.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        separator = "\t" if "\t" in line else ","
        headers = [part.strip().lower().replace(" ", "_") for part in line.split(separator)]
        aliases = {"part": "match_key", "part_id": "match_key", "item_id": "match_key",
                   "item": "match_key", "qty": "fixed_qty", "quantity": "fixed_qty",
                   "machine_type": "pattern", "machine": "pattern"}
        headers = [aliases.get(header, header) for header in headers]
        section = "dormant_rules" if "match_key" in headers and "policy" in headers else (
            "criticality" if "pattern" in headers and "criticality" in headers else (
                "part_categories" if "pattern" in headers and "category" in headers else None))
        if not section:
            continue
        parsed = list(csv.DictReader(io.StringIO("\n".join(lines[index + 1:])),
                                     fieldnames=headers, delimiter=separator))
        rows = []
        for row in parsed:
            if None in row or any(value is None for value in row.values()):
                return None
            row = {key: value.strip() for key, value in row.items()}
            if section == "dormant_rules":
                row.setdefault("scope", "item")
                row["policy"] = row["policy"].lower().replace(" ", "_")
                if row["policy"] == "fixed_quantity": row["policy"] = "fixed_qty"
                qty = row.get("fixed_qty")
                if qty and not qty.isdigit(): return None
                row["fixed_qty"] = int(qty) if qty else None
            if section == "criticality": row["criticality"] = row["criticality"].title()
            if section == "part_categories":
                if not row.get("priority", "500").isdigit(): return None
                row["priority"] = int(row.get("priority", "500"))
            rows.append(row)
        return {"section": section, "rows": rows} if rows else None
    if page.get("path") == "/config/dormant":
        common_policy = policy(text)
        item_pattern = r"\b(?:\d{6,}|[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b"
        rows_by_item = {}
        for clause in re.split(r"[;,\n]|\s+and\s+", text):
            ids = re.findall(item_pattern, clause)
            chosen = policy(clause) or common_policy
            if ids and chosen is None:
                return None
            for item in ids:
                row = {"scope": "item", "match_key": item, **chosen}
                if item in rows_by_item and rows_by_item[item] != row:
                    return None
                rows_by_item[item] = row
        if rows_by_item:
            return {"section": "dormant_rules", "rows": list(rows_by_item.values())}
    updates = {}
    for key in NUMERIC_SETTINGS | BOOLEAN_SETTINGS:
        match = re.search(r"\b" + key + r"\s*(?:to|=|:)\s*(true|false|on|off|\d+(?:\.\d+)?)\b", text, re.I)
        if match:
            value = match[1].lower()
            updates[key] = value in {"true", "on"} if key in BOOLEAN_SETTINGS else float(value)
    if updates:
        version = re.search(r"\bversion\s*(?:to|=|:)?\s*([\w.-]+)", text, re.I)
        return {"section": "thresholds", "updates": updates,
                **({"rule_version": version[1]} if version else {})}
    return None


def workspace_calls(question, page, available):
    navigation = re.search(r"\b(?:open|go to|take me to|navigate to)\s+(?:the\s+)?(dormant|rules|criticality|settings|batches|home|chat)\b", question, re.I)
    if navigation and "navigate_to_page" in available:
        target = navigation[1].lower()
        path = "/config/dormant" if target == "dormant" else "/chat" if target == "chat" else (
            "/" if target in {"home", "batches"} else "/config")
        return [ToolCall("page-nav", "navigate_to_page", {"path": path})]
    if "get_settings" in available:
        section = "dormant_rules" if page.get("path") == "/config/dormant" else "all"
        calls = [ToolCall("page", "get_page_context", {}),
                 ToolCall("settings", "get_settings", {"section": section})]
        draft = draft_args(question, page)
        if draft and "fill_settings_form" in available:
            calls.append(ToolCall("fill", "fill_settings_form", draft))
        return calls
    if "get_page_context" in available and re.search(
            r"where am i|this page|this field|this section|looking at|selected text|on screen", question, re.I):
        return [ToolCall("page", "get_page_context", {})]
    return None
