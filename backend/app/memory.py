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
  * mem0's optional pgvector backend opens a direct psycopg connection, which only works
    where direct Postgres egress exists (Backend_Scaffold_Notes F1/F3 -- on a
    dev machine that means the proxy tunnel must be running);
  * the suite must pass with mem0ai uninstalled.

MEM0_VECTOR_STORE=supabase_rest uses the existing server-only HTTPS connection
for explicit preference storage and cosine recall, with local embeddings.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile

from .redact import redact_for_memory

_client = None
_unavailable = False
_runtime_dir = None
_last_error: str | None = None


def enabled() -> bool:
    return os.environ.get("MEM0_ENABLED", "0") == "1"


def _vector_store() -> str:
    return os.environ.get("MEM0_VECTOR_STORE", "pgvector").strip()


def _collection_name() -> str:
    return os.environ.get("MEM0_COLLECTION_NAME", "bom_engineer_memory").strip()


def _clean_conn_string(raw: str | None) -> str:
    """Best-effort cleanup for values coming from the minimal .env parser.

    backend/app/config.py does not support inline comments, so a line like:
        MEM0_DATABASE_URL=   # defaults to DATABASE_URL
    becomes the literal value "# defaults to DATABASE_URL", which is truthy and
    breaks libpq parsing. Treat that as unset and also strip common inline
    comments (" # ...") from unquoted values.
    """
    val = (raw or "").strip()
    if not val:
        return ""
    if val.startswith("#"):
        return ""
    if " #" in val:
        val = val.split(" #", 1)[0].rstrip()
    if "\t#" in val:
        val = val.split("\t#", 1)[0].rstrip()
    if val.startswith("#"):
        return ""
    return val


def _effective_database_url() -> tuple[str, str]:
    mem0_url = _clean_conn_string(os.environ.get("MEM0_DATABASE_URL"))
    if mem0_url:
        return mem0_url, "MEM0_DATABASE_URL"
    db_url = _clean_conn_string(os.environ.get("DATABASE_URL"))
    if db_url:
        return db_url, "DATABASE_URL"
    return "", "none"


def status() -> dict:
    """Passive status: never imports mem0 or touches the network."""
    import sys

    installed = False
    try:
        installed = importlib.util.find_spec("mem0") is not None
    except Exception:  # noqa: BLE001 - find_spec can choke on a stubbed sys.modules entry
        installed = False
    installed = installed or ("mem0" in sys.modules)

    dsn, dsn_source = _effective_database_url()
    rest_configured = False
    if _vector_store() == "supabase_rest":
        from .rest_conn import configured
        rest_configured = configured()
        installed = importlib.util.find_spec("httpx") is not None
    return {
        "enabled": enabled(),
        "installed": installed,
        "initialised": _client is not None,
        "unavailable": _unavailable,
        "database_url_set": bool(dsn),
        "database_url_source": dsn_source,
        "vector_store": _vector_store(),
        "collection_name": _collection_name(),
        "storage_configured": rest_configured if _vector_store() == "supabase_rest" else bool(dsn),
        "api_key_set": bool(_mem0_openai_api_key()),
        "base_url_set": bool(_mem0_openai_base_url()),
        "embedding_model": _mem0_embedding_model(),
        "embedding_dims": _mem0_embedding_dims(),
        "last_error": (_last_error or "")[:300],
    }


def probe() -> bool:
    """Active check: initialise mem0 and perform a no-write search."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.search("mem0 probe", user_id="__probe__", limit=1)
        return True
    except Exception as exc:  # noqa: BLE001
        global _client, _unavailable, _last_error
        _unavailable = True
        _client = None
        _last_error = type(exc).__name__
        return False


def _mem0_openai_base_url() -> str:
    # Prefer the app's own config so one env file controls both NYRA and mem0.
    return (
        os.environ.get("LLM_BASE_URL", "").strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
    )


def _mem0_openai_api_key() -> str:
    return (
        os.environ.get("LLM_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )


def _mem0_embedding_model() -> str:
    return os.environ.get("MEM0_EMBEDDING_MODEL", "text-embedding-3-small").strip()


def _mem0_embedding_dims() -> int:
    raw = os.environ.get("MEM0_EMBEDDING_DIMS", "1536").strip()
    try:
        dims = int(raw)
    except ValueError:
        dims = 1536
    return dims if dims > 0 else 1536


def _get_client():
    """Lazy: mem0ai is only needed for the direct pgvector path."""
    global _client, _runtime_dir, _unavailable, _last_error
    if _client is not None or _unavailable:
        return _client
    if not enabled():
        return None
    try:
        if _vector_store() == "supabase_rest":
            from .memory_rest import SupabaseRestMemory
            _client = SupabaseRestMemory(
                base_url=_mem0_openai_base_url(), api_key=_mem0_openai_api_key(),
                model=_mem0_embedding_model(), dimensions=_mem0_embedding_dims(),
                collection=_collection_name(),
                timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "180")),
            )
            return _client
        if _vector_store() != "pgvector":
            raise ValueError("Unsupported memory vector store")
        # mem0 OSS always creates a small filesystem config directory when it
        # is imported. Keep that package-internal metadata ephemeral; actual
        # searchable memories live in Supabase through the pgvector store.
        if not os.environ.get("MEM0_DIR"):
            _runtime_dir = tempfile.TemporaryDirectory(prefix="bom-mem0-")
            os.environ["MEM0_DIR"] = _runtime_dir.name

        from mem0 import Memory

        dsn, _src = _effective_database_url()
        if not dsn:
            _last_error = "DatabaseNotConfigured"
            _unavailable = True
            _client = None
            return None
        base_url = _mem0_openai_base_url()
        api_key = _mem0_openai_api_key()
        embed_model = _mem0_embedding_model()
        embed_dims = _mem0_embedding_dims()
        cfg = {
            # mem0 2.0.x only supports SQLite for its auxiliary change history.
            # This advisory layer does not need persistent local history, so
            # keep it process-local rather than writing history.db to disk.
            "history_db_path": ":memory:",
            "vector_store": {
                "provider": "pgvector",
                "config": {"connection_string": dsn,
                           "collection_name": _collection_name(),
                           "embedding_model_dims": embed_dims},
            },
            # Even with infer=False on writes, mem0 still needs an embedder for
            # semantic search. Configure it explicitly so we don't rely on
            # OPENAI_* env vars (this app uses LLM_*).
            "embedder": {
                "provider": "openai",
                "config": {"model": embed_model, "api_key": api_key,
                           "embedding_dims": embed_dims},
            },
            # mem0's default add() path uses an LLM for extraction/inference.
            # Keeping this configured makes the setup predictable even if a
            # caller switches infer=True later.
            "llm": {
                "provider": "openai",
                "config": {"model": _clean_conn_string(os.environ.get("MEM0_LLM_MODEL"))
                                     or os.environ.get("LLM_MODEL", "").strip(),
                           "api_key": api_key},
            },
        }
        if base_url:
            cfg["embedder"]["config"]["openai_base_url"] = base_url
            cfg["llm"]["config"]["openai_base_url"] = base_url
        _client = Memory.from_config(cfg)
    except Exception as exc:  # noqa: BLE001 - degrade to no-memory, never 500 a chat turn
        _unavailable = True
        _client = None
        # Avoid leaking secrets via error strings (DSNs can include passwords).
        _last_error = type(exc).__name__
    return _client


def remember(text: str, user_id: str) -> bool:
    """Store a working preference. Sensitive fields are stripped
    unconditionally -- a vector store is long-lived and hard to redact later."""
    client = _get_client()
    if client is None or not user_id:
        return False
    try:
        kwargs = {"user_id": user_id, "infer": False}
        if _vector_store() == "pgvector":
            # The HTTPS adapter records this itself; tag mem0 OSS writes too
            # so the Qwen collection's model constraint holds on either path.
            kwargs["metadata"] = {"embedding_model": _mem0_embedding_model()}
        client.add(redact_for_memory(text), **kwargs)
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
