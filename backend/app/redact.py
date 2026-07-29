"""Masking for PRD section 5.1 sensitive fields.

Two different thresholds, deliberately:

  redact_for_memory()  UNCONDITIONAL. Anything persisted to a vector store is
                       long-lived, hard to audit, and hard to delete
                       selectively. Supplier names, unit prices and engineer
                       identities never go in.

  redact_for_prompt()  Configurable (LLM_REDACT_PROMPTS, default off). A prompt
                       to the approved internal endpoint is transient and the
                       engine's explanations are less useful without real
                       numbers. Turn it on for any external endpoint.

The column list is the one already agreed in analysis/common.py SENSITIVE_COLS
-- kept in sync here rather than imported, because backend/ must not depend on
the analysis package at request time.
"""

from __future__ import annotations

import os
from typing import Any

# Mirrors analysis/common.py:51 (PRD section 5.1).
SENSITIVE_COLS = frozenset({
    "unitprice", "supplier_name", "machine_type", "factory", "site",
    "stockroom_id", "stockroom_name", "item_gl_account", "spending_impact",
    "comments", "inventory_owner", "area_owner", "modified_user",
})

# Identity fields that must never reach a durable memory store.
IDENTITY_COLS = frozenset({
    "modified_user", "reviewer", "created_by", "author", "uploaded_by",
    "set_by", "confirmed_by", "senior_approved_by", "user",
})

MASK = "[redacted]"


def _mask(value: Any) -> Any:
    if value is None or value == "":
        return value
    return MASK


def redact_for_memory(obj: Any) -> Any:
    """Strip sensitive + identity fields from anything bound for mem0."""
    return _walk(obj, SENSITIVE_COLS | IDENTITY_COLS)


def redact_for_prompt(obj: Any) -> Any:
    """Off by default -- the approved endpoint is internal (PRD 4.3)."""
    if os.environ.get("LLM_REDACT_PROMPTS", "0") != "1":
        return obj
    return _walk(obj, SENSITIVE_COLS)


def _walk(obj: Any, cols: frozenset[str]) -> Any:
    if isinstance(obj, dict):
        return {k: (_mask(v) if k.lower() in cols else _walk(v, cols))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, cols) for v in obj]
    return obj
