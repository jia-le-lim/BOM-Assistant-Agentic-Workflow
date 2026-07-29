"""Data layer -- one code path, two dialects (SQLite for dev/test, Postgres/Supabase).

PRD section 5.3 specifies item_master / inventory_snapshot / consumption_snapshot
as separate normalized tables. The scaffold deviates deliberately: the source is a
single 116-column workbook, so rows are stored whole (JSON payload + indexed key
columns) in `bom_rows`, preserving full input fidelity for the engine. Normalize
when direct WINGS/SFM connectors exist. Documented in Backend_Scaffold_Notes.md.

Why raw SQL behind a thin shim rather than an ORM
-------------------------------------------------
The queries here are simple and readable, and the repo's established style is
no-ORM. The only real dialect gaps are placeholder style, `datetime('now')`,
and `lastrowid` -- all handled by `Conn` below, so every existing query string
is unchanged. `ON CONFLICT ... DO UPDATE SET ... = excluded.x` already parses
identically on both.

The two DDL blocks are written out per dialect rather than generated. They sit
side by side so drift is visible, and `tests/test_schema_parity.py` asserts the
table and column sets match exactly -- that test, not a shared generator, is
what keeps them honest.

Timestamps are TEXT on both sides, formatted identically ('YYYY-MM-DD HH:MM:SS'
UTC). Postgres TIMESTAMPTZ would be better practice, but psycopg would then
return `datetime` objects where SQLite returns `str`, diverging every JSON
response between dev and production. Parity first; convert once the port is
verified end to end.
"""

import json
import re
import sqlite3
from pathlib import Path

from .config import (ENGINE_DIR, database_url, db_path, is_postgres,
                     require_database)

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS batches (
  batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  source_filename TEXT,
  uploaded_by TEXT,
  uploaded_at TEXT DEFAULT (datetime('now')),
  module_filter TEXT,
  row_count INTEGER,
  quarantined_count INTEGER,
  status TEXT DEFAULT 'loaded',
  scored_rule_version TEXT,
  scored_config_hash TEXT,
  scored_at TEXT
);

CREATE TABLE IF NOT EXISTS bom_rows (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  module TEXT,
  quarantined INTEGER DEFAULT 0,
  quarantine_reason TEXT,
  payload TEXT NOT NULL,
  PRIMARY KEY (batch_id, item_id, stockroom_id)
);

CREATE TABLE IF NOT EXISTS recommendation_result (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  new_max INTEGER, new_rop INTEGER, new_min INTEGER,
  review_required TEXT, action TEXT, reason_code TEXT,
  risk_level TEXT, confidence REAL, explanation TEXT,
  exposure_usd REAL,
  model_version TEXT, rule_version TEXT,
  scored_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (batch_id, item_id, stockroom_id)
);

CREATE TABLE IF NOT EXISTS review_history (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  reviewer TEXT, role TEXT,
  decision TEXT NOT NULL,
  current_max INTEGER, current_rop INTEGER, current_min INTEGER,
  engine_max INTEGER, engine_rop INTEGER, engine_min INTEGER,
  final_max INTEGER, final_rop INTEGER, final_min INTEGER,
  comment TEXT, justification TEXT,
  requires_senior_approval INTEGER DEFAULT 0,
  senior_approved_by TEXT, senior_approved_at TEXT,
  rule_version TEXT, model_version TEXT,
  reviewed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rule_config (
  config_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_version TEXT NOT NULL,
  config_json TEXT NOT NULL,
  active INTEGER DEFAULT 0,
  updated_by TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS machine_criticality_config (
  pattern TEXT PRIMARY KEY,
  criticality TEXT NOT NULL,
  service_level_target REAL,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (datetime('now')),
  user TEXT, role TEXT, method TEXT, path TEXT,
  entity TEXT, entity_id TEXT, detail TEXT
);

-- Memory layer ------------------------------------------------------------
-- Staging. The agent's only write target. Nothing here is a decision yet;
-- it becomes one only when a human confirms it into review_history.
CREATE TABLE IF NOT EXISTS pending_change (
  pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  proposed_max INTEGER, proposed_rop INTEGER, proposed_min INTEGER,
  rationale TEXT,
  source_utterance TEXT NOT NULL,
  parsed_by TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_by TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  confirmed_review_id INTEGER
);

-- Verbatim conversation log. The future ML label corpus
-- (Feature_Selection_TCB_Jan26.md section 12.6: mine comments/justification as
-- LABELS, not features -- they leak as features at OOF AUC 0.987/0.923).
CREATE TABLE IF NOT EXISTS conversation_turn (
  turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  batch_id INTEGER,
  user TEXT, role TEXT,
  question TEXT NOT NULL, answer TEXT,
  tool_calls TEXT,
  provider TEXT, model TEXT,
  ts TEXT DEFAULT (datetime('now'))
);

-- Item-keyed, batch-independent. The monthly roster is a rotating sample, not
-- the part population: an item discussed in January may be absent in February
-- and return in March. Keying on item_id (not batch_id) is what carries that
-- context forward.
CREATE TABLE IF NOT EXISTS item_note (
  note_id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL,
  author TEXT,
  origin_batch_id INTEGER,
  origin_turn_id INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);

-- PRD 5.3. Created now, empty until a model is served, so that predictions are
-- auditable from the first one rather than retrofitted (see app/scoring.py).
CREATE TABLE IF NOT EXISTS model_prediction_log (
  prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  prediction REAL,
  features_hash TEXT,
  predicted_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_item_note_item ON item_note(item_id, active);
CREATE INDEX IF NOT EXISTS ix_pending_change_status ON pending_change(status, item_id);
CREATE INDEX IF NOT EXISTS ix_conversation_turn_ts ON conversation_turn(ts);
"""

# Postgres equivalent. Differences are confined to: IDENTITY vs AUTOINCREMENT,
# the now() expression, and quoting `user` (reserved word in Postgres).
# Flag columns stay INTEGER rather than BOOLEAN so `confirmed=1` / `int(...)`
# comparisons in the routers keep working identically on both dialects.
PG_NOW = "to_char((now() at time zone 'utc'), 'YYYY-MM-DD HH24:MI:SS')"

POSTGRES_DDL = f"""
CREATE TABLE IF NOT EXISTS batches (
  batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  label TEXT NOT NULL,
  source_filename TEXT,
  uploaded_by TEXT,
  uploaded_at TEXT DEFAULT {PG_NOW},
  module_filter TEXT,
  row_count INTEGER,
  quarantined_count INTEGER,
  status TEXT DEFAULT 'loaded',
  scored_rule_version TEXT,
  scored_config_hash TEXT,
  scored_at TEXT
);

CREATE TABLE IF NOT EXISTS bom_rows (
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  module TEXT,
  quarantined INTEGER DEFAULT 0,
  quarantine_reason TEXT,
  payload TEXT NOT NULL,
  PRIMARY KEY (batch_id, item_id, stockroom_id)
);

CREATE TABLE IF NOT EXISTS recommendation_result (
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  new_max INTEGER, new_rop INTEGER, new_min INTEGER,
  review_required TEXT, action TEXT, reason_code TEXT,
  risk_level TEXT, confidence DOUBLE PRECISION, explanation TEXT,
  exposure_usd DOUBLE PRECISION,
  model_version TEXT, rule_version TEXT,
  scored_at TEXT DEFAULT {PG_NOW},
  PRIMARY KEY (batch_id, item_id, stockroom_id)
);

CREATE TABLE IF NOT EXISTS review_history (
  review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  reviewer TEXT, role TEXT,
  decision TEXT NOT NULL,
  current_max INTEGER, current_rop INTEGER, current_min INTEGER,
  engine_max INTEGER, engine_rop INTEGER, engine_min INTEGER,
  final_max INTEGER, final_rop INTEGER, final_min INTEGER,
  comment TEXT, justification TEXT,
  requires_senior_approval INTEGER DEFAULT 0,
  senior_approved_by TEXT, senior_approved_at TEXT,
  rule_version TEXT, model_version TEXT,
  reviewed_at TEXT DEFAULT {PG_NOW}
);

CREATE TABLE IF NOT EXISTS rule_config (
  config_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  rule_version TEXT NOT NULL,
  config_json TEXT NOT NULL,
  active INTEGER DEFAULT 0,
  updated_by TEXT,
  updated_at TEXT DEFAULT {PG_NOW}
);

CREATE TABLE IF NOT EXISTS machine_criticality_config (
  pattern TEXT PRIMARY KEY,
  criticality TEXT NOT NULL,
  service_level_target DOUBLE PRECISION,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT {PG_NOW}
);

CREATE TABLE IF NOT EXISTS audit_log (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts TEXT DEFAULT {PG_NOW},
  "user" TEXT, role TEXT, method TEXT, path TEXT,
  entity TEXT, entity_id TEXT, detail TEXT
);

CREATE TABLE IF NOT EXISTS pending_change (
  pending_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id BIGINT,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  proposed_max INTEGER, proposed_rop INTEGER, proposed_min INTEGER,
  rationale TEXT,
  source_utterance TEXT NOT NULL,
  parsed_by TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_by TEXT,
  created_at TEXT DEFAULT {PG_NOW},
  confirmed_review_id BIGINT
);

CREATE TABLE IF NOT EXISTS conversation_turn (
  turn_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id TEXT,
  batch_id BIGINT,
  "user" TEXT, role TEXT,
  question TEXT NOT NULL, answer TEXT,
  tool_calls TEXT,
  provider TEXT, model TEXT,
  ts TEXT DEFAULT {PG_NOW}
);

CREATE TABLE IF NOT EXISTS item_note (
  note_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL,
  author TEXT,
  origin_batch_id BIGINT,
  origin_turn_id BIGINT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT {PG_NOW}
);

CREATE TABLE IF NOT EXISTS model_prediction_log (
  prediction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  prediction DOUBLE PRECISION,
  features_hash TEXT,
  predicted_at TEXT DEFAULT {PG_NOW}
);

CREATE INDEX IF NOT EXISTS ix_item_note_item ON item_note(item_id, active);
CREATE INDEX IF NOT EXISTS ix_pending_change_status ON pending_change(status, item_id);
CREATE INDEX IF NOT EXISTS ix_conversation_turn_ts ON conversation_turn(ts);
"""

# Tables whose PK is a generated identity -- needed to translate lastrowid.
IDENTITY_PK = {
    "batches": "batch_id",
    "review_history": "review_id",
    "rule_config": "config_id",
    "audit_log": "id",
    "pending_change": "pending_id",
    "conversation_turn": "turn_id",
    "item_note": "note_id",
    "model_prediction_log": "prediction_id",
}

# `user` is a reserved word in Postgres: bare `user` parses as CURRENT_USER, so
# `SELECT DISTINCT user FROM audit_log` would silently return the database role
# instead of the column rather than failing. Quote every bare occurrence.
# The lookarounds keep `modified_user`, `user_id`, `users` and already-quoted
# `"user"` untouched.
_USER_COL = re.compile(r'(?<![\"\w.])user\b(?![\"\w])', re.IGNORECASE)
_QUOTED = re.compile(r"'(?:[^']|'')*'")
_SQLITE_NOW = "datetime('now')"


def _translate(sql: str) -> str:
    """SQLite-flavoured SQL -> Postgres.

    Order matters: datetime('now') *contains* a string literal, so it has to be
    substituted before the literal-preserving pass, or the split hides it.
    The replacement text carries its own literals ('utc', the format string),
    which the second pass then protects.
    """
    if not is_postgres():
        return sql
    sql = sql.replace(_SQLITE_NOW, PG_NOW)

    out, last = [], 0
    for m in _QUOTED.finditer(sql):
        out.append(_translate_fragment(sql[last:m.start()]))
        out.append(m.group(0))          # literals pass through untouched
        last = m.end()
    out.append(_translate_fragment(sql[last:]))
    return "".join(out)


def _translate_fragment(frag: str) -> str:
    return _USER_COL.sub('"user"', frag.replace("?", "%s"))


class _Result:
    """Uniform cursor surface over sqlite3.Cursor and psycopg.Cursor."""

    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self._lastrowid = lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    @property
    def lastrowid(self):
        if self._lastrowid is not None:
            return self._lastrowid
        return self._cur.lastrowid


class Conn:
    """Thin dialect-neutral wrapper. Same call signature the routers already use."""

    def __init__(self, raw, is_postgres: bool):
        self._raw = raw
        self.is_postgres = is_postgres

    def execute(self, sql: str, params=()) -> _Result:
        sql = _translate(sql)
        if self.is_postgres:
            cur = self._raw.cursor()
            cur.execute(sql, tuple(params))
            return _Result(cur)
        return _Result(self._raw.execute(sql, params))

    def executemany(self, sql: str, seq) -> None:
        sql = _translate(sql)
        if self.is_postgres:
            cur = self._raw.cursor()
            cur.executemany(sql, [tuple(r) for r in seq])
            return
        self._raw.executemany(sql, seq)

    def insert_returning(self, sql: str, params, table: str) -> int:
        """INSERT that yields the generated primary key on either dialect."""
        pk = IDENTITY_PK[table]
        if self.is_postgres:
            cur = self._raw.cursor()
            cur.execute(_translate(sql) + f" RETURNING {pk}", tuple(params))
            return cur.fetchone()[pk]
        return self._raw.execute(sql, params).lastrowid

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


def get_conn() -> Conn:
    require_database()
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(database_url(), row_factory=dict_row,
                              autocommit=False)
        return Conn(raw, True)

    raw = sqlite3.connect(db_path())
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Conn(raw, False)


def init_db() -> None:
    conn = get_conn()
    try:
        pg = is_postgres()
        ddl = POSTGRES_DDL if pg else SQLITE_DDL
        if pg:
            cur = conn._raw.cursor()
            cur.execute(ddl)
        else:
            conn._raw.executescript(ddl)
        # Seed rule_config from the analysed engine config on first run.
        n = conn.execute("SELECT COUNT(*) c FROM rule_config").fetchone()["c"]
        if n == 0:
            cfg = json.loads((ENGINE_DIR / "rule_config.json").read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO rule_config (rule_version, config_json, active, updated_by) "
                "VALUES (?,?,1,?)",
                (cfg["rule_version"], json.dumps(cfg), "seed"),
            )
        conn.commit()
    finally:
        conn.close()


def active_config(conn: Conn) -> dict:
    row = conn.execute(
        "SELECT config_json FROM rule_config WHERE active=1 ORDER BY config_id DESC LIMIT 1"
    ).fetchone()
    cfg = json.loads(row["config_json"])
    # Merge engineer-confirmed criticality (PRD 5.3). Only confirmed rows count:
    # an agent/LLM may WRITE a proposal, a human must confirm before the engine
    # reads it.
    crit = {
        r["pattern"]: r["criticality"]
        for r in conn.execute(
            "SELECT pattern, criticality FROM machine_criticality_config WHERE confirmed=1"
        )
    }
    if crit:
        cfg = {**cfg, "machine_criticality": {**cfg.get("machine_criticality", {}), **crit}}
    return cfg
