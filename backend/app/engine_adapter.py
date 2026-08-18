"""Bridge between the API and the analysed rule engine.

The engine lives in analysis/engine/engine.py and is the single source of truth
for all decision logic (rule_version 0.2.0-tcb, backtested in
docs/Engine_Backtest_TCB_Jan26.md). The backend adds persistence and workflow
around it -- it never re-implements rules.
"""

import hashlib
import json
import os
import sys

import pandas as pd

from .config import ENGINE_DIR
from .db import Conn, active_config
from . import engine_statistical

sys.path.insert(0, str(ENGINE_DIR))
import engine  # noqa: E402  (analysis/engine/engine.py)


def _select_engine():
    """Statistical sizing engine, unless BOM_ENGINE=rules pins the legacy one
    (the workflow test-suite was written against the rule engine)."""
    return engine if os.environ.get("BOM_ENGINE", "").strip().lower() == "rules" else engine_statistical


def load_batch_df(conn: Conn, batch_id: int) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT payload FROM bom_rows WHERE batch_id=? AND quarantined=0", (batch_id,)
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(r["payload"]) for r in rows])


def score_batch(conn: Conn, batch_id: int) -> dict:
    df = load_batch_df(conn, batch_id)
    if df.empty:
        raise ValueError(f"Batch {batch_id} has no scoreable rows")

    cfg = active_config(conn)
    cfg_hash = hashlib.sha256(
        json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]

    res = _select_engine().run(df, cfg)

    stk = df.get("stockroom_id", pd.Series("", index=df.index)).astype(str).str.strip()

    # review_history -> recommendation_result is ON DELETE RESTRICT by design: a
    # row an engineer already decided on must not disappear under them. So a
    # re-score (the /config/rules workflow) replaces only the un-reviewed rows;
    # a reviewed item keeps the recommendation its reviewer actually saw, which
    # is also what review_history's engine_* columns record. Deleting the batch
    # wholesale here used to raise IntegrityError -> HTTP 500.
    reviewed = {(r["item_id"], r["stockroom_id"]) for r in conn.execute(
        "SELECT DISTINCT item_id, stockroom_id FROM review_history WHERE batch_id=?",
        (batch_id,)).fetchall()}
    conn.execute(
        "DELETE FROM recommendation_result WHERE batch_id=? AND NOT EXISTS ("
        " SELECT 1 FROM review_history h"
        " WHERE h.batch_id=recommendation_result.batch_id"
        " AND h.item_id=recommendation_result.item_id"
        " AND h.stockroom_id=recommendation_result.stockroom_id)", (batch_id,))
    # Triage signals: the statistical engine emits these; the legacy rule engine
    # does not, so default them rather than KeyError on a rules re-score.
    for c in ("route", "consumable", "agreement"):
        if c not in res.columns:
            res[c] = ""
    # NOTE: not itertuples() -- it renames underscore-prefixed columns
    # (_exposure_usd), which would silently mis-read fields.
    cols = ["item_id", "factory_recommended_new_max", "factory_recommended_new_rop",
            "factory_recommended_new_min", "review_required",
            "factory_recommendation_action", "reason_code", "risk_level",
            "confidence_score", "explanation", "_exposure_usd",
            "model_version", "rule_version", "route", "consumable", "agreement"]
    payload = [
        (batch_id, str(v[0]), stk.iloc[i], int(v[1]), int(v[2]), int(v[3]),
         str(v[4]), str(v[5]), str(v[6]), str(v[7]), float(v[8]), str(v[9]),
         float(v[10]), str(v[11]), str(v[12]), str(v[13]), str(v[14]), str(v[15]))
        for i, v in enumerate(res[cols].to_numpy())
    ]
    payload = [p for p in payload if (p[1], p[2]) not in reviewed]
    conn.executemany(
        "INSERT INTO recommendation_result (batch_id, item_id, stockroom_id, new_max, "
        "new_rop, new_min, review_required, action, reason_code, risk_level, confidence, "
        "explanation, exposure_usd, model_version, rule_version, route, consumable, agreement) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    conn.execute(
        "UPDATE batches SET status='scored', scored_rule_version=?, scored_config_hash=?, "
        "scored_at=datetime('now') WHERE batch_id=?",
        (cfg["rule_version"], cfg_hash, batch_id))
    conn.commit()

    review_y = int((res.review_required == "Y").sum())
    codes: dict[str, int] = {}
    for cs in res.reason_code:
        for c in cs.split(","):
            codes[c] = codes.get(c, 0) + 1
    return {
        "batch_id": batch_id,
        "rows_scored": len(res),
        "rows_preserved": len(reviewed),   # already reviewed; not re-scored
        "review_required_Y": review_y,
        "review_required_N": len(res) - review_y,
        "actions": res.factory_recommendation_action.value_counts().to_dict(),
        "risk_levels": res.risk_level.value_counts().to_dict(),
        "top_reason_codes": dict(sorted(codes.items(), key=lambda kv: -kv[1])[:8]),
        "rule_version": cfg["rule_version"],
        "config_hash": cfg_hash,
    }
