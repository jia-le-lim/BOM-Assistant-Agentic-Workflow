"""S19 -- export engineer decision vs engine recommendation, TCB historical months.

One row per reviewed item per monthly BOM review: source month, part code,
description, engineer max/rop/min, engine max/rop/min, the engine's demand route
(active / dormant / dying / no data), the verdict, and the engineer's own reason
plus the stocking context needed to interpret it.

`validation` is the review scorecard rule (see _validate): a strict 10% relative
tolerance on Max and ROP only, with no absolute floor, so small parts are held to
a tight band -- an engineer's 2 admits only 2 (1.8-2.2), and an engine answer of
1 diverges. Min is excluded because Max and ROP drive replenishment; Min is a
derived floor.

The tolerance is not restated here: _validate calls engine_statistical.close_enough,
and _selfcheck asserts case by case that this export and the engine reach the same
verdict, so the scorecard cannot drift from the rule the engine applies.

`recommendation_result.agreement` used to be carried through as a second column,
back when the engine graded on max(1, 10%) across all three fields. The engine now
applies the same rule as this scorecard, so the column was redundant for new
batches and was dropped on 2026-08-28. Note it still differs for the eight
historical batches, whose stored verdicts are an audit record scored under the old
rule and are deliberately never re-scored -- read them from
recommendation_result directly if that history is ever needed.

Reads Supabase over the same PostgREST transport the backend uses.
Run: python analysis/s19_export_decision_vs_engine.py
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# python-dotenv is not a dependency of the analysis venv; the file is plain
# KEY=VALUE, so read it directly rather than adding one.
for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import pandas as pd  # noqa: E402

from app import engine_statistical, rest_conn  # noqa: E402

OUT = ROOT / "analysis" / "output" / "s19_decision_vs_engine_tcb.csv"

# Batch -> calendar month of the review. Derived from the source filenames in
# `batches`; the label alone is not sortable (hist-sept_24 < hist-oct_24
# alphabetically but not chronologically).
BATCH_MONTH = {
    8: "2024-07",
    9: "2024-08",
    12: "2024-09",
    11: "2024-10",
    15: "2024-12",
    16: "2025-03",
    10: "2025-05",
    13: "2026-01",
}

ROUTE_LABEL = {
    "active": "active",
    "dormant": "dormant",
    "dying": "dying",
    "no-data": "no data",
    "": "no data",
    None: "no data",
}

# Part-type / stocking context carried alongside each decision. Trimmed to what
# the reviewers actually use: the accounting and org fields (item_gl_account,
# gl_group, functional_group, purchasing_group_name) and sfm_recommendation were
# dropped as unused on 2026-08-28.
#
# Worth knowing if a dormant keep-rule is ever built from this file: measured
# against the engineers' own "Dead Keep Max=1" (204 rows) vs "Dead Keep Max=0"
# (4,090) comments, replenishment_policy = "Order To Max" predicts keep-1 at
# 94.8% precision / 71.6% recall, and sfm_recommendation = "Decrease Algo" was
# 100% (122/122). replenishment_policy is kept here for that reason; every
# dropped field is still recoverable from bom_rows.payload.
PART_TYPE_COLS = ["replenishment_policy", "machine_type", "max_qty",
                  "avail_qty", "days_since_last_issue"]

# Description tokens measured on the same split: each appears in >=60% of the
# dormant parts engineers chose to stock. They are consumable tooling terms,
# which is the point -- the free-text description carries wear-and-tear
# information that no structured column in this extract does. A hint to eyeball,
# never a classification.
WEAR_TOKENS = ("PLACE", "SISIC", "MATRIX", "PREHEAT", "INTERLOCK", "CBL", "CHUCK")


def _wear_hint(desc) -> str:
    """Which measured consumable-tooling tokens appear in this description."""
    d = str(desc or "").upper()
    return ";".join(t for t in WEAR_TOKENS if t in d)


SQL = """
select rh.item_id,
       br.payload::jsonb ->> 'item_desc' as part_description,
       rh.final_max, rh.final_rop, rh.final_min,
       rh.engine_max, rh.engine_rop, rh.engine_min,
       rh.justification as engineer_justification,
       rh.comment as engineer_comment,
       {part_type},
       rr.route
  from review_history rh
  join bom_rows br
    on br.batch_id = rh.batch_id
   and br.item_id = rh.item_id
   and br.stockroom_id = rh.stockroom_id
  join recommendation_result rr
    on rr.batch_id = rh.batch_id
   and rr.item_id = rh.item_id
   and rr.stockroom_id = rh.stockroom_id
 where rh.batch_id = ?
   and rh.decision = 'historical'
 order by rh.item_id
""".format(part_type=",\n       ".join(
    f"br.payload::jsonb ->> '{c}' as {c}" for c in PART_TYPE_COLS))

HEADER = [
    "source_month", "part_code", "part_description",
    "engineer_max", "engineer_rop", "engineer_min",
    "engine_max", "engine_rop", "engine_min",
    "category", "validation",
    # --- evidence for the wear-and-tear / part-class decision (see above) ---
    "engineer_justification", "engineer_comment",
] + PART_TYPE_COLS + ["wear_hint"]

def _validate(row) -> str:
    """Scorecard verdict on Max and ROP. 'none' when the row has no benchmark.

    The tolerance itself is engine_statistical.close_enough -- imported, not
    restated, so this scorecard cannot drift from the rule the engine applies.
    """
    pairs = [(row[f"engine_{f}"], row[f"final_{f}"]) for f in engine_statistical.AGREE_FIELDS]
    if all(pd.isna(b) for _, b in pairs):
        return "none"
    return ("match" if all(e is not None and engine_statistical.close_enough(e, b)
                           for e, b in pairs) else "diverge")


def _selfcheck() -> None:
    """The tolerance rule is the whole scorecard -- pin it with real cases.

    Also pins that this export and the engine agree: the same inputs must give
    the same verdict through _validate here and _agreement inside the engine.
    """
    def v(emax, erop, bmax, brop, bmin=None):
        return _validate({"engine_max": emax, "engine_rop": erop,
                          "final_max": bmax, "final_rop": brop, "final_min": bmin})
    assert v(2, 2, 2, 2) == "match"          # exact
    assert v(1, 2, 2, 2) == "diverge"        # 2 admits only 2 (1.8-2.2)
    assert v(11, 11, 10, 10) == "match"      # exactly 10% away, inclusive
    assert v(12, 10, 10, 10) == "diverge"    # Max 2 off a base of 10 breaks it
    assert v(10, 12, 10, 10) == "diverge"    # ROP alone can break it too
    assert v(0, 0, 0, 0) == "match"          # both zero
    assert v(1, 0, 0, 0) == "diverge"        # 0 admits only 0
    assert v(5, 5, 5, 5, 999) == "match"     # Min is ignored however far off
    assert v(5, 5, None, None) == "none"     # no benchmark to grade against
    # ... and the engine reaches the same verdict from the same numbers.
    for emax, erop, bmax, brop in [(2, 2, 2, 2), (1, 2, 2, 2), (11, 11, 10, 10),
                                   (1, 0, 0, 0), (5, 5, 5, 5)]:
        assert v(emax, erop, bmax, brop) == engine_statistical._agreement(
            (emax, erop, 0), (bmax, brop, 999)), "scorecard drifted from the engine"


def main() -> int:
    _selfcheck()
    conn = rest_conn.connect()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with OUT.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for batch_id, month in sorted(BATCH_MONTH.items(), key=lambda kv: kv[1]):
            rows = conn.execute(SQL, (batch_id,)).fetchall()
            for r in rows:
                w.writerow([
                    month, r["item_id"], r["part_description"],
                    r["final_max"], r["final_rop"], r["final_min"],
                    r["engine_max"], r["engine_rop"], r["engine_min"],
                    ROUTE_LABEL.get(r["route"], r["route"]),
                    _validate(r),
                    r["engineer_justification"], r["engineer_comment"],
                    *[r[c] for c in PART_TYPE_COLS],
                    _wear_hint(r["part_description"]),
                ])
            written += len(rows)
            print(f"  {month} (batch {batch_id}): {len(rows)} rows")
    conn.close()
    print(f"wrote {written} rows -> {OUT}")
    assert written > 0, "no historical review rows returned"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
