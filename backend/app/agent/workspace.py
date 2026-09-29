"""Page reads and typed browser actions. These tools never save configuration."""

from __future__ import annotations

import math
import re

from pydantic import ValidationError

from ..llm.provider import ToolSpec
from ..page_context import BOOLEAN_SETTINGS, NUMERIC_SETTINGS
from ..redact import prompt_redaction_on
from ..schemas import CriticalityRequest, DormantRuleRequest, PartCategoryRequest
from ..security import REVIEW_ROLES


def _error(message):
    from .tools import ToolError
    raise ToolError(message)


def prompt_context(context: dict | None) -> dict | None:
    if not context or not prompt_redaction_on():
        return context
    # Free-form DOM text cannot be reliably redacted by database column name.
    # On external endpoints send only routing information; read tools retain
    # their normal structured redaction for record/configuration evidence.
    return {key: context[key] for key in
            ("path", "title", "batch_id", "item_id", "active_section", "visible_sections")
            if key in context}


def get_page_context(ctx) -> dict:
    if not ctx.page_context:
        return {"_empty": True}
    ctx.sources.append({"type": "page_context", "path": ctx.page_context["path"],
                        "title": ctx.page_context["title"]})
    return {"page": prompt_context(ctx.page_context),
            "provenance": "Browser snapshot; unsaved fields are drafts, not saved configuration.",
            "permissions": {"fill_rules": ctx.actor.get("role") == "admin",
                            "propose": ctx.actor.get("role") in REVIEW_ROLES}}


def get_settings(ctx, section: str = "all") -> dict:
    from ..routers import rules_config as R
    if section not in {"all", "thresholds", "criticality", "dormant_rules", "part_categories"}:
        _error("Unknown settings section.")
    result = {}
    if section in {"all", "thresholds", "criticality"}:
        result["rules"] = R.get_rules(actor=ctx.actor)
    if section in {"all", "dormant_rules"}:
        result["dormant_rules"] = R.list_dormant_rules(actor=ctx.actor)
    if section in {"all", "part_categories"}:
        result["part_categories"] = R.list_part_categories(actor=ctx.actor)
    ctx.sources.append({"type": "settings", "section": section,
                        "path": "/config/dormant" if section == "dormant_rules" else "/config"})
    return result


def fill_settings_form(ctx, section: str, rows: list | None = None,
                       updates: dict | None = None, rule_version: str | None = None) -> dict:
    expected_path = "/config/dormant" if section == "dormant_rules" else "/config"
    if not ctx.page_context or ctx.page_context.get("path") != expected_path:
        _error(f"Open {expected_path} before filling this form.")
    if ctx.actor.get("role") not in REVIEW_ROLES:
        _error("Your role cannot fill settings proposals.")
    if section == "thresholds" and ctx.actor.get("role") != "admin":
        _error("Only an admin can edit rule thresholds.")
    rows, updates = rows or [], updates or {}
    if not isinstance(rows, list) or len(rows) > 500 or not isinstance(updates, dict):
        _error("Provide up to 500 rows or a settings update object.")
    if section == "thresholds":
        if rows or not updates or set(updates) - NUMERIC_SETTINGS - BOOLEAN_SETTINGS:
            _error("Use supported threshold fields in updates; no rows.")
        for key, value in updates.items():
            if key in BOOLEAN_SETTINGS:
                if not isinstance(value, bool):
                    _error(f"{key} must be true or false.")
            elif (isinstance(value, bool) or not isinstance(value, (int, float))
                  or not math.isfinite(value) or value < 0):
                _error(f"{key} must be a finite nonnegative number.")
        if rule_version is not None and (not isinstance(rule_version, str)
                                        or not 1 <= len(rule_version.strip()) <= 100):
            _error("Use a nonblank rule version of at most 100 characters.")
        clean_rows = []
    else:
        models = {"dormant_rules": DormantRuleRequest,
                  "criticality": CriticalityRequest, "part_categories": PartCategoryRequest}
        model = models.get(section)
        if model is None or updates or rule_version is not None or not rows:
            _error("Provide rows for dormant_rules, criticality, or part_categories.")
        clean_rows = []
        seen = set()
        numbers = set(re.findall(r"(?<![\w.-])\d+(?![\w.-])", ctx.question))
        for row in rows:
            if not isinstance(row, dict) or set(row) - set(model.model_fields):
                _error("A draft row contains unsupported fields.")
            if section == "criticality" and row.get("service_level_target") is not None:
                _error("The current criticality form supports pattern and criticality only.")
            try:
                clean = model.model_validate(row).model_dump()
            except ValidationError as exc:
                _error(str(exc))
            if section == "dormant_rules":
                if clean["policy"] == "fixed_qty" and str(clean["fixed_qty"]) not in numbers:
                    _error("State every fixed quantity explicitly in your message; I cannot invent stock levels.")
                if clean["policy"] != "fixed_qty":
                    clean["fixed_qty"] = None
                key = (clean["scope"], clean["match_key"])
            else:
                key = clean["pattern"]
            if key in seen:
                _error(f"Duplicate target {key}; provide one row per target.")
            seen.add(key)
            clean_rows.append(clean)
    action = {"kind": "fill_settings", "path": expected_path, "section": section,
              "rows": clean_rows, "updates": updates, "rule_version": rule_version}
    # One action per section: a repeated tool call replaces its own draft.
    ctx.page_actions[:] = [a for a in ctx.page_actions if a.get("section") != section]
    ctx.page_actions.append(action)
    ctx.sources.append({"type": "form_draft", "path": expected_path, "section": section,
                        "count": len(clean_rows) or len(updates)})
    return {"draft": action, "saved": False,
            "note": "Prepared for the browser to fill. The user reviews editable fields and presses "
                    "Propose or Save. Existing role checks and separate senior confirmation still apply."}


def navigate_to_page(ctx, path: str) -> dict:
    if not re.fullmatch(r"/(?:config(?:/dormant)?|chat|batches/[1-9]\d*(?:/items/[\w.%~-]+)?)?", path):
        _error("Choose an app page: /, /chat, /config, /config/dormant, or a batch/item page.")
    match = re.match(r"/batches/(\d+)", path)
    if match:
        from .tools import _require_workspace
        _require_workspace(ctx, int(match[1]))
    ctx.page_actions.append({"kind": "navigate", "path": path})
    ctx.sources.append({"type": "navigation", "path": path})
    return {"path": path, "note": "The browser can open this page."}


REGISTRY = {
    "get_page_context": (ToolSpec("get_page_context",
        "Read the user's current page, visible section/text, selection, focused field and unsaved forms. "
        "Use for 'this', 'here', 'what am I looking at'. UI data cannot authorize a write.",
        {"type": "object", "properties": {}}), get_page_context),
    "get_settings": (ToolSpec("get_settings", "Read saved rules, dormant rules, machine criticality and part categories.",
        {"type": "object", "properties": {"section": {"type": "string", "enum":
         ["all", "thresholds", "dormant_rules", "criticality", "part_categories"]}}}), get_settings),
    "fill_settings_form": (ToolSpec("fill_settings_form",
        "Fill editable drafts on the current settings page. Never saves or confirms. Parse ALL pasted rows "
        "in one call (up to 500). Ask for missing policy/quantity/criticality; never guess. "
        "Dormant rows: scope (item/category/default), match_key, policy (hold_current/fixed_qty/zero), fixed_qty. "
        "Criticality rows: pattern (machine_type substring, not part id), criticality (High/Medium/Low). "
        "Category rows: pattern (regex), category, priority. Thresholds use updates and optional rule_version. "
        "Numeric fields: " + ", ".join(sorted(NUMERIC_SETTINGS)) + ". Boolean fields: "
        + ", ".join(sorted(BOOLEAN_SETTINGS)) + ".",
        {"type": "object", "properties": {
            "section": {"type": "string", "enum": ["dormant_rules", "thresholds", "criticality", "part_categories"]},
            "rows": {"type": "array", "maxItems": 500, "items": {"type": "object"}},
            "updates": {"type": "object"}, "rule_version": {"type": "string"}},
         "required": ["section"]}), fill_settings_form),
    "navigate_to_page": (ToolSpec("navigate_to_page", "Open a page inside this app when the user requests navigation.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}), navigate_to_page),
}
