"""mem0 seam -- optional, off by default, never authoritative.

The split this file exists to enforce
-------------------------------------
"Increase item 500123456 max to 5" is a DECISION. It goes to Postgres
(pending_change -> review_history): exact, replayable, versioned, exportable to
WINGS, auditable. PRD section 5.3 says as much in its own words -- review_history
is "the true long-term memory -- a table, not chatbot memory".

"I don't stock Phoenix consumables under $50" is a PREFERENCE. Fuzzy recall is
exactly what mem0 is for, and losing it costs nothing.

If mem0 owned both, its LLM extraction could legitimately compress the first
into the second: useful context, destroyed inventory decision. So nothing here
is ever read back as fact -- `recall_context` labels every hit
`authoritative: false`, and the tool layer refuses to base a proposal on one.

Off by default because:
  * with one month of history there is almost nothing worth recalling;
  * mem0's pgvector backend opens a direct psycopg connection, which only works
    where direct Postgres egress exists (Backend_Scaffold_Notes F1/F3 -- on a
    dev machine that means the proxy tunnel must be running);
  * the suite must pass with mem0ai uninstalled.
"""

from __future__ import annotations

import os

from .redact import redact_for_memory

_client = None
_unavailable = False


def enabled() -> bool:
    return os.environ.get("MEM0_ENABLED", "0") == "1"


def _get_client():
    """Lazy: mem0ai is never imported unless explicitly switched on."""
    global _client, _unavailable
    if _client is not None or _unavailable:
        return _client
    if not enabled():
        return None
    try:
        from mem0 import Memory

        dsn = os.environ.get("MEM0_DATABASE_URL") or os.environ.get("DATABASE_URL")
        cfg = {
            "vector_store": {
                "provider": "pgvector",
                "config": {"connection_string": dsn,
                           "collection_name": "bom_engineer_memory"},
            },
        }
        base_url = os.environ.get("LLM_BASE_URL", "").strip()
        if base_url:
            cfg["llm"] = {"provider": "openai",
                          "config": {"model": os.environ.get("LLM_MODEL", ""),
                                     "openai_base_url": base_url}}
        _client = Memory.from_config(cfg)
    except Exception:  # noqa: BLE001 - degrade to no-memory, never 500 a chat turn
        _unavailable = True
        _client = None
    return _client


def remember(text: str, user_id: str) -> bool:
    """Store a working preference. Sensitive fields are stripped
    unconditionally -- a vector store is long-lived and hard to redact later."""
    client = _get_client()
    if client is None or not user_id:
        return False
    try:
        client.add(redact_for_memory(text), user_id=user_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def search_memory(query: str, user_id: str, limit: int = 5) -> list[dict]:
    client = _get_client()
    if client is None or not user_id:
        return []
    try:
        res = client.search(query, user_id=user_id, limit=limit)
        hits = res.get("results", res) if isinstance(res, dict) else res
        return [{"memory": h.get("memory", ""), "score": h.get("score")}
                for h in (hits or [])]
    except Exception:  # noqa: BLE001
        return []
