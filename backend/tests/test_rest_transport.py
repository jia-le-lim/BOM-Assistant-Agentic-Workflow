"""The Supabase-over-HTTPS transport, offline.

Only the parts that can silently corrupt a query are worth testing here: the
placeholder rewrite, the reserved-word quoting, and the multi-row INSERT
batching. Everything past _rpc() is Supabase's side, and the RPC itself was
exercised against the live project when it was created.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rest_conn import BATCH, RestConn, _translate, _wants_rows  # noqa: E402


def test_placeholders_become_numbered():
    out = _translate("SELECT * FROM t WHERE batch_id=? AND item_id=?")
    assert out == "SELECT * FROM t WHERE batch_id=$1 AND item_id=$2"


def test_question_mark_inside_a_literal_is_not_a_placeholder():
    out = _translate("SELECT '? really' AS q FROM t WHERE item_id=?")
    assert out == "SELECT '? really' AS q FROM t WHERE item_id=$1"


def test_user_column_is_quoted_but_lookalikes_are_not():
    out = _translate("SELECT DISTINCT user FROM audit_log WHERE modified_user=?")
    assert '"user"' in out
    assert "modified_user" in out                  # not modified_"user"
    assert out.endswith("=$1")


def test_sqlite_now_becomes_the_postgres_expression():
    out = _translate("UPDATE t SET ts=datetime('now') WHERE id=?")
    assert "datetime('now')" not in out
    assert "now() at time zone 'utc'" in out
    assert "$1" in out


def test_row_returning_statements_are_recognised():
    assert _wants_rows("SELECT 1")
    assert _wants_rows("  select * from t")
    assert _wants_rows("INSERT INTO t (a) VALUES ($1) RETURNING id")
    assert not _wants_rows("UPDATE t SET a=1 WHERE b=$1")
    assert not _wants_rows("DELETE FROM t WHERE b=$1")
    assert not _wants_rows("CREATE TABLE IF NOT EXISTS t (a int)")


def _capture() -> tuple[RestConn, list]:
    conn = RestConn("https://example.invalid", "test-key")
    calls: list = []
    conn._rpc = lambda sql, params, want: calls.append((sql, params, want)) or []
    return conn, calls


def test_executemany_sends_one_multi_row_insert():
    conn, calls = _capture()
    rows = [(1, "100005", "24"), (1, "100006", "24")]
    conn.executemany("INSERT INTO bom_rows (batch_id, item_id, stockroom_id) "
                     "VALUES (?,?,?)", rows)
    conn.close()

    assert len(calls) == 1, "each row must not cost its own HTTP round trip"
    sql, params, want_rows = calls[0]
    assert "($1,$2,$3)" in sql and "($4,$5,$6)" in sql
    assert params == [1, "100005", "24", 1, "100006", "24"]
    assert want_rows is False


def test_executemany_chunks_large_inserts():
    conn, calls = _capture()
    rows = [(1, str(i), "24") for i in range(BATCH + 5)]
    conn.executemany("INSERT INTO bom_rows (batch_id, item_id, stockroom_id) "
                     "VALUES (?,?,?)", rows)
    conn.close()

    assert len(calls) == 2                          # BATCH rows, then the rest
    assert len(calls[0][1]) == BATCH * 3
    assert len(calls[1][1]) == 5 * 3


def test_executemany_on_no_rows_makes_no_call():
    conn, calls = _capture()
    conn.executemany("INSERT INTO bom_rows (batch_id) VALUES (?)", [])
    conn.close()
    assert calls == []


# --- atomicity ------------------------------------------------------------
# One HTTP call is one transaction here, so confirming a proposal must not be
# two statements. The CTE itself was run against the live project inside a
# rolled-back DO block; these tests hold the branch and its shape.

from app.routers.review import _record_review  # noqa: E402


class _Rows:
    def __init__(self, rows): self._rows = rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows
    def __iter__(self): return iter(self._rows)


class _FakeConn:
    """Records statements. `payload` is what current_values() reads back."""

    def __init__(self, is_postgres: bool):
        self.is_postgres = is_postgres
        self.statements: list[tuple[str, tuple]] = []
        self.inserts: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "FROM bom_rows" in sql:
            return _Rows([{"payload": '{"max_qty": 1, "rop_qty": 0, "min_qty": 0}'}])
        if sql.startswith("WITH r AS"):
            return _Rows([{"review_id": 42}])
        return _Rows([])

    def insert_returning(self, sql, params, table):
        self.inserts.append(table)
        self.statements.append((sql, params))
        return 42


_REC = {"item_id": "100005", "stockroom_id": "24", "new_max": 3, "new_rop": 2,
        "new_min": 1, "risk_level": "High", "rule_version": "0.2.0-tcb",
        "model_version": "none"}
_ACTOR = {"user": "alice", "role": "engineer"}


def test_confirming_a_proposal_is_one_statement_on_postgres():
    conn = _FakeConn(is_postgres=True)
    review_id, _cur, _eng, senior = _record_review(
        conn, _ACTOR, 1, _REC, "override", (3, 2, 1), "c", "j", link_pending=7)

    writes = [s for s, _ in conn.statements if "FROM bom_rows" not in s]
    assert len(writes) == 1, "the review and the pending update must not be split"
    assert writes[0].startswith("WITH r AS (INSERT INTO review_history")
    assert "UPDATE pending_change" in writes[0]
    assert conn.statements[-1][1][-1] == 7          # pending_id is the last param
    assert conn.inserts == [], "insert_returning would be a second transaction"
    assert review_id == 42 and senior == 1


def test_sqlite_keeps_the_two_statement_form():
    """SQLite has no data-modifying CTEs -- and does not need one; it is the
    test backend and its two statements share a real transaction."""
    conn = _FakeConn(is_postgres=False)
    review_id, _cur, _eng, _senior = _record_review(
        conn, _ACTOR, 1, _REC, "override", (3, 2, 1), "c", "j", link_pending=7)

    assert conn.inserts == ["review_history"]
    assert any(s.startswith("UPDATE pending_change") for s, _ in conn.statements)
    assert review_id == 42


def test_a_plain_review_touches_no_pending_change():
    conn = _FakeConn(is_postgres=True)
    _record_review(conn, _ACTOR, 1, _REC, "accept", (3, 2, 1), "c", "j")
    assert conn.inserts == ["review_history"]
    assert not any("pending_change" in s for s, _ in conn.statements)
