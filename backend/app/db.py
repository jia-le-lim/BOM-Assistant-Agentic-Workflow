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
import threading
from pathlib import Path

from .config import (ENGINE_DIR, database_url, db_path, is_postgres,
                     require_database, use_rest)

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

# Every foreign key in the schema, as data. The DDL below declares them inline
# for a fresh database; this list is what `_ensure_foreign_keys()` uses to bring
# an ALREADY EXISTING Postgres database up to the same shape, since
# CREATE TABLE IF NOT EXISTS is a no-op there. Names match the constraints
# already on the Supabase project, so the migration is a no-op against it.
# tests/test_foreign_keys.py asserts this list and the two DDL blocks agree.
FOREIGN_KEYS = [
    ("bom_rows_batch_fk", "bom_rows", ("batch_id",),
     "batches", ("batch_id",), "CASCADE"),
    ("recommendation_result_batch_fk", "recommendation_result", ("batch_id",),
     "batches", ("batch_id",), "CASCADE"),
    ("recommendation_result_row_fk", "recommendation_result",
     ("batch_id", "item_id", "stockroom_id"),
     "bom_rows", ("batch_id", "item_id", "stockroom_id"), "CASCADE"),
    ("review_history_batch_fk", "review_history", ("batch_id",),
     "batches", ("batch_id",), "RESTRICT"),
    ("review_history_result_fk", "review_history",
     ("batch_id", "item_id", "stockroom_id"),
     "recommendation_result", ("batch_id", "item_id", "stockroom_id"),
     "RESTRICT"),
    ("pending_change_batch_fk", "pending_change", ("batch_id",),
     "batches", ("batch_id",), "RESTRICT"),
    ("pending_change_review_fk", "pending_change", ("confirmed_review_id",),
     "review_history", ("review_id",), "RESTRICT"),
    ("conversation_turn_batch_fk", "conversation_turn", ("batch_id",),
     "batches", ("batch_id",), "SET NULL"),
    ("item_note_batch_fk", "item_note", ("origin_batch_id",),
     "batches", ("batch_id",), "SET NULL"),
    ("item_note_turn_fk", "item_note", ("origin_turn_id",),
     "conversation_turn", ("turn_id",), "SET NULL"),
    ("model_prediction_log_batch_fk", "model_prediction_log", ("batch_id",),
     "batches", ("batch_id",), "CASCADE"),
    ("triage_result_recommendation_fk", "triage_result",
     ("batch_id", "item_id", "stockroom_id"),
     "recommendation_result", ("batch_id", "item_id", "stockroom_id"),
     "CASCADE"),
    ("similarity_result_recommendation_fk", "similarity_result",
     ("batch_id", "item_id", "stockroom_id"),
     "recommendation_result", ("batch_id", "item_id", "stockroom_id"),
     "CASCADE"),
    ("similarity_neighbour_result_fk", "similarity_neighbour",
     ("batch_id", "item_id", "stockroom_id"),
     "similarity_result", ("batch_id", "item_id", "stockroom_id"),
     "CASCADE"),
    ("assist_result_recommendation_fk", "assist_result",
     ("batch_id", "item_id", "stockroom_id"),
     "recommendation_result", ("batch_id", "item_id", "stockroom_id"),
     "CASCADE"),
]

# Postgres does not index the referencing side of a foreign key, so every
# parent delete and every RESTRICT check would otherwise scan the child table
# (bom_rows is 2.8k rows for one month of TCB, 17k in the raw workbook).
# Where the child's PRIMARY KEY already leads with the FK columns
# (bom_rows, recommendation_result composite) no extra index is needed.
# Identical on SQLite so the two dialects stay comparable.
FK_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_bom_rows_batch ON bom_rows(batch_id);
CREATE INDEX IF NOT EXISTS ix_recommendation_result_batch
  ON recommendation_result(batch_id);
CREATE INDEX IF NOT EXISTS ix_review_history_batch ON review_history(batch_id);
CREATE INDEX IF NOT EXISTS ix_review_history_result
  ON review_history(batch_id, item_id, stockroom_id);
CREATE INDEX IF NOT EXISTS ix_review_history_item ON review_history(item_id);
CREATE INDEX IF NOT EXISTS ix_pending_change_batch ON pending_change(batch_id);
CREATE INDEX IF NOT EXISTS ix_pending_change_review
  ON pending_change(confirmed_review_id);
CREATE INDEX IF NOT EXISTS ix_conversation_turn_batch
  ON conversation_turn(batch_id);
CREATE INDEX IF NOT EXISTS ix_item_note_batch ON item_note(origin_batch_id);
CREATE INDEX IF NOT EXISTS ix_item_note_turn ON item_note(origin_turn_id);
CREATE INDEX IF NOT EXISTS ix_model_prediction_log_batch
  ON model_prediction_log(batch_id);
"""

# Full-text search over engineer free text. GIN/to_tsvector is Postgres-only;
# SQLite would need FTS5 virtual tables, and nothing queries it there.
PG_ONLY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_item_note_fts
  ON item_note USING gin (to_tsvector('english', note));
CREATE INDEX IF NOT EXISTS ix_review_history_comment_fts
  ON review_history USING gin (to_tsvector('english', coalesce(comment, '')));
"""

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
  batch_id INTEGER NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  module TEXT,
  quarantined INTEGER DEFAULT 0,
  quarantine_reason TEXT,
  payload TEXT NOT NULL,
  PRIMARY KEY (batch_id, item_id, stockroom_id)
);

CREATE TABLE IF NOT EXISTS recommendation_result (
  batch_id INTEGER NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  new_max INTEGER, new_rop INTEGER, new_min INTEGER,
  review_required TEXT, action TEXT, reason_code TEXT,
  risk_level TEXT, confidence REAL, explanation TEXT,
  exposure_usd REAL,
  model_version TEXT, rule_version TEXT,
  route TEXT DEFAULT '', consumable TEXT DEFAULT '', agreement TEXT DEFAULT '',
  agreement_source TEXT DEFAULT '',
  scored_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES bom_rows(batch_id, item_id, stockroom_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_history (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL REFERENCES batches(batch_id) ON DELETE RESTRICT,
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
  reviewed_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE RESTRICT
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

-- What KIND of part this is, matched against item_desc. Engineer-owned on the
-- same terms as machine_criticality_config: anyone with review rights may
-- PROPOSE a rule, only an approver confirms, and similarity reads confirmed
-- rows only. Lower priority wins, so specific rules sit above generic ones.
CREATE TABLE IF NOT EXISTS part_category_config (
  pattern TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 500,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now'))
);

-- How much stock a DORMANT part keeps. Engineer-owned on the same terms as
-- part_category_config: review rights may PROPOSE, an approver CONFIRMS, and
-- the engine reads confirmed rows only. Lower priority wins; item beats
-- category beats default. Seeded from what engineers actually decided -- the
-- engine's own zero is wrong on 1,344 of 8,343 dormant rows in the TCB history.
CREATE TABLE IF NOT EXISTS dormant_rule_config (
  rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,                  -- 'item' | 'category' | 'default'
  match_key TEXT NOT NULL DEFAULT '',   -- item_id, category name, or ''
  criticality TEXT NOT NULL DEFAULT '', -- '' = any, else h|m|l|d
  policy TEXT NOT NULL,                 -- 'hold_current' | 'fixed_qty' | 'zero'
  fixed_qty INTEGER,
  priority INTEGER NOT NULL DEFAULT 500,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT (datetime('now')),
  UNIQUE (scope, match_key, criticality)
);

-- Advisory review assistance for the active/dying rows. The verdict is decided
-- by assist/rules.py, not by a model: same evidence in, same verdict out, so it
-- can be replayed and backtested. `narrative` is the only generated field and
-- explains a decision that was already made. Never an input to sizing.
CREATE TABLE IF NOT EXISTS assist_result (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL CHECK (
    verdict IN ('flag_for_review', 'bulk_accept_candidate', 'needs_context')),
  reasons_json TEXT NOT NULL DEFAULT '[]',
  narrative TEXT,
  -- The number to put in front of the reviewer. Written by assist/rules.suggest
  -- (deterministic), never by the model, and never applied without a decision.
  suggested_max INTEGER,
  suggested_rop INTEGER,
  suggestion_basis TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  model_version TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  assisted_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
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
  batch_id INTEGER REFERENCES batches(batch_id) ON DELETE RESTRICT,
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
    REFERENCES review_history(review_id) ON DELETE RESTRICT
);

-- Verbatim conversation log. The future ML label corpus
-- (Feature_Selection_TCB_Jan26.md section 12.6: mine comments/justification as
-- LABELS, not features -- they leak as features at OOF AUC 0.987/0.923).
CREATE TABLE IF NOT EXISTS conversation_turn (
  turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT,
  batch_id INTEGER REFERENCES batches(batch_id) ON DELETE SET NULL,
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
-- Both parents are SET NULL, never CASCADE: a note must outlive the batch and
-- the conversation it came from. That is the whole point of keying on item_id
-- -- the roster rotates, so the note has to survive the roster.
CREATE TABLE IF NOT EXISTS item_note (
  note_id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL,
  author TEXT,
  origin_batch_id INTEGER REFERENCES batches(batch_id) ON DELETE SET NULL,
  origin_turn_id INTEGER
    REFERENCES conversation_turn(turn_id) ON DELETE SET NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);

-- PRD 5.3. Created now, empty until a model is served, so that predictions are
-- auditable from the first one rather than retrofitted (see app/scoring.py).
CREATE TABLE IF NOT EXISTS model_prediction_log (
  prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  prediction REAL,
  features_hash TEXT,
  predicted_at TEXT DEFAULT (datetime('now'))
);

-- Advisory explanations only. Human decisions remain in review_history.
CREATE TABLE IF NOT EXISTS triage_result (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  triage_tier TEXT NOT NULL CHECK (
    triage_tier IN ('clear_candidate', 'review', 'escalate')),
  priority_score REAL NOT NULL DEFAULT 0,
  rationale TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  focus_question TEXT,
  history_narrative TEXT,
  demand_narrative TEXT,
  procurement_narrative TEXT,
  sources_json TEXT NOT NULL DEFAULT '[]',
  provider TEXT,
  model TEXT,
  triaged_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

-- Advisory peer evidence. Never an input to sizing, never exported.
-- The engine calculates; these are historical analogues for the engineer.
CREATE TABLE IF NOT EXISTS similarity_result (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  similarity_model_version TEXT NOT NULL,
  neighbour_count INTEGER NOT NULL DEFAULT 0,
  pool_size INTEGER NOT NULL DEFAULT 0,
  nearest_distance REAL,
  outlier_score REAL NOT NULL DEFAULT 1,
  is_outlier INTEGER NOT NULL DEFAULT 1,
  historical_override_rate REAL,
  historical_upward_override_rate REAL,
  historical_high_risk_rate REAL,
  analogue_max_median INTEGER,
  analogue_max_p25 INTEGER,
  analogue_max_p75 INTEGER,
  analogue_rop_median INTEGER,
  analogue_min_median INTEGER,
  part_category TEXT NOT NULL DEFAULT '',
  advisory_codes TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  generated_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

-- One row per retrieved peer. similarity_reasons is business language only --
-- it must never embed a supplier or machine_type VALUE (redact.py masks by key
-- name, so a value inside a free-text column would bypass LLM_REDACT_PROMPTS).
CREATE TABLE IF NOT EXISTS similarity_neighbour (
  batch_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  neighbour_rank INTEGER NOT NULL,
  neighbour_item_id TEXT NOT NULL,
  neighbour_stockroom_id TEXT NOT NULL DEFAULT '',
  neighbour_batch_id INTEGER NOT NULL,
  distance REAL NOT NULL,
  similarity_reasons TEXT NOT NULL DEFAULT '',
  neighbour_decision TEXT,
  neighbour_final_max INTEGER,
  neighbour_final_rop INTEGER,
  neighbour_final_min INTEGER,
  neighbour_engine_max INTEGER,
  neighbour_risk_level TEXT,
  neighbour_reason_code TEXT,
  neighbour_justification TEXT,
  neighbour_comment TEXT,
  PRIMARY KEY (batch_id, item_id, stockroom_id, neighbour_rank),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES similarity_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_item_note_item ON item_note(item_id, active);
CREATE INDEX IF NOT EXISTS ix_pending_change_status ON pending_change(status, item_id);
CREATE INDEX IF NOT EXISTS ix_conversation_turn_ts ON conversation_turn(ts);
CREATE INDEX IF NOT EXISTS ix_triage_result_tier
  ON triage_result(batch_id, triage_tier, priority_score);
CREATE INDEX IF NOT EXISTS ix_similarity_result_outlier
  ON similarity_result(batch_id, is_outlier, outlier_score);
""" + FK_INDEX_DDL

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
  batch_id BIGINT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  module TEXT,
  quarantined INTEGER DEFAULT 0,
  quarantine_reason TEXT,
  payload TEXT NOT NULL,
  PRIMARY KEY (batch_id, item_id, stockroom_id)
);

CREATE TABLE IF NOT EXISTS recommendation_result (
  batch_id BIGINT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  new_max INTEGER, new_rop INTEGER, new_min INTEGER,
  review_required TEXT, action TEXT, reason_code TEXT,
  risk_level TEXT, confidence DOUBLE PRECISION, explanation TEXT,
  exposure_usd DOUBLE PRECISION,
  model_version TEXT, rule_version TEXT,
  route TEXT DEFAULT '', consumable TEXT DEFAULT '', agreement TEXT DEFAULT '',
  agreement_source TEXT DEFAULT '',
  scored_at TEXT DEFAULT {PG_NOW},
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES bom_rows(batch_id, item_id, stockroom_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_history (
  review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES batches(batch_id) ON DELETE RESTRICT,
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
  reviewed_at TEXT DEFAULT {PG_NOW},
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE RESTRICT
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

-- What KIND of part this is, matched against item_desc. Engineer-owned on the
-- same terms as machine_criticality_config: anyone with review rights may
-- PROPOSE a rule, only an approver confirms, and similarity reads confirmed
-- rows only. Lower priority wins, so specific rules sit above generic ones.
CREATE TABLE IF NOT EXISTS part_category_config (
  pattern TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 500,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT {PG_NOW}
);

-- How much stock a DORMANT part keeps. Engineer-owned on the same terms as
-- part_category_config: review rights may PROPOSE, an approver CONFIRMS, and
-- the engine reads confirmed rows only. Lower priority wins; item beats
-- category beats default. Seeded from what engineers actually decided -- the
-- engine's own zero is wrong on 1,344 of 8,343 dormant rows in the TCB history.
CREATE TABLE IF NOT EXISTS dormant_rule_config (
  rule_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  scope TEXT NOT NULL,                  -- 'item' | 'category' | 'default'
  match_key TEXT NOT NULL DEFAULT '',   -- item_id, category name, or ''
  criticality TEXT NOT NULL DEFAULT '', -- '' = any, else h|m|l|d
  policy TEXT NOT NULL,                 -- 'hold_current' | 'fixed_qty' | 'zero'
  fixed_qty INTEGER,
  priority INTEGER NOT NULL DEFAULT 500,
  set_by TEXT,
  confirmed_by TEXT,
  confirmed INTEGER DEFAULT 0,
  updated_at TEXT DEFAULT {PG_NOW},
  UNIQUE (scope, match_key, criticality)
);

-- Advisory review assistance for the active/dying rows. The verdict is decided
-- by assist/rules.py, not by a model: same evidence in, same verdict out, so it
-- can be replayed and backtested. `narrative` is the only generated field and
-- explains a decision that was already made. Never an input to sizing.
CREATE TABLE IF NOT EXISTS assist_result (
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  verdict TEXT NOT NULL CHECK (
    verdict IN ('flag_for_review', 'bulk_accept_candidate', 'needs_context')),
  reasons_json TEXT NOT NULL DEFAULT '[]',
  narrative TEXT,
  -- The number to put in front of the reviewer. Written by assist/rules.suggest
  -- (deterministic), never by the model, and never applied without a decision.
  suggested_max INTEGER,
  suggested_rop INTEGER,
  suggestion_basis TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '{{}}',
  model_version TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  assisted_at TEXT DEFAULT {PG_NOW},
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_log (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts TEXT DEFAULT {PG_NOW},
  "user" TEXT, role TEXT, method TEXT, path TEXT,
  entity TEXT, entity_id TEXT, detail TEXT
);

CREATE TABLE IF NOT EXISTS pending_change (
  pending_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id BIGINT REFERENCES batches(batch_id) ON DELETE RESTRICT,
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
    REFERENCES review_history(review_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS conversation_turn (
  turn_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id TEXT,
  batch_id BIGINT REFERENCES batches(batch_id) ON DELETE SET NULL,
  "user" TEXT, role TEXT,
  question TEXT NOT NULL, answer TEXT,
  tool_calls TEXT,
  provider TEXT, model TEXT,
  ts TEXT DEFAULT {PG_NOW}
);

-- Both parents are SET NULL, never CASCADE: a note must outlive the batch and
-- the conversation it came from. That is the whole point of keying on item_id
-- -- the roster rotates, so the note has to survive the roster.
CREATE TABLE IF NOT EXISTS item_note (
  note_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL,
  author TEXT,
  origin_batch_id BIGINT REFERENCES batches(batch_id) ON DELETE SET NULL,
  origin_turn_id BIGINT
    REFERENCES conversation_turn(turn_id) ON DELETE SET NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT {PG_NOW}
);

CREATE TABLE IF NOT EXISTS model_prediction_log (
  prediction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES batches(batch_id) ON DELETE CASCADE,
  item_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  prediction DOUBLE PRECISION,
  features_hash TEXT,
  predicted_at TEXT DEFAULT {PG_NOW}
);

-- Advisory explanations only. Human decisions remain in review_history.
CREATE TABLE IF NOT EXISTS triage_result (
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  triage_tier TEXT NOT NULL CHECK (
    triage_tier IN ('clear_candidate', 'review', 'escalate')),
  priority_score DOUBLE PRECISION NOT NULL DEFAULT 0,
  rationale TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  focus_question TEXT,
  history_narrative TEXT,
  demand_narrative TEXT,
  procurement_narrative TEXT,
  sources_json TEXT NOT NULL DEFAULT '[]',
  provider TEXT,
  model TEXT,
  triaged_at TEXT DEFAULT {PG_NOW},
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

-- Advisory peer evidence. Never an input to sizing, never exported.
-- The engine calculates; these are historical analogues for the engineer.
CREATE TABLE IF NOT EXISTS similarity_result (
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  similarity_model_version TEXT NOT NULL,
  neighbour_count INTEGER NOT NULL DEFAULT 0,
  pool_size INTEGER NOT NULL DEFAULT 0,
  nearest_distance DOUBLE PRECISION,
  outlier_score DOUBLE PRECISION NOT NULL DEFAULT 1,
  is_outlier INTEGER NOT NULL DEFAULT 1,
  historical_override_rate DOUBLE PRECISION,
  historical_upward_override_rate DOUBLE PRECISION,
  historical_high_risk_rate DOUBLE PRECISION,
  analogue_max_median INTEGER,
  analogue_max_p25 INTEGER,
  analogue_max_p75 INTEGER,
  analogue_rop_median INTEGER,
  analogue_min_median INTEGER,
  part_category TEXT NOT NULL DEFAULT '',
  advisory_codes TEXT NOT NULL DEFAULT '',
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  generated_at TEXT DEFAULT {PG_NOW},
  PRIMARY KEY (batch_id, item_id, stockroom_id),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES recommendation_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

-- One row per retrieved peer. similarity_reasons is business language only --
-- it must never embed a supplier or machine_type VALUE (redact.py masks by key
-- name, so a value inside a free-text column would bypass LLM_REDACT_PROMPTS).
CREATE TABLE IF NOT EXISTS similarity_neighbour (
  batch_id BIGINT NOT NULL,
  item_id TEXT NOT NULL,
  stockroom_id TEXT NOT NULL DEFAULT '',
  neighbour_rank INTEGER NOT NULL,
  neighbour_item_id TEXT NOT NULL,
  neighbour_stockroom_id TEXT NOT NULL DEFAULT '',
  neighbour_batch_id BIGINT NOT NULL,
  distance DOUBLE PRECISION NOT NULL,
  similarity_reasons TEXT NOT NULL DEFAULT '',
  neighbour_decision TEXT,
  neighbour_final_max INTEGER,
  neighbour_final_rop INTEGER,
  neighbour_final_min INTEGER,
  neighbour_engine_max INTEGER,
  neighbour_risk_level TEXT,
  neighbour_reason_code TEXT,
  neighbour_justification TEXT,
  neighbour_comment TEXT,
  PRIMARY KEY (batch_id, item_id, stockroom_id, neighbour_rank),
  FOREIGN KEY (batch_id, item_id, stockroom_id)
    REFERENCES similarity_result(batch_id, item_id, stockroom_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_item_note_item ON item_note(item_id, active);
CREATE INDEX IF NOT EXISTS ix_pending_change_status ON pending_change(status, item_id);
CREATE INDEX IF NOT EXISTS ix_conversation_turn_ts ON conversation_turn(ts);
CREATE INDEX IF NOT EXISTS ix_triage_result_tier
  ON triage_result(batch_id, triage_tier, priority_score);
CREATE INDEX IF NOT EXISTS ix_similarity_result_outlier
  ON similarity_result(batch_id, is_outlier, outlier_score);
""" + FK_INDEX_DDL + PG_ONLY_INDEX_DDL

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
    "dormant_rule_config": "rule_id",
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

    def __init__(self, cur=None, lastrowid=None, rows=None):
        self._cur = cur
        self._lastrowid = lastrowid
        self._rows = rows
        self._index = 0

    def fetchone(self):
        if self._rows is not None:
            if self._index >= len(self._rows):
                return None
            row = self._rows[self._index]
            self._index += 1
            return row
        return self._cur.fetchone()

    def fetchall(self):
        if self._rows is not None:
            rows = self._rows[self._index:]
            self._index = len(self._rows)
            return rows
        return self._cur.fetchall()

    def __iter__(self):
        if self._rows is not None:
            rows = self._rows[self._index:]
            self._index = len(self._rows)
            return iter(rows)
        return iter(self._cur)

    @property
    def lastrowid(self):
        if self._cur is None or self._lastrowid is not None:
            return self._lastrowid
        return self._cur.lastrowid


class Conn:
    """Thin dialect-neutral wrapper. Same call signature the routers already use."""

    def __init__(self, raw, is_postgres: bool):
        self._raw = raw
        self.is_postgres = is_postgres
        self._lock = threading.RLock()

    def execute(self, sql: str, params=()) -> _Result:
        sql = _translate(sql)
        if self.is_postgres:
            cur = self._raw.cursor()
            cur.execute(sql, tuple(params))
            return _Result(cur)
        with self._lock:
            cur = self._raw.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            return _Result(lastrowid=cur.lastrowid, rows=rows)

    def executemany(self, sql: str, seq) -> None:
        sql = _translate(sql)
        if self.is_postgres:
            cur = self._raw.cursor()
            cur.executemany(sql, [tuple(r) for r in seq])
            return
        with self._lock:
            self._raw.executemany(sql, seq)

    def insert_returning(self, sql: str, params, table: str) -> int:
        """INSERT that yields the generated primary key on either dialect."""
        pk = IDENTITY_PK[table]
        if self.is_postgres:
            cur = self._raw.cursor()
            cur.execute(_translate(sql) + f" RETURNING {pk}", tuple(params))
            return cur.fetchone()[pk]
        with self._lock:
            return self._raw.execute(sql, params).lastrowid

    def commit(self) -> None:
        with self._lock:
            self._raw.commit()

    def rollback(self) -> None:
        with self._lock:
            self._raw.rollback()

    def close(self) -> None:
        with self._lock:
            self._raw.close()


def get_conn() -> Conn:
    require_database()
    if use_rest():
        # Duck-typed, not a Conn subclass: it implements the same five methods
        # over HTTP and shares none of the psycopg/sqlite machinery.
        from .rest_conn import connect as rest_connect
        return rest_connect()            # type: ignore[return-value]
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(database_url(), row_factory=dict_row,
                              autocommit=False)
        return Conn(raw, True)

    # LangGraph fan-out runs specialist nodes in worker threads. Those nodes
    # only read in parallel and join before persist, so one serialized SQLite
    # connection is sufficient; the default same-thread guard is not.
    raw = sqlite3.connect(db_path(), check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Conn(raw, False)


class SchemaMigrationFailed(RuntimeError):
    pass


def _ensure_foreign_keys(conn: Conn) -> list[str]:
    """Add any FOREIGN_KEYS entry missing from an existing Postgres database.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    without this a database created before a constraint was declared keeps the
    constraint-free schema for good -- and the suite, which builds a fresh tmp
    database every run, can never notice. That is code and production being two
    separate sources of truth, which is what F10 warns about.

    SQLite has no ALTER TABLE ADD CONSTRAINT. It is the test backend and its
    databases are always created from scratch, so there is nothing to migrate.
    """
    added = []
    for name, child, cols, parent, pcols, action in FOREIGN_KEYS:
        exists = conn.execute(
            "SELECT 1 AS ok FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_class p ON p.oid = c.confrelid "
            "WHERE c.contype='f' AND t.relname=? AND p.relname=?",
            (child, parent)).fetchone()
        if exists:
            continue
        stmt = (f"ALTER TABLE {child} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({', '.join(cols)}) "
                f"REFERENCES {parent} ({', '.join(pcols)}) ON DELETE {action}")
        try:
            conn.execute(stmt)
        except Exception as e:      # orphan rows, or no rights to ALTER
            raise SchemaMigrationFailed(
                f"could not add {name}: {e}\n"
                f"Run backend/scripts/check_referential_integrity.py first -- "
                f"an existing row that violates the constraint has to be "
                f"resolved before it can be applied.") from e
        added.append(name)
    return added


def _ensure_columns(conn) -> None:
    """Additive migration for DBs created before a column was added.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so a dev/prod DB
    that predates route/consumable/agreement or Phase-2 procurement would 500
    on the next request. New columns are nullable/default-empty.
    """
    wanted = ("route", "consumable", "agreement", "agreement_source")
    if is_postgres() or use_rest():
        for col in wanted:
            conn.execute("ALTER TABLE recommendation_result "
                         f"ADD COLUMN IF NOT EXISTS {col} TEXT DEFAULT ''")
        conn.execute("ALTER TABLE triage_result ADD COLUMN IF NOT EXISTS "
                     "procurement_narrative TEXT")
        # similarity_result already exists on the live project, so CREATE TABLE
        # IF NOT EXISTS will never add this one.
        conn.execute("ALTER TABLE similarity_result ADD COLUMN IF NOT EXISTS "
                     "part_category TEXT DEFAULT ''")
        for col, decl in (("suggested_max", "INTEGER"), ("suggested_rop", "INTEGER"),
                          ("suggestion_basis", "TEXT DEFAULT ''")):
            conn.execute("ALTER TABLE assist_result "
                         f"ADD COLUMN IF NOT EXISTS {col} {decl}")
        return
    existing = {r["name"] for r in conn.execute(
        "PRAGMA table_info(recommendation_result)")}
    for col in wanted:
        if col not in existing:
            conn.execute("ALTER TABLE recommendation_result "
                         f"ADD COLUMN {col} TEXT DEFAULT ''")
    triage_cols = {r["name"] for r in conn.execute(
        "PRAGMA table_info(triage_result)")}
    if "procurement_narrative" not in triage_cols:
        conn.execute("ALTER TABLE triage_result ADD COLUMN procurement_narrative TEXT")
    sim_cols = {r["name"] for r in conn.execute(
        "PRAGMA table_info(similarity_result)")}
    if "part_category" not in sim_cols:
        conn.execute("ALTER TABLE similarity_result "
                     "ADD COLUMN part_category TEXT DEFAULT ''")
    assist_cols = {r["name"] for r in conn.execute(
        "PRAGMA table_info(assist_result)")}
    for col, decl in (("suggested_max", "INTEGER"), ("suggested_rop", "INTEGER"),
                      ("suggestion_basis", "TEXT DEFAULT ''")):
        if col not in assist_cols:
            conn.execute(f"ALTER TABLE assist_result ADD COLUMN {col} {decl}")


def init_db() -> None:
    conn = get_conn()
    try:
        pg = is_postgres()
        ddl = POSTGRES_DDL if pg else SQLITE_DDL
        if use_rest():
            # exec_sql runs a multi-statement string, so the whole DDL goes in
            # one call. No _raw cursor exists on this transport.
            conn.execute(ddl)
            _ensure_foreign_keys(conn)
        elif pg:
            cur = conn._raw.cursor()
            cur.execute(ddl)
            _ensure_foreign_keys(conn)
        else:
            conn._raw.executescript(ddl)
        _ensure_columns(conn)
        # Seed rule_config from the analysed engine config on first run.
        n = conn.execute("SELECT COUNT(*) c FROM rule_config").fetchone()["c"]
        if n == 0:
            cfg = json.loads((ENGINE_DIR / "rule_config.json").read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO rule_config (rule_version, config_json, active, updated_by) "
                "VALUES (?,?,1,?)",
                (cfg["rule_version"], json.dumps(cfg), "seed"),
            )
        # Seed the part-category lexicon, confirmed. An empty criticality table
        # means "fall back to the source column", which is a safe default; an
        # empty lexicon means the similarity constraint silently does nothing,
        # which is worse than a default an engineer can correct. rule_config is
        # seeded active=1 for the same reason.
        n = conn.execute(
            "SELECT COUNT(*) c FROM part_category_config").fetchone()["c"]
        if n == 0:
            from .part_category import DEFAULT_RULES
            # Every value is a placeholder, including the constants: the REST
            # transport rebuilds the VALUES clause from the row width, so a
            # literal written inline is silently dropped (rest_conn.executemany).
            conn.executemany(
                "INSERT INTO part_category_config (pattern, category, priority, "
                "set_by, confirmed, confirmed_by) VALUES (?,?,?,?,?,?)",
                [(pat, cat, pri, "seed", 1, "seed")
                 for pri, cat, pat in DEFAULT_RULES])
        conn.commit()
    finally:
        conn.close()


def stored_config(conn: Conn) -> dict:
    """The active rule config exactly as persisted -- no criticality merge.

    Anything that WRITES a config back must start here. active_config() returns
    a view with machine_criticality_config folded in for the engine; persisting
    that view would bake a snapshot of a mutable table into the immutable
    rule_version stamp, and later criticality edits would then be ignored.
    """
    row = conn.execute(
        "SELECT config_json FROM rule_config WHERE active=1 ORDER BY config_id DESC LIMIT 1"
    ).fetchone()
    return json.loads(row["config_json"])


def active_config(conn: Conn) -> dict:
    cfg = stored_config(conn)
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
