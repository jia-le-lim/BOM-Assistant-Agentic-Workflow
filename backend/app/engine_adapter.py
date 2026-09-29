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
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from .config import ENGINE_DIR
from .db import Conn, active_config
from .account_settings import batch_owner
from . import dormant_rules, engine_statistical
from .part_category import categorise, load_rules as load_category_rules

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


_TS = "%Y-%m-%d %H:%M:%S"

# One precedent per stocking row, latest wins -- ORDER BY reviewed_at so "latest"
# means the most recent DECISION, not the most recent row insert. review_id breaks
# ties only.
#
# The cutoff is what keeps future information out of a re-score. batch_id is NOT a
# clock: backfill_history loads workbooks alphabetically (its glob is sorted by
# filename), so on the live project May'25 sits at batch 10 while Oct'24 and Sept'24
# sit at 11 and 12. `batch_id != ?` admits every other batch, and even `batch_id < ?`
# would grade October against a decision made seven months later -- while ORDER BY
# review_id would then pick the OLDER of the two as "latest". reviewed_at is the real
# decision time (backfill sets it from the workbook's modified_date for exactly this
# reason), so it is the only key that orders these correctly. It is fixed-width
# 'YYYY-MM-DD HH:MM:SS' UTC on both dialects, so TEXT comparison is chronological.
#
# NULL reviewed_at is excluded rather than admitted: a decision whose date is unknown
# cannot be shown to predate this batch, and this value feeds auto-clear.
#
# `<=`, not `<`: both clocks have one-second resolution, and reviewing a batch and
# uploading the next one inside the same second is ordinary. A tie is admissible --
# the leak this guards against is a decision made strictly AFTER this batch existed.
_PRIOR_SQL = (
    "SELECT h.item_id, h.stockroom_id, h.final_max, h.final_rop, h.final_min, "
    "b.payload "
    "FROM review_history h "
    "JOIN bom_rows b ON b.batch_id=h.batch_id AND b.item_id=h.item_id "
    "AND b.stockroom_id=h.stockroom_id "
    "WHERE h.batch_id != ? AND h.reviewed_at IS NOT NULL AND h.reviewed_at <= ? "
    "AND h.batch_id IN (SELECT batch_id FROM batches WHERE uploaded_by="
    "(SELECT uploaded_by FROM batches WHERE batch_id=?)) "
    "ORDER BY h.reviewed_at, h.review_id")


def _vintage(conn: Conn, batch_id: int, df: pd.DataFrame) -> str:
    """How current this batch's data is. A precedent must not postdate it.

    Three rungs, best first:

      modified_date  the newest engineer touch on any row -- the data's own vintage,
                     and the same field backfill_history reads to stamp reviewed_at,
                     so both sides of the comparison sit on one clock.
      uploaded_at    when the snapshot entered the system. Weaker (a backfilled 2024
                     workbook is stamped with the day it was loaded) but it still
                     pins a re-score to its own upload, which is the case that leaks.
      now()          nothing to go on; admits every decision already recorded.
    """
    # format="mixed" is deliberate, not a default: one workbook column carries both
    # '08/14/2025 14:59:42' and ISO+offset. Without it pandas warns on every scoring
    # run that it could not infer a single format and silently falls back to the
    # same per-element parse.
    ts = (pd.to_datetime(df["modified_date"], format="mixed",
                         errors="coerce", utc=True).max()
          if "modified_date" in df.columns else pd.NaT)
    if not pd.isna(ts):
        return ts.tz_localize(None).strftime(_TS)
    row = conn.execute("SELECT uploaded_at FROM batches WHERE batch_id=?",
                       (batch_id,)).fetchone()
    if row and row["uploaded_at"]:
        return str(row["uploaded_at"])
    return datetime.now(timezone.utc).strftime(_TS)


def _attach_prior_benchmark(conn: Conn, batch_id: int, df: pd.DataFrame) -> pd.DataFrame:
    """Carry each part's last engineer decision onto this month's rows.

    A brand-new upload has no factory_recommended_new_*, so without this the
    engine has nothing to grade itself against and agreement is 'none' for the
    whole batch. Unlike the similarity layer we keep backfill-v1 rows: that
    exclusion (similarity.py) is about accept/override RATES being meaningless
    for a backfilled decision -- the number itself is the engineer's real one.

    prior_c365 travels alongside so the engine can reject a stale precedent
    (engine_statistical._drift_ok).

    Only decisions older than this batch's own data are admissible -- see _vintage
    and _PRIOR_SQL. Re-scoring January after February has been reviewed must not
    grade January against February's answer: that value gates specialists.safe_clear
    and recommend.bulk_acceptable, so future information would be clearing rows.
    """
    prior: dict[tuple[str, str], tuple] = {}
    for row in conn.execute(_PRIOR_SQL, (batch_id, _vintage(conn, batch_id, df), batch_id)):
        payload = json.loads(row["payload"])
        prior[(str(row["item_id"]), str(row["stockroom_id"]))] = (
            row["final_max"], row["final_rop"], row["final_min"],
            payload.get(engine_statistical.CONS[365]))
    if not prior:
        return df
    keys = zip(df.get("item_id", pd.Series("", index=df.index)).astype(str),
               df.get("stockroom_id", pd.Series("", index=df.index)
                      ).astype(str).str.strip())
    hits = [prior.get(k, (None, None, None, None)) for k in keys]
    for pos, col in enumerate(("prior_final_max", "prior_final_rop",
                               "prior_final_min", "prior_c365")):
        df[col] = [h[pos] for h in hits]
    return df


def score_batch(conn: Conn, batch_id: int) -> dict:
    df = load_batch_df(conn, batch_id)
    if df.empty:
        raise ValueError(f"Batch {batch_id} has no scoreable rows")
    df = _attach_prior_benchmark(conn, batch_id, df)
    # The engine matches category-scoped dormant rules on this column. Resolved
    # here, not in the engine: categorising needs the confirmed lexicon, and
    # engine_statistical.run() holds no database handle by design.
    owner = batch_owner(conn, batch_id)
    category_rules, _broken = load_category_rules(conn, owner)
    df["part_category"] = [categorise(d, category_rules)
                           for d in df.get("item_desc", pd.Series("", index=df.index))]

    cfg = active_config(conn, owner)
    # Engineer-owned dormant stocking rules travel in cfg for the same reason.
    # Folded in BEFORE cfg_hash so editing a rule invalidates the fingerprint --
    # otherwise a re-score would claim the same config produced a new number.
    cfg = {**cfg, "dormant_rules": dormant_rules.load_rules(conn, owner)}
    cfg_hash = hashlib.sha256(
        json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]

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
    for c in ("route", "consumable", "agreement", "agreement_source"):
        if c not in res.columns:
            res[c] = ""
    # NOTE: not itertuples() -- it renames underscore-prefixed columns
    # (_exposure_usd), which would silently mis-read fields.
    cols = ["item_id", "factory_recommended_new_max", "factory_recommended_new_rop",
            "factory_recommended_new_min", "review_required",
            "factory_recommendation_action", "reason_code", "risk_level",
            "confidence_score", "explanation", "_exposure_usd",
            "model_version", "rule_version", "route", "consumable", "agreement",
            "agreement_source"]
    payload = [
        (batch_id, str(v[0]), stk.iloc[i], int(v[1]), int(v[2]), int(v[3]),
         str(v[4]), str(v[5]), str(v[6]), str(v[7]), float(v[8]), str(v[9]),
         float(v[10]), str(v[11]), str(v[12]), str(v[13]), str(v[14]), str(v[15]),
         str(v[16]))
        for i, v in enumerate(res[cols].to_numpy())
    ]
    payload = [p for p in payload if (p[1], p[2]) not in reviewed]
    conn.executemany(
        "INSERT INTO recommendation_result (batch_id, item_id, stockroom_id, new_max, "
        "new_rop, new_min, review_required, action, reason_code, risk_level, confidence, "
        "explanation, exposure_usd, model_version, rule_version, route, consumable, agreement, "
        "agreement_source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
    conn.execute(
        "UPDATE batches SET status='scored', scored_rule_version=?, scored_config_hash=?, "
        "scored_at=datetime('now') WHERE batch_id=?",
        (cfg["rule_version"], cfg_hash, batch_id))
    conn.commit()

    review_y = int((res.review_required == "Y").sum())
    codes = Counter(c for cs in res.reason_code for c in cs.split(","))
    return {
        "batch_id": batch_id,
        "rows_scored": len(res),
        "rows_preserved": len(reviewed),   # already reviewed; not re-scored
        "review_required_Y": review_y,
        "review_required_N": len(res) - review_y,
        "actions": res.factory_recommendation_action.value_counts().to_dict(),
        "risk_levels": res.risk_level.value_counts().to_dict(),
        "top_reason_codes": dict(codes.most_common(8)),
        "rule_version": cfg["rule_version"],
        "config_hash": cfg_hash,
    }
