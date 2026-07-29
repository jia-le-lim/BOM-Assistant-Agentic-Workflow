"""Regression tests for the SQLite -> Postgres SQL translation.

Both cases here were live bugs caught by dumping the translated SQL and running
it against the real Supabase instance, not by the SQLite suite -- which passes
either way, because on SQLite no translation happens at all. That is the gap
this file closes.

  1. `datetime('now')` contains a string literal, so a naive
     literal-preserving pass hid it and it was never substituted. Postgres has
     no datetime() -- every write touching it would have failed.

  2. `SELECT DISTINCT user FROM audit_log` left `user` unquoted. Postgres parses
     bare `user` as CURRENT_USER, so it does not error -- it silently returns
     the database role ('postgres') instead of the column ('alice'). Verified
     against the live database: quoted gives 'alice', bare gives 'postgres'.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def pg(monkeypatch):
    """Point the data layer at Postgres for the duration of one test.

    No module reload needed: config resolves the target at call time precisely
    so that monkeypatch works (a module constant would be captured at import,
    before the patch, which is also how a populated backend/.env could silently
    become the test database).
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host:5432/db")
    import app.db
    return app.db


def test_placeholders_become_pyformat(pg):
    assert pg._translate("SELECT * FROM t WHERE a=? AND b=?") == \
        "SELECT * FROM t WHERE a=%s AND b=%s"


def test_datetime_now_is_substituted(pg):
    out = pg._translate("UPDATE t SET x=datetime('now') WHERE id=?")
    assert "datetime(" not in out, out
    assert "now() at time zone 'utc'" in out
    assert out.endswith("WHERE id=%s")


def test_datetime_now_substituted_more_than_once(pg):
    out = pg._translate(
        "INSERT INTO t (a) VALUES (datetime('now')) "
        "ON CONFLICT(a) DO UPDATE SET b=datetime('now')")
    assert "datetime(" not in out
    assert out.count("to_char") == 2


def test_bare_user_is_quoted(pg):
    """The silent-wrong-answer case."""
    assert pg._translate("SELECT DISTINCT user FROM audit_log") == \
        'SELECT DISTINCT "user" FROM audit_log'
    assert pg._translate("INSERT INTO audit_log (user, role) VALUES (?,?)") == \
        'INSERT INTO audit_log ("user", role) VALUES (%s,%s)'


@pytest.mark.parametrize("sql", [
    "SELECT modified_user FROM t",
    "SELECT user_id FROM t",
    "SELECT * FROM users",
    'SELECT "user" FROM audit_log',
])
def test_user_lookalikes_untouched(pg, sql):
    """modified_user / user_id / users / already-quoted must not be rewritten."""
    out = pg._translate(sql)
    assert out == sql, out
    assert '""' not in out


def test_string_literals_are_never_rewritten(pg):
    """A literal question mark is data, not a placeholder."""
    assert pg._translate("SELECT * FROM t WHERE note = 'why? because'") == \
        "SELECT * FROM t WHERE note = 'why? because'"
    assert pg._translate("SELECT * FROM b WHERE status='scored' AND id=?") == \
        "SELECT * FROM b WHERE status='scored' AND id=%s"


def test_sqlite_mode_is_a_passthrough():
    """No translation must happen on the default dev/test backend."""
    import app.db
    sql = "INSERT INTO audit_log (user) VALUES (?) -- datetime('now')"
    assert app.db._translate(sql) == sql


def test_identity_pk_covers_every_insert_returning_caller(pg):
    """insert_returning() KeyErrors on Postgres if a table is missing here."""
    for table in ("batches", "review_history", "pending_change",
                  "conversation_turn", "audit_log", "item_note", "rule_config"):
        assert table in pg.IDENTITY_PK


# -- the app must not silently run on a local file ------------------------

def test_unconfigured_database_is_a_hard_error(monkeypatch):
    """The application runs on Supabase. An unset DATABASE_URL must fail loudly
    rather than fall back to SQLite -- silently writing engineers' decisions to
    a local file nobody else can see is the failure that actually costs."""
    from app.config import DatabaseNotConfigured, require_database

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BOM_ALLOW_SQLITE", raising=False)
    with pytest.raises(DatabaseNotConfigured, match="Supabase"):
        require_database()


def test_postgres_url_satisfies_the_guard(monkeypatch):
    from app.config import require_database

    monkeypatch.delenv("BOM_ALLOW_SQLITE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:6543/postgres")
    require_database()          # must not raise


def test_tests_opt_into_sqlite_explicitly(monkeypatch):
    from app.config import require_database

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BOM_ALLOW_SQLITE", "1")
    require_database()          # must not raise


def test_conftest_clears_database_url(db_file):
    """A developer with backend/.env populated must not have the suite run
    against their real Supabase project."""
    import os

    from app.config import is_postgres
    assert os.environ.get("DATABASE_URL") in (None, "")
    assert is_postgres() is False
