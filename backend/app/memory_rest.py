"""Preference recall over the app's server-only Supabase HTTPS transport.

Implements the add/search surface used by memory.py. Preferences are stored
verbatim after redaction (infer=False), so this path needs only an embedder.
The optional mem0 OSS path remains available for direct Postgres deployments.
Schema installation is explicit; initialising this client never runs DDL.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from contextlib import closing
from datetime import datetime, timezone

from . import rest_conn


class MemorySchemaMismatch(ValueError):
    """The configured collection cannot hold this embedding model's vectors."""


class SupabaseRestMemory:
    def __init__(self, *, base_url: str, api_key: str, model: str,
                 dimensions: int, collection: str, timeout_s: float):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", collection):
            raise ValueError("Invalid memory collection name")
        if not base_url or not model or dimensions < 1:
            raise ValueError("Embedding endpoint, model and dimensions are required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.collection = collection
        self.timeout_s = timeout_s
        # Validate the deployed column without touching or rewriting memories.
        with closing(rest_conn.connect()) as conn:
            row = conn.execute(
                "SELECT a.atttypmod AS dimensions FROM pg_attribute a "
                "JOIN pg_type t ON t.oid = a.atttypid "
                "WHERE a.attrelid = to_regclass(?) AND a.attname = 'vector' "
                "AND NOT a.attisdropped AND t.typname = 'vector'",
                (f"public.{collection}",),
            ).fetchone()
        if not row or row["dimensions"] != dimensions:
            raise MemorySchemaMismatch("Memory table is missing or has incompatible dimensions")

    @staticmethod
    def _user(user_id: str) -> str:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("A user is required for preference recall")
        return user_id

    def _embed(self, text: str) -> str:
        import httpx

        with httpx.Client(timeout=self.timeout_s, trust_env=True) as client:
            response = client.post(
                self.base_url + "/embeddings",
                headers={"Authorization": f"Bearer {self.api_key or 'ollama'}"},
                json={"model": self.model, "input": [text],
                      "encoding_format": "float", "dimensions": self.dimensions},
            )
            response.raise_for_status()
            vector = response.json()["data"][0]["embedding"]
        if (not isinstance(vector, list) or len(vector) != self.dimensions
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
                or not any(vector)):
            raise MemorySchemaMismatch("Embedder returned an invalid or incompatible vector")
        return json.dumps(vector, allow_nan=False)

    def add(self, text: str, *, user_id: str, infer: bool = False) -> dict:
        user_id = self._user(user_id)
        if infer:
            raise ValueError("HTTPS preference recall supports explicit preferences only")
        vector = self._embed(text)
        memory_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        payload = {"data": text, "user_id": user_id,
                   "hash": hashlib.sha256(text.encode()).hexdigest(),
                   "created_at": now, "updated_at": now,
                   "embedding_model": self.model}
        with closing(rest_conn.connect()) as conn:
            conn.execute(
                f'INSERT INTO public."{self.collection}" (id, vector, payload) '
                "VALUES (?::uuid, ?::extensions.vector, ?::jsonb)",
                (memory_id, vector, json.dumps(payload)),
            )
        return {"results": [{"id": memory_id, "memory": text, "event": "ADD"}]}

    def search(self, query: str, *, user_id: str, limit: int = 5) -> dict:
        user_id = self._user(user_id)
        if type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError("Memory search limit must be between 1 and 25")
        vector = self._embed(query)
        with closing(rest_conn.connect()) as conn:
            rows = conn.execute(
                "SELECT id, payload, GREATEST(0.0, 1.0 - "
                "(vector OPERATOR(extensions.<=>) ?::extensions.vector)) AS score "
                f'FROM public."{self.collection}" '
                "WHERE payload->>'user_id' = ? AND payload->>'embedding_model' = ? "
                "ORDER BY vector OPERATOR(extensions.<=>) ?::extensions.vector "
                "LIMIT ?::integer",
                (vector, user_id, self.model, vector, limit),
            ).fetchall()
        return {"results": [
            {"id": row["id"], "memory": row["payload"]["data"],
             "score": row["score"], "user_id": row["payload"]["user_id"],
             "created_at": row["payload"].get("created_at")}
            for row in rows
        ]}
