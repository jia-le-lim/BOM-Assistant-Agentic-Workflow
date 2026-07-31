"""Talk to Supabase over PostgREST instead of opening a Postgres connection.

Why this exists
---------------
The direct route needs a DSN, the project's database password, and -- on a
network whose egress is proxied -- a TCP tunnel, because libpq speaks no HTTP.
This route needs a URL and an API key over ordinary HTTPS, which the proxy
already carries. Same database, same SQL, different transport.

How
---
Every query goes to the `exec_sql` RPC (migration
`exec_sql_rpc_service_role_only`), which substitutes parameters as quoted
literals and runs the statement. That keeps all existing SQL in the routers
working unchanged -- rewriting 13 files of joins and composite-key lookups into
PostgREST filter syntax would have been a different application.

What it costs, stated plainly
-----------------------------
1. NO MULTI-STATEMENT TRANSACTIONS. One HTTP call is one transaction, so
   conn.commit() is a no-op, and a route that writes review_history and then
   updates pending_change can now fail between the two. On the direct
   connection those rolled back together. The review routers are the affected
   path.
2. Every statement is a round trip. executemany() batches into multi-row
   INSERTs (see BATCH) so ingestion is one call per few hundred rows.
3. The key must be a service key, kept server-side. The anon key cannot execute
   this RPC (the grant is service_role only) and must never be able to: it is
   published to browsers, and arbitrary SQL behind a public key is a public
   database.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# Rows per multi-row INSERT. 2,768 scored rows would otherwise be 2,768 HTTP
# round trips; at 500 it is six.
BATCH = 500

_PLACEHOLDER = re.compile(r"\?")
# `user` is reserved in Postgres -- bare `user` parses as CURRENT_USER and
# silently returns the database role. Same lookarounds as db._USER_COL.
_USER_COL = re.compile(r'(?<![\"\w.])user\b(?![\"\w])', re.IGNORECASE)
_QUOTED = re.compile(r"'(?:[^']|'')*'")
_SQLITE_NOW = "datetime('now')"
_PG_NOW = "to_char((now() at time zone 'utc'), 'YYYY-MM-DD HH24:MI:SS')"


class RestError(RuntimeError):
    pass


def _key() -> str:
    """Server-side key only. SUPABASE_ANON_KEY is deliberately not consulted:
    it cannot execute exec_sql, so falling back to it would fail at the first
    query with a confusing 403 instead of failing here with a reason."""
    return (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            or os.environ.get("SUPABASE_SECRET_KEY", "").strip())


def configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL", "").strip() and _key())


def _translate(sql: str) -> str:
    """SQLite-flavoured SQL -> Postgres with $n placeholders.

    Mirrors db._translate: datetime('now') first (it contains a literal), then
    a literal-preserving pass so a `?` inside a string is left alone.
    """
    sql = sql.replace(_SQLITE_NOW, _PG_NOW)
    out, last, n = [], 0, 0

    def frag(s: str) -> str:
        nonlocal n

        def repl(_m: "re.Match[str]") -> str:
            nonlocal n
            n += 1
            return f"${n}"

        return _USER_COL.sub('"user"', _PLACEHOLDER.sub(repl, s))

    for m in _QUOTED.finditer(sql):
        out.append(frag(sql[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(frag(sql[last:]))
    return "".join(out)


def _wants_rows(sql: str) -> bool:
    """Does this statement produce rows to wrap in jsonb_agg?

    Getting it wrong is not subtle: a data-modifying statement wrapped in
    `select ... from (<stmt>) t` is a syntax error, so a WITH that ends in an
    unreturning INSERT/UPDATE/DELETE has to be recognised as row-less.
    """
    upper = sql.upper()
    head = sql.lstrip().lstrip("(").lstrip().upper()
    if " RETURNING " in upper:
        return True
    if head.startswith("WITH"):
        return not any(k in upper for k in (") INSERT", ") UPDATE", ") DELETE"))
    return head.startswith(("SELECT", "VALUES"))


def _jsonable(v: Any) -> Any:
    """Params travel as JSON. Everything the app binds is a scalar."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


class _Result:
    """The cursor surface db.Conn callers already use."""

    def __init__(self, rows: list[dict], lastrowid: int | None = None):
        self._rows = rows
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class RestConn:
    """Same call signature as db.Conn, over HTTP."""

    is_postgres = True

    def __init__(self, url: str, key: str, timeout_s: float = 60.0):
        import httpx

        self._endpoint = url.rstrip("/") + "/rest/v1/rpc/exec_sql"
        # trust_env picks up HTTPS_PROXY -- being able to use it is the whole
        # point of this transport; libpq could not.
        headers = {
            "apikey": key,
            "Content-Type": "application/json",
        }
        # Current sb_secret_* keys are opaque API keys, not JWTs. Sending one
        # as a Bearer token makes the gateway try to parse it as a JWT and
        # reject the request. Legacy service_role keys remain JWTs and still
        # need the Authorization header.
        if not key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {key}"

        self._http = httpx.Client(
            timeout=timeout_s,
            trust_env=True,
            headers=headers,
        )

    # -- wire ---------------------------------------------------------------

    def _rpc(self, sql: str, params: list, want_rows: bool) -> list[dict]:
        r = self._http.post(self._endpoint, content=json.dumps({
            "q": sql,
            "params": [_jsonable(p) for p in params],
            "want_rows": want_rows,
        }))
        if r.status_code >= 400:
            raise RestError(f"{r.status_code} from exec_sql: {r.text[:500]}\n"
                            f"statement: {sql[:200]}")
        body = r.json()
        return body if isinstance(body, list) else []

    # -- db.Conn surface ----------------------------------------------------

    def execute(self, sql: str, params=()) -> _Result:
        translated = _translate(sql)
        return _Result(self._rpc(translated, list(params), _wants_rows(translated)))

    def executemany(self, sql: str, seq) -> None:
        rows = [tuple(r) for r in seq]
        if not rows:
            return
        translated = _translate(sql)
        head, sep, _tail = translated.partition("VALUES")
        if not sep:                     # not an INSERT ... VALUES; one call each
            for r in rows:
                self._rpc(translated, list(r), False)
            return

        width = len(rows[0])
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            groups, flat, n = [], [], 0
            for row in chunk:
                groups.append("(" + ",".join(f"${n + i + 1}" for i in range(width)) + ")")
                flat.extend(row)
                n += width
            self._rpc(f"{head}VALUES {','.join(groups)}", flat, False)

    def insert_returning(self, sql: str, params, table: str) -> int:
        from .db import IDENTITY_PK

        pk = IDENTITY_PK[table]
        rows = self._rpc(_translate(sql) + f" RETURNING {pk}", list(params), True)
        if not rows:
            raise RestError(f"INSERT into {table} returned no {pk}")
        return int(rows[0][pk])

    def commit(self) -> None:
        """No-op: exec_sql commits per call. See the module docstring -- this is
        the one guarantee the direct connection had that this cannot give."""

    def rollback(self) -> None:
        """Cannot undo statements that already committed. Silent by choice: the
        routers call this on error paths, and turning a handled failure into a
        second failure helps nobody."""

    def close(self) -> None:
        self._http.close()


def connect() -> RestConn:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = _key()
    if not url or not key:
        raise RestError(
            "SUPABASE_URL and either SUPABASE_SECRET_KEY or "
            "SUPABASE_SERVICE_ROLE_KEY are required for the REST transport. "
            "The anon/publishable key cannot be used: exec_sql is "
            "granted to service_role only, because a public key that can run "
            "arbitrary SQL is a public database.")
    return RestConn(url, key, float(os.environ.get("SUPABASE_TIMEOUT_S", "60")))
