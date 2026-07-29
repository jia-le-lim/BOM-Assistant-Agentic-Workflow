"""Do the proposed foreign keys actually hold on real data?

Run this BEFORE adding constraints. The risky pair is
`recommendation_result -> bom_rows`: ingestion writes bom_rows.item_id from the
*stripped* record while the engine reads item_id back out of the JSON payload,
so any disagreement about whitespace, casing or the `item#dup<n>` suffix would
turn a silent mismatch into a failed batch run the day the constraint lands.

Exercises the full real path -- ingest the Jan'26 workbook, score it, record a
review, stage and confirm a chat proposal -- then counts orphans for every
relation the migration is about to declare.

    .venv\\Scripts\\python.exe backend\\scripts\\check_referential_integrity.py
"""

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Test-shaped run: local SQLite, no network, no secrets.
os.environ["BOM_ALLOW_SQLITE"] = "1"
os.environ.pop("DATABASE_URL", None)
os.environ["BOM_DB_PATH"] = str(Path(tempfile.gettempdir()) / "bom_fk_check.db")
Path(os.environ["BOM_DB_PATH"]).unlink(missing_ok=True)

from app.db import get_conn, init_db          # noqa: E402
from app.engine_adapter import score_batch    # noqa: E402
from app.ingestion import ingest              # noqa: E402

CSV = BACKEND.parent / "BOM table" / "BOM REVIEW_Jan'26 .csv"

# (label, orphan-counting query). Each returns rows that WOULD violate the FK.
CHECKS = [
    ("bom_rows.batch_id -> batches",
     "SELECT COUNT(*) c FROM bom_rows r "
     "LEFT JOIN batches b ON b.batch_id=r.batch_id WHERE b.batch_id IS NULL"),

    ("recommendation_result.batch_id -> batches",
     "SELECT COUNT(*) c FROM recommendation_result r "
     "LEFT JOIN batches b ON b.batch_id=r.batch_id WHERE b.batch_id IS NULL"),

    ("recommendation_result -> bom_rows (composite)  <-- the risky one",
     "SELECT COUNT(*) c FROM recommendation_result r LEFT JOIN bom_rows m "
     "ON m.batch_id=r.batch_id AND m.item_id=r.item_id "
     "AND m.stockroom_id=r.stockroom_id WHERE m.item_id IS NULL"),

    ("review_history.batch_id -> batches",
     "SELECT COUNT(*) c FROM review_history r "
     "LEFT JOIN batches b ON b.batch_id=r.batch_id WHERE b.batch_id IS NULL"),

    ("review_history -> recommendation_result (composite)",
     "SELECT COUNT(*) c FROM review_history r LEFT JOIN recommendation_result x "
     "ON x.batch_id=r.batch_id AND x.item_id=r.item_id "
     "AND x.stockroom_id=r.stockroom_id WHERE x.item_id IS NULL"),

    ("pending_change.batch_id -> batches",
     "SELECT COUNT(*) c FROM pending_change p "
     "LEFT JOIN batches b ON b.batch_id=p.batch_id "
     "WHERE p.batch_id IS NOT NULL AND b.batch_id IS NULL"),

    ("pending_change.confirmed_review_id -> review_history",
     "SELECT COUNT(*) c FROM pending_change p "
     "LEFT JOIN review_history r ON r.review_id=p.confirmed_review_id "
     "WHERE p.confirmed_review_id IS NOT NULL AND r.review_id IS NULL"),

    ("conversation_turn.batch_id -> batches",
     "SELECT COUNT(*) c FROM conversation_turn t "
     "LEFT JOIN batches b ON b.batch_id=t.batch_id "
     "WHERE t.batch_id IS NOT NULL AND b.batch_id IS NULL"),

    ("item_note.origin_turn_id -> conversation_turn",
     "SELECT COUNT(*) c FROM item_note n "
     "LEFT JOIN conversation_turn t ON t.turn_id=n.origin_turn_id "
     "WHERE n.origin_turn_id IS NOT NULL AND t.turn_id IS NULL"),

    ("item_note.origin_batch_id -> batches",
     "SELECT COUNT(*) c FROM item_note n "
     "LEFT JOIN batches b ON b.batch_id=n.origin_batch_id "
     "WHERE n.origin_batch_id IS NOT NULL AND b.batch_id IS NULL"),

    ("model_prediction_log.batch_id -> batches",
     "SELECT COUNT(*) c FROM model_prediction_log m "
     "LEFT JOIN batches b ON b.batch_id=m.batch_id WHERE b.batch_id IS NULL"),
]


def main() -> int:
    if not CSV.exists():
        print(f"FAIL: source workbook not found at {CSV}")
        return 1

    init_db()
    conn = get_conn()
    actor = {"user": "fkcheck", "role": "engineer"}

    print(f"ingesting {CSV.name} ...")
    res = ingest(conn, CSV.read_bytes(), "fk-check", CSV.name, "TCB", "fkcheck")
    batch_id = res["batch_id"]
    print(f"  batch {batch_id}: {res['rows_loaded']} rows, "
          f"{res['rows_quarantined']} quarantined")

    print("scoring ...")
    summary = score_batch(conn, batch_id)
    print(f"  {summary['rows_scored']} scored")

    # Exercise the decision + agent paths so those tables are non-empty; an
    # all-zero orphan count over empty tables would prove nothing.
    from app.agent.loop import log_turn, run_agent
    from app.routers.review import _record_review

    top = conn.execute(
        "SELECT * FROM recommendation_result WHERE batch_id=? AND review_required='Y' "
        "ORDER BY exposure_usd DESC LIMIT 2", (batch_id,)).fetchall()
    for rec in top:
        _record_review(conn, actor, batch_id, rec, "accept",
                       (rec["new_max"], rec["new_rop"], rec["new_min"]),
                       "fk check", "")
    item = top[0]["item_id"]

    said = f"set item {item} max to 4"
    result = run_agent(conn, said, batch_id, actor)
    turn_id = log_turn(conn, result, actor, said)
    conn.execute(
        "INSERT INTO item_note (item_id, note, author, origin_batch_id, origin_turn_id) "
        "VALUES (?,?,?,?,?)",
        (item, "carried-forward note", "fkcheck", batch_id, turn_id))

    pend = conn.execute("SELECT * FROM pending_change LIMIT 1").fetchone()
    if pend:
        rec = conn.execute(
            "SELECT * FROM recommendation_result WHERE batch_id=? AND item_id=?",
            (batch_id, pend["item_id"])).fetchone()
        rid, *_ = _record_review(conn, actor, batch_id, rec, "override",
                                 (4, 2, 1), "confirmed", "")
        conn.execute("UPDATE pending_change SET status='confirmed', "
                     "confirmed_review_id=? WHERE pending_id=?",
                     (rid, pend["pending_id"]))
    conn.commit()

    counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
              for t in ("batches", "bom_rows", "recommendation_result",
                        "review_history", "pending_change", "conversation_turn",
                        "item_note", "model_prediction_log")}
    print("\nrow counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    print("\norphan check")
    print("-" * 72)
    failures = 0
    for label, sql in CHECKS:
        n = conn.execute(sql).fetchone()["c"]
        status = "OK  " if n == 0 else "FAIL"
        if n:
            failures += 1
        print(f"  {status}  {n:>6}  {label}")
    conn.close()

    print("-" * 72)
    if failures:
        print(f"\n{failures} relation(s) have orphans -- do NOT add those "
              f"constraints until the mismatch is understood.")
        return 1
    print("\nAll proposed foreign keys hold on the real Jan'26 data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
