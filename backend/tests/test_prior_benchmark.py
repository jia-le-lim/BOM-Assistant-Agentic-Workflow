"""engine_adapter._attach_prior_benchmark: only PAST decisions may be a precedent.

Re-scoring January after February has been reviewed must not grade January against
February's answer. agreement / agreement_source gate specialists.safe_clear and
recommend.bulk_acceptable, so a future decision leaking in would be auto-clearing
rows on information that did not exist when the month was current.

batch_id cannot express "older". backfill_history globs workbooks and sorts them by
FILENAME, so on the live project May'25 landed at batch 10 while Oct'24 and Sept'24
landed at 11 and 12 -- `batch_id < ?` would admit a May 2025 decision as precedent
for October 2024, and ORDER BY review_id would then call the OLDER of two decisions
the "latest". Both halves are keyed on reviewed_at instead, which backfill stamps
from the workbook's own modified_date.
"""

import json

import pandas as pd
import pytest

SR = "24"
OCT_24 = "2024-10-16 06:09:36"
SEP_24 = "2024-09-18 07:04:56"
MAY_25 = "2025-05-15 07:12:48"


@pytest.fixture()
def conn(db_file):
    from app.db import get_conn, init_db
    init_db()
    c = get_conn()
    yield c
    c.close()


def _batch(conn, label: str) -> int:
    return conn.insert_returning("INSERT INTO batches (label, uploaded_by) VALUES (?, ?)",
                                 (label, "alice"), "batches")


def _decision(conn, label: str, item: str, final_max: int, reviewed_at, c365=12):
    """A reviewed row in its own batch: bom_rows + recommendation_result (the FK
    review_history hangs off) + the decision itself."""
    bid = _batch(conn, label)
    conn.execute(
        "INSERT INTO bom_rows (batch_id, item_id, stockroom_id, payload) VALUES (?,?,?,?)",
        (bid, item, SR, json.dumps({"item_id": item,
                                    "last_365_day_cnsmptn_qty": str(c365)})))
    conn.execute(
        "INSERT INTO recommendation_result (batch_id, item_id, stockroom_id, "
        "new_max, new_rop, new_min) VALUES (?,?,?,?,?,?)", (bid, item, SR, 0, 0, 0))
    conn.execute(
        "INSERT INTO review_history (batch_id, item_id, stockroom_id, decision, "
        "final_max, final_rop, final_min, reviewed_at) VALUES (?,?,?,?,?,?,?,?)",
        (bid, item, SR, "historical", final_max, 0, 0, reviewed_at))
    conn.commit()
    return bid


def _target(modified_date=OCT_24, item="A") -> pd.DataFrame:
    row = {"item_id": item, "stockroom_id": SR}
    if modified_date is not None:
        row["modified_date"] = modified_date
    return pd.DataFrame([row])


def _attach(conn, batch_id, df):
    from app.engine_adapter import _attach_prior_benchmark
    return _attach_prior_benchmark(conn, batch_id, df)


def _prior_max(out):
    """What the engine will see. _attach_prior_benchmark skips the columns entirely
    when nothing matched, and engine_statistical._num maps both that and an explicit
    None to NaN -- so "no precedent" is the assertion, not the column's presence."""
    if "prior_final_max" not in out.columns:
        return None
    return out["prior_final_max"].iloc[0]


# ---------------------------------------------------------------------------
# the cutoff
# ---------------------------------------------------------------------------

def test_future_decision_is_not_a_precedent(conn):
    """The bug: a May'25 review must not grade an Oct'24 batch."""
    _decision(conn, "hist-may_25", "A", 999, MAY_25)
    target = _batch(conn, "oct-24-rescore")

    assert _prior_max(_attach(conn, target, _target(OCT_24))) is None


def test_past_decision_is_a_precedent(conn):
    """The guard must not throw away the precedents it exists to carry."""
    _decision(conn, "hist-sept_24", "A", 111, SEP_24)
    target = _batch(conn, "oct-24-rescore")

    out = _attach(conn, target, _target(OCT_24))
    assert out["prior_final_max"].iloc[0] == 111
    assert out["prior_c365"].iloc[0] == "12"


def test_latest_past_decision_wins_by_date_not_insert_order(conn):
    """The ordering half. The chronologically LATER decision is inserted FIRST, so
    it carries the LOWER review_id -- ORDER BY review_id would pick the wrong one."""
    _decision(conn, "hist-sept_24", "A", 222, SEP_24)                 # review_id 1
    _decision(conn, "hist-july_24", "A", 111, "2024-07-15 04:56:13")  # review_id 2
    target = _batch(conn, "oct-24-rescore")

    out = _attach(conn, target, _target(OCT_24))
    assert out["prior_final_max"].iloc[0] == 222


def test_undated_decision_is_not_a_precedent(conn):
    """A decision that cannot be shown to predate this batch feeds auto-clear, so
    it is excluded rather than assumed old."""
    _decision(conn, "hist-undated", "A", 999, None)
    target = _batch(conn, "oct-24-rescore")

    assert _prior_max(_attach(conn, target, _target(OCT_24))) is None


def test_batch_own_reviews_are_never_its_own_precedent(conn):
    """Self-precedent is circular; the batch_id exclusion still stands."""
    own = _decision(conn, "self", "A", 999, SEP_24)

    assert _prior_max(_attach(conn, own, _target(OCT_24))) is None


def test_fresh_upload_without_modified_date_admits_recorded_decisions(conn):
    """A new month has no vintage to compare against. Falling back to now() admits
    everything already on record, which for a genuinely new upload is correct."""
    _decision(conn, "hist-may_25", "A", 111, MAY_25)
    target = _batch(conn, "brand-new-month")

    out = _attach(conn, target, _target(modified_date=None))
    assert out["prior_final_max"].iloc[0] == 111


def test_unmatched_part_gets_no_precedent(conn):
    _decision(conn, "hist-sept_24", "OTHER", 111, SEP_24)
    target = _batch(conn, "oct-24-rescore")

    assert _prior_max(_attach(conn, target, _target(OCT_24, item="A"))) is None


# ---------------------------------------------------------------------------
# _vintage
# ---------------------------------------------------------------------------

def test_vintage_is_the_newest_touch_on_the_batch(conn):
    from app.engine_adapter import _vintage
    df = pd.DataFrame([{"modified_date": SEP_24}, {"modified_date": OCT_24},
                       {"modified_date": ""}])
    assert _vintage(conn, _batch(conn, "any"), df) == OCT_24


def test_vintage_normalises_the_workbook_date_format(conn):
    """Workbooks carry '08/14/2025 14:59:42' and ISO+offset in the same column;
    both must land in the TEXT format reviewed_at is stored in, or the comparison
    is a string sort against a different shape."""
    from app.engine_adapter import _vintage
    bid = _batch(conn, "any")
    assert _vintage(conn, bid,
                    pd.DataFrame([{"modified_date": "08/14/2025 14:59:42"}])) \
        == "2025-08-14 14:59:42"
    assert _vintage(conn, bid,
                    pd.DataFrame([{"modified_date": "2025-08-14T14:59:42+00:00"}])) \
        == "2025-08-14 14:59:42"


def test_vintage_falls_back_to_the_batch_upload_time(conn):
    """No usable modified_date -> uploaded_at still pins a re-score to its own
    upload, which is the case that leaks future decisions."""
    from app.engine_adapter import _vintage
    bid = _batch(conn, "any")
    uploaded = conn.execute("SELECT uploaded_at FROM batches WHERE batch_id=?",
                            (bid,)).fetchone()["uploaded_at"]
    assert _vintage(conn, bid, pd.DataFrame([{"item_id": "A"}])) == uploaded
    assert _vintage(conn, bid, pd.DataFrame([{"modified_date": "not a date"}])) == uploaded
