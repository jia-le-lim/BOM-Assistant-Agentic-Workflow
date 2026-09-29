"""End-to-end against the real Jan'26 file (skipped if the CSV is absent)."""

import sqlite3
import time
from pathlib import Path

import pytest

from conftest import ENG, OWNER_VIEWER as VIEWER

REAL_CSV = Path(__file__).resolve().parents[2] / "BOM table" / "BOM REVIEW_Jan'26 .csv"

pytestmark = pytest.mark.skipif(not REAL_CSV.exists(), reason="real CSV not present")


def test_real_jan26_end_to_end(client, db_file):
    t0 = time.time()
    with REAL_CSV.open("rb") as f:
        r = client.post("/upload-bom-file",
                        files={"file": (REAL_CSV.name, f, "text/csv")},
                        data={"label": "Jan26", "module_filter": "TCB"},
                        headers=ENG)
    assert r.status_code == 200, r.text
    s = r.json()
    t_upload = time.time() - t0

    assert s["rows_loaded"] == 2780
    assert 10 <= s["rows_quarantined"] <= 30, s["quarantine_reasons"]

    t0 = time.time()
    run1 = client.post(f"/run-recommendation?batch_id={s['batch_id']}", headers=ENG)
    assert run1.status_code == 200, run1.text
    t_score = time.time() - t0
    r1 = run1.json()

    assert r1["rows_scored"] == 2780 - s["rows_quarantined"]
    assert 900 <= r1["review_required_Y"] <= 1200
    assert "ZERO_RECOMMENDATION_OVERRIDE" in r1["top_reason_codes"]

    # Determinism at the API level: rescore -> byte-identical results
    conn = sqlite3.connect(db_file)
    dump1 = conn.execute(
        "SELECT item_id,new_max,new_rop,new_min,review_required,action,reason_code,"
        "risk_level,confidence FROM recommendation_result ORDER BY item_id").fetchall()
    conn.close()
    client.post(f"/run-recommendation?batch_id={s['batch_id']}", headers=ENG)
    conn = sqlite3.connect(db_file)
    dump2 = conn.execute(
        "SELECT item_id,new_max,new_rop,new_min,review_required,action,reason_code,"
        "risk_level,confidence FROM recommendation_result ORDER BY item_id").fetchall()
    conn.close()
    assert dump1 == dump2

    # Export with zero reviews: nothing leaves, everything pending is counted
    e = client.get(f"/export/wings?batch_id={s['batch_id']}", headers=ENG)
    assert e.status_code == 200
    assert e.headers["X-Rows-Exported"] == "0"

    # Pagination sanity on the biggest queue
    q = client.get(f"/recommendations?batch_id={s['batch_id']}"
                   f"&status=pending_review&limit=10", headers=VIEWER).json()
    assert q["total"] > 0 and len(q["items"]) == 10

    print(f"\n[REAL E2E] upload {t_upload:.1f}s | score {t_score:.1f}s | "
          f"loaded {s['rows_loaded']} | quarantined {s['rows_quarantined']} "
          f"{s['quarantine_reasons']} | review_Y {r1['review_required_Y']} | "
          f"pending_export {e.headers['X-Pending-Review']}")
