"""S12 -- Two-snapshot monthly consumption reconstruction (PoC, PRD v3).

NOTE (PRD v3.1): the month-by-month series here is an EXPLORATORY trend view,
not an engine input. Even-spreading wide buckets erases the intermittency
(ADI/CV2) the statistical engine needs, so the engine uses raw window *rates*
instead. Keep this output for plotting/eyeballing only.

Scope (deliberately minimal, per PRD v3): use ONLY two BOM-review snapshots --
``AUGUST'24`` and ``Jan'26`` -- and reconstruct a *month-by-month* consumption
series for every part that appears in BOTH files and is tagged TCB or Epoxy.

Why two snapshots is enough to span ~3 years
--------------------------------------------
Each snapshot ships nested trailing-window actuals (last_30/90/180/365/547 day).
De-cumulating adjacent windows (``last_90 - last_30`` etc.) yields non-overlapping
period consumption; the 547-day window reaches ~18 months back, so:

    Aug'24  covers  2023-03 .. 2024-08
    Jan'26  covers  2024-08 .. 2026-01           (contiguous -> ~35 months)

Design decisions (all reversible; see PRD v3)
---------------------------------------------
* De-cumulate to per-period buckets, never feed nested cumulatives to a model.
* Buckets wider than one month are spread EVENLY across their months, and each
  emitted month carries ``bucket_month_span`` + ``is_measured`` so the model
  knows which points are directly measured (the last_30 month of each snapshot)
  vs interpolated.
* Missing != 0. A month with no covering data stays NaN (never zero-filled);
  a blank source window propagates NaN, a genuine source 0 stays 0.
* Overlap seam at 2024-08: Aug'24 owns months <= 2024-08, Jan'26 owns >= 2024-09,
  so the one shared month is not double-counted.
* Module filter uses the multi-tag field (``new_modulle``): a row is kept if that
  tag list contains ``Module-TCB`` or ``Module-Epoxy``.

Outputs (analysis/output/)
--------------------------
    s12_two_snapshot_monthly_long.csv   tidy panel -- one row per (item, month)
    s12_two_snapshot_monthly_wide.csv   item x month matrix (quick trend view)
    s12_two_snapshot_manifest.csv       provenance / coverage summary
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from common import OUT, find_header_row, find_tag_col, norm_cols, to_num

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "BOM table"

# (path, year, month, sheet)  -- sheet None == CSV. Anchored at month-end.
SNAPSHOTS: list[tuple[str, int, int, str | None]] = [
    ("AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx", 2024, 8, "Upload File"),
    ("BOM REVIEW_Jan'26 .csv", 2026, 1, None),
]

# Keep a row if its multi-tag module field mentions any of these modules.
TAG_PATTERN = re.compile(r"module-(tcb|epoxy)", re.IGNORECASE)

# Ascending trailing windows (days) -> months of lookback they represent.
WIN_MONTHS = {30: 1, 90: 3, 180: 6, 365: 12, 547: 18}
WINDOWS = sorted(WIN_MONTHS)                       # [30, 90, 180, 365, 547]
CONS_COLS = {w: f"last_{w}_day_cnsmptn_qty" for w in WINDOWS}

# Non-overlapping buckets: (window, previous-window-or-None, offsets in whole
# months back from the anchor). e.g. last_90-last_30 -> the 2 months before it.
BUCKETS = [(30, None, range(0, 1)), (90, 30, range(1, 3)), (180, 90, range(3, 6)),
           (365, 180, range(6, 12)), (547, 365, range(12, 18))]

# Static (non-consumption) attributes carried into the training set.
ID_COLS = ["item_desc", "machine_type", "replenishment_policy",
           "shared_parts", "unitprice", "contractual_lead_time"]

SEAM = 2024 * 12 + (8 - 1)          # month_index of 2024-08; Aug'24 owns <= this


def _mi(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def _ym(month_index: int) -> str:
    y, m = divmod(month_index, 12)
    return f"{y:04d}-{m + 1:02d}"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_snapshot(name: str, year: int, month: int, sheet: str | None) -> pd.DataFrame:
    """Load one snapshot, filter to TCB/Epoxy tags, collapse stockrooms -> item."""
    path = SRC / name
    if sheet is None:
        raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    else:
        hdr = find_header_row(path, sheet)
        raw = pd.read_excel(path, sheet_name=sheet, header=hdr, dtype=str)
    raw = norm_cols(raw)
    for c in raw.columns:
        if hasattr(raw[c], "str"):
            raw[c] = raw[c].str.strip()

    tag_col = find_tag_col(raw)
    keep = raw[raw[tag_col].fillna("").str.contains(TAG_PATTERN)].copy()

    for w in WINDOWS:
        col = CONS_COLS[w]
        keep[col] = to_num(keep[col]) if col in keep.columns else np.nan
    for c in ["unitprice", "contractual_lead_time"]:
        if c in keep.columns:
            keep[c] = to_num(keep[c])
    for c in ID_COLS + ["module", tag_col]:
        if c not in keep.columns:
            keep[c] = np.nan

    # Collapse multiple stockroom rows into one item: consumption summed (the
    # part's total demand), price/lead-time median, categoricals first non-null.
    def first(s: pd.Series):
        s = s.dropna()
        s = s[s.astype(str).str.strip() != ""]
        return s.iloc[0] if len(s) else np.nan

    agg: dict[str, object] = {CONS_COLS[w]: "sum" for w in WINDOWS}
    agg.update({"unitprice": "median", "contractual_lead_time": "median"})
    agg.update({c: first for c in ["item_desc", "machine_type",
                                    "replenishment_policy", "shared_parts",
                                    "module", tag_col]})
    item = keep.groupby(keep["item_id"].str.strip(), as_index=False).agg(agg)
    item = item.rename(columns={tag_col: "module_tags"})
    item["snapshot_ym"] = f"{year:04d}-{month:02d}"
    item["anchor_index"] = _mi(year, month)
    return item


# ---------------------------------------------------------------------------
# De-cumulation -> monthly rows
# ---------------------------------------------------------------------------
def explode_months(item: pd.DataFrame) -> pd.DataFrame:
    """Turn each item's nested windows into one row per (item, month)."""
    rows = []
    for r in item.itertuples(index=False):
        anchor = r.anchor_index
        cons = {w: getattr(r, CONS_COLS[w]) for w in WINDOWS}
        for w, prev, offsets in BUCKETS:
            hi = cons[w]
            lo = 0.0 if prev is None else cons[prev]
            span = len(offsets)
            # NaN in either window -> unknown period (missing, not zero).
            bucket = np.nan if (pd.isna(hi) or pd.isna(lo)) else (hi - lo)
            per_month = np.nan if pd.isna(bucket) else bucket / span
            for off in offsets:
                mi = anchor - off
                # overlap seam: each snapshot only emits the months it owns.
                owns = (mi <= SEAM) if anchor == _mi(2024, 8) else (mi >= SEAM + 1)
                if not owns:
                    continue
                rows.append({
                    "item_id": r.item_id,
                    "month": _ym(mi),
                    "month_index": mi,
                    "monthly_cons": per_month,
                    "source_snapshot": r.snapshot_ym,
                    "bucket_days": w if prev is None else w - prev,
                    "bucket_month_span": span,
                    "is_measured": prev is None,      # only the last_30 month
                })
    return pd.DataFrame(rows)


def build() -> None:
    frames, manifest = [], []
    for name, y, m, sheet in SNAPSHOTS:
        item = load_snapshot(name, y, m, sheet)
        frames.append(item)
        manifest.append({"snapshot": f"{y:04d}-{m:02d}", "file": name,
                         "tcb_epoxy_items": item["item_id"].nunique()})
        print(f"  {y}-{m:02d}  {name[:40]:40s}  TCB/Epoxy items={item['item_id'].nunique():5d}")

    aug, jan = frames[0], frames[1]
    overlap = sorted(set(aug["item_id"]) & set(jan["item_id"]))
    print(f"\noverlap (in BOTH snapshots): {len(overlap):,} items")

    aug_o = aug[aug["item_id"].isin(overlap)]
    jan_o = jan[jan["item_id"].isin(overlap)]

    monthly = pd.concat([explode_months(aug_o), explode_months(jan_o)],
                        ignore_index=True)

    # Static attributes: prefer the latest (Jan'26) snapshot, fall back to Aug'24.
    static = (pd.concat([jan_o, aug_o])
              .drop_duplicates("item_id", keep="first")
              .set_index("item_id")[["item_desc", "module_tags", "module",
                                      "machine_type", "replenishment_policy",
                                      "unitprice", "contractual_lead_time"]])
    monthly = monthly.join(static, on="item_id")

    # Full contiguous month grid so absent months are explicit NaN, not dropped.
    lo, hi = monthly["month_index"].min(), monthly["month_index"].max()
    grid_index = pd.MultiIndex.from_product(
        [overlap, range(lo, hi + 1)], names=["item_id", "month_index"])
    long = (monthly.set_index(["item_id", "month_index"])
            .reindex(grid_index)
            .reset_index())
    long["month"] = long["month_index"].map(_ym)
    long = long.sort_values(["item_id", "month_index"]).reset_index(drop=True)
    # Re-attach static for grid-filled (previously NaN) rows.
    for c in static.columns:
        long[c] = long["item_id"].map(static[c])

    lead = ["item_id", "item_desc", "module_tags", "module", "machine_type",
            "replenishment_policy", "unitprice", "contractual_lead_time",
            "month", "month_index", "monthly_cons", "source_snapshot",
            "bucket_days", "bucket_month_span", "is_measured"]
    long = long[lead]

    wide = (long.pivot_table(index=["item_id", "item_desc", "module_tags"],
                             columns="month", values="monthly_cons", aggfunc="first")
            .reset_index())

    long_path = OUT / "s12_two_snapshot_monthly_long.csv"
    wide_path = OUT / "s12_two_snapshot_monthly_wide.csv"
    man_path = OUT / "s12_two_snapshot_manifest.csv"
    long.to_csv(long_path, index=False, encoding="utf-8-sig")
    wide.to_csv(wide_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(manifest).to_csv(man_path, index=False, encoding="utf-8-sig")

    months = sorted(long["month"].unique())
    measured = int(long["is_measured"].fillna(False).sum())
    filled = int(long["monthly_cons"].notna().sum())
    print("\n" + "=" * 68)
    print(f"overlapping items        : {len(overlap):,}")
    print(f"month span               : {months[0]} .. {months[-1]}  ({len(months)} months)")
    print(f"panel rows (item x month): {len(long):,}")
    print(f"  measured (last_30)     : {measured:,}")
    print(f"  filled (measured+spread): {filled:,}")
    print(f"  NaN (missing)          : {len(long) - filled:,}")
    print("\nwrote:")
    for p in (long_path, wide_path, man_path):
        print(f"   {p.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
