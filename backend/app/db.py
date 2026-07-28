"""SQLite data mart stand-in.

PRD section 5.3 specifies item_master / inventory_snapshot / consumption_snapshot
as separate normalized tables. The scaffold deviates deliberately: the source is a
single 116-column workbook, so rows are stored whole (JSON payload + indexed key
columns) in `bom_rows`, preserving full input fidelity for the engine. Normalize
when direct WINGS/SFM connectors exist. Documented in Backend_Scaffold_Notes.md.
"""

import json
import sqlite3
from pathlib import Path

from .config import ENGINE_DIR, db_path

DDL = """
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
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(DDL)
        # Seed rule_config from the analysed engine config on first run.
        n = conn.execute("SELECT COUNT(*) c FROM rule_config").fetchone()["c"]
        if n == 0:
            cfg = json.loads((ENGINE_DIR / "rule_config.json").read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO rule_config (rule_version, config_json, active, updated_by) VALUES (?,?,1,?)",
                (cfg["rule_version"], json.dumps(cfg), "seed"),
            )
        conn.commit()
    finally:
        conn.close()


def active_config(conn: sqlite3.Connection) -> dict:
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
