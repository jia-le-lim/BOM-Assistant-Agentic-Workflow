"""Load previously-reviewed BOM workbooks so the KNN layer has peers to retrieve.

The similarity layer is instance-based: there is no model to tune, so the only
lever on its quality is how many past human decisions it can see. A fresh
database has none, and every part comes back NO_RELIABLE_ANALOGUE.

Per workbook this does what the app itself does, in the same order and through
the same functions -- there is no second ingestion path:

    ingest()      -> batches + bom_rows      (the frozen feature snapshot)
    score_batch() -> recommendation_result   (supplies route / consumable)
    then          -> review_history          (the engineer's own numbers)

The last step reads factory_recommended_new_max/rop/min: the number the engineer
actually chose, which is what an analogue median is made of. justification and
comments carry across as the "why".

WHAT THESE ROWS ARE NOT
-----------------------
A review_history row normally records a decision made against THIS engine's
proposal. The engineers who filled these workbooks never saw it -- they were
answering WINGS/SFM/ATM numbers. So:

  decision       = 'historical'      not accept/override/reject. Deriving one by
                                     comparing their number to an engine that did
                                     not exist would fabricate an override rate.
  model_version  = 'backfill-v1'     similarity.py skips these rows when computing
                                     historical_override_rate and friends.
  engine_max/... = what this engine computes today, recorded for traceability
                   only. It is NOT what the reviewer saw.

current_max/rop/min and every similarity feature ARE contemporaneous: they come
from the workbook's own row, which ingestion stores verbatim and never updates.

USAGE
-----
    # see the plan, write nothing (default)
    python backend/scripts/backfill_history.py

    # do it, one file first
    python backend/scripts/backfill_history.py --apply --only Dec_24

    # then the rest
    python backend/scripts/backfill_history.py --apply

    # undo one run (review_history -> batches; the FK is RESTRICT, so in
    # that order and no other)
    python backend/scripts/backfill_history.py --rollback hist-dec_24

Re-running is safe: a workbook whose label is already in `batches` is skipped.
"""

import argparse
import glob
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import pandas as pd  # noqa: E402

from app.db import get_conn, init_db, stored_config  # noqa: E402
from app.engine_adapter import score_batch  # noqa: E402
from app.ingestion import IngestionError, ingest  # noqa: E402

WORKBOOKS = BACKEND.parent / "BOM table"
LABEL_PREFIX = "hist-"

# Never peer evidence. DEMO_TCB_showcase is synthetic -- fabricated decisions
# would be indistinguishable from real ones once they are in review_history.
SKIP_FILES = {"demo_tcb_showcase.csv"}
BACKFILL_MODEL_VERSION = "backfill-v1"
HISTORICAL_DECISION = "historical"

# review_history, with reviewed_at set explicitly: the default is now(), and a
# 2024 decision stamped 2026 would break the "what was known at the time"
# ordering this whole layer rests on.
_INSERT = (
    "INSERT INTO review_history (batch_id, item_id, stockroom_id, reviewer, "
    "role, decision, current_max, current_rop, current_min, engine_max, "
    "engine_rop, engine_min, final_max, final_rop, final_min, comment, "
    "justification, requires_senior_approval, rule_version, model_version, "
    "reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def label_for(path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
    return f"{LABEL_PREFIX}{slug[:48]}"


def as_int(value, default=None):
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return default if pd.isna(v) else int(v)


def as_ts(value):
    """Workbook timestamps are mixed ('08/14/2025 14:59:42' and ISO+tz). Store
    in the repo's uniform TEXT format so ORDER BY reviewed_at stays sane."""
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.tz_localize(None).strftime("%Y-%m-%d %H:%M:%S")


def existing_labels(conn) -> set:
    return {r["label"] for r in conn.execute("SELECT label FROM batches")}


def synthesise_reviews(conn, batch_id: int, rule_version: str) -> dict:
    """One review_history row per scored row that carries an engineer number."""
    scored = {(r["item_id"], r["stockroom_id"]): r for r in conn.execute(
        "SELECT item_id, stockroom_id, new_max, new_rop, new_min "
        "FROM recommendation_result WHERE batch_id=?", (batch_id,))}

    rows, skipped_no_decision, inconsistent = [], 0, 0
    for src in conn.execute(
            "SELECT item_id, stockroom_id, payload FROM bom_rows "
            "WHERE batch_id=? AND quarantined=0", (batch_id,)):
        key = (src["item_id"], src["stockroom_id"])
        rec = scored.get(key)
        if rec is None:                      # not scored -> the FK would reject it
            continue
        p = json.loads(src["payload"])

        final_max = as_int(p.get("factory_recommended_new_max"))
        if final_max is None:
            skipped_no_decision += 1         # no engineer number = no decision
            continue
        final_rop = as_int(p.get("factory_recommended_new_rop"), 0)
        final_min = as_int(p.get("factory_recommended_new_min"), 0)
        if not (final_max >= final_rop >= final_min):
            inconsistent += 1                # stored as-is; it is historical fact

        rows.append((
            batch_id, src["item_id"], src["stockroom_id"],
            str(p.get("modified_user") or "backfill")[:120], "engineer",
            HISTORICAL_DECISION,
            as_int(p.get("max_qty"), 0), as_int(p.get("rop_qty"), 0),
            as_int(p.get("min_qty"), 0),
            rec["new_max"], rec["new_rop"], rec["new_min"],
            final_max, final_rop, final_min,
            str(p.get("comments") or "")[:2000] or None,
            str(p.get("justification") or "")[:2000] or None,
            0, rule_version, BACKFILL_MODEL_VERSION,
            as_ts(p.get("modified_date")),
        ))

    if rows:
        conn.executemany(_INSERT, rows)
        conn.commit()
    return {"reviews": len(rows), "no_engineer_number": skipped_no_decision,
            "inconsistent_triples": inconsistent}


def process(conn, path: Path, module: str, match: str, apply: bool) -> dict:
    label = label_for(path)
    if not apply:
        return {"file": path.name, "label": label, "status": "dry-run"}

    content = path.read_bytes()
    summary = ingest(conn, content, label, path.name, module, "backfill",
                     match_mode=match)
    batch_id = summary["batch_id"]
    score = score_batch(conn, batch_id)
    reviews = synthesise_reviews(conn, batch_id, score["rule_version"])
    return {"file": path.name, "label": label, "batch_id": batch_id,
            "status": "loaded", "rows": summary["rows_loaded"],
            "quarantined": summary["rows_quarantined"],
            "scored": score["rows_scored"], **reviews}


def rollback(conn, label: str) -> dict:
    """review_history first: review_history -> batches is ON DELETE RESTRICT, so
    dropping the batch while decisions hang off it fails by design."""
    row = conn.execute("SELECT batch_id FROM batches WHERE label=?",
                       (label,)).fetchone()
    if row is None:
        return {"label": label, "status": "not found"}
    bid = row["batch_id"]
    n = conn.execute("SELECT COUNT(*) AS c FROM review_history WHERE batch_id=?",
                     (bid,)).fetchone()["c"]
    conn.execute("DELETE FROM review_history WHERE batch_id=?", (bid,))
    conn.execute("DELETE FROM batches WHERE batch_id=?", (bid,))
    conn.commit()
    return {"label": label, "batch_id": bid, "status": "removed",
            "reviews_deleted": n}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without it nothing is stored")
    ap.add_argument("--only", default="",
                    help="substring filter on the filename")
    ap.add_argument("--module", default="TCB")
    ap.add_argument("--match", default="exact", choices=("exact", "tag"))
    ap.add_argument("--rollback", default="",
                    help="delete a backfilled batch by label")
    args = ap.parse_args()

    init_db()
    conn = get_conn()
    try:
        if args.rollback:
            print(rollback(conn, args.rollback))
            return 0

        rule_version = stored_config(conn)["rule_version"]
        done = existing_labels(conn)
        files = sorted(Path(p) for p in
                       glob.glob(str(WORKBOOKS / "*.xlsx"))
                       + glob.glob(str(WORKBOOKS / "*.csv")))
        files = [f for f in files if args.only.lower() in f.name.lower()
                 and f.name.lower() not in SKIP_FILES]
        if not files:
            print("no workbooks matched")
            return 1

        print(f"rule_version={rule_version}  module={args.module}/{args.match}  "
              f"apply={args.apply}\n")
        totals = {"reviews": 0, "scored": 0}
        for path in files:
            if label_for(path) in done:
                print(f"  SKIP    {path.name[:44]:46s} label already loaded")
                continue
            try:
                out = process(conn, path, args.module, args.match, args.apply)
            except (IngestionError, ValueError) as e:
                print(f"  FAIL    {path.name[:44]:46s} {e}")
                continue
            if out["status"] == "dry-run":
                # ingest() reads the whole workbook into memory, so size is the
                # practical limit here, not row count.
                mb = path.stat().st_size / 1_048_576
                warn = "  <-- large, expect minutes" if mb > 100 else ""
                print(f"  WOULD   {path.name[:44]:46s} {mb:8.1f} MB "
                      f"-> {out['label']}{warn}")
                continue
            totals["reviews"] += out["reviews"]
            totals["scored"] += out["scored"]
            print(f"  LOADED  {path.name[:44]:46s} batch={out['batch_id']} "
                  f"rows={out['rows']} scored={out['scored']} "
                  f"reviews={out['reviews']} "
                  f"(no_number={out['no_engineer_number']}, "
                  f"inconsistent={out['inconsistent_triples']})")

        if args.apply:
            pool = conn.execute("SELECT COUNT(*) AS c FROM review_history"
                                ).fetchone()["c"]
            print(f"\nadded {totals['reviews']} reviews; "
                  f"review_history now holds {pool}")
        else:
            print("\nnothing written. re-run with --apply")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
