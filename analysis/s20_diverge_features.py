"""S20 -- pull the engine's own input features for every S19 decision-vs-engine row.

S19 exports what the engineer and the engine each said. It cannot say WHY they
differ, because the drivers (consumption windows, lead time, criticality, MOQ,
current levels) live in `bom_rows.payload`. This joins them back on, one row
per reviewed item per month, so divergence can be decomposed into causes
instead of counted.

Run: python analysis/s20_diverge_features.py   ->  analysis/output/s20_diverge_features.csv
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from app import rest_conn  # noqa: E402

OUT = ROOT / "analysis" / "output" / "s20_diverge_features.csv"
# Full payloads, kept as a pickle so s20_diverge_rootcause.py can re-run the
# engine offline on exactly the rows the engineers reviewed.
PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"

# Same mapping as s19 -- batch id to the calendar month of that BOM review.
BATCH_MONTH = {8: "2024-07", 9: "2024-08", 12: "2024-09", 11: "2024-10",
               15: "2024-12", 16: "2025-03", 10: "2025-05", 13: "2026-01"}

# payload keys pulled through as-is; every one is a documented engine input or
# a policy field the engine deliberately ignores (the second group is what
# lets us test "did the engineer use something the engine never reads?").
PAYLOAD_COLS = [
    "last_5_day_cnsmptn_qty", "last_30_day_cnsmptn_qty", "last_90_day_cnsmptn_qty",
    "last_180_day_cnsmptn_qty", "last_365_day_cnsmptn_qty", "last_547_day_cnsmptn_qty",
    "contractual_lead_time", "sfm_mean_lt_cd", "order_qty_multiple", "unitprice",
    "max_qty", "rop_qty", "min_qty", "frequencymonthswithusage", "sfm_criticality",
    "ownership", "qry_eoh_excess_qty", "days_since_last_issue",
    "sfm_max", "sfm_rop", "sfm_min", "sfm_recommendation",
    "other_stkrm_365d_cons_qty", "shareable_indicator", "replenishment_policy",
    "category_type", "bunker_type", "avail_qty", "open_po_qty", "partfreq",
    "spending_impact", "aging_status", "excess_status_tf",
]

SQL = """
select rh.item_id, rh.stockroom_id,
       br.payload::jsonb ->> 'item_desc' as part_description,
       rh.final_max, rh.final_rop, rh.final_min,
       rh.engine_max, rh.engine_rop, rh.engine_min,
       rr.route, rr.agreement, rr.agreement_source,
       rh.current_max, rh.current_rop, rh.current_min,
       rh.comment, rh.justification as engineer_justification,
       rr.reason_code, rr.confidence, rr.review_required, rr.consumable,
       rr.risk_level, rr.action, rr.exposure_usd,
       br.payload as _payload,
       {payload}
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
""".format(payload=",\n       ".join(
    f"br.payload::jsonb ->> '{c}' as {c}" for c in PAYLOAD_COLS))

BASE = ["item_id", "stockroom_id", "part_description",
        "final_max", "final_rop", "final_min",
        "engine_max", "engine_rop", "engine_min",
        "route", "agreement", "agreement_source",
        "current_max", "current_rop", "current_min",
        "comment", "engineer_justification",
        "reason_code", "confidence", "review_required", "consumable",
        "risk_level", "action", "exposure_usd"]


def main() -> int:
    conn = rest_conn.connect()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    payloads: list[dict] = []
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source_month", "batch_id"] + BASE + PAYLOAD_COLS)
        for batch_id, month in sorted(BATCH_MONTH.items(), key=lambda kv: kv[1]):
            rows = conn.execute(SQL, (batch_id,)).fetchall()
            for r in rows:
                w.writerow([month, batch_id] + [r[c] for c in BASE + PAYLOAD_COLS])
                p = r["_payload"]
                p = dict(json.loads(p) if isinstance(p, str) else p)
                p["_month"], p["_batch"] = month, batch_id
                p["_final_max"], p["_final_rop"], p["_final_min"] = (
                    r["final_max"], r["final_rop"], r["final_min"])
                payloads.append(p)
            written += len(rows)
            print(f"  {month} (batch {batch_id}): {len(rows)} rows")
    conn.close()
    pd.DataFrame(payloads).to_pickle(PKL)
    print(f"wrote {written} rows -> {OUT}")
    print(f"wrote {len(payloads)} payloads -> {PKL}")
    assert written > 0, "no historical review rows returned"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
