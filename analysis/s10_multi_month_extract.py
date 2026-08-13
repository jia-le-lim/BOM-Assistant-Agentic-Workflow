"""S10 -- multi-month TCB extraction + consumption-trend feature build.

Reads every monthly BOM review workbook/CSV in ``BOM table/``, filters to the
TCB module, keeps the review-relevant key columns, stamps each row with the
snapshot month, and produces three artefacts in ``analysis/output/``:

    s10_tcb_panel_long.csv        tidy panel  -- one row per (item_id, month)
    s10_tcb_consumption_trend.csv per-item    -- pivoted monthly consumption +
                                                 trend aggregates + trend label
    s10_extract_manifest.csv      provenance  -- one row per source file

Design notes
------------
* The monthly files have *heterogeneous scope* (some are full site dumps, some
  are small curated review lists), so an item that is absent in a month means
  "not in that month's review scope", NOT "zero consumption". Absent months are
  therefore left as NaN and never filled with 0. A genuine in-file 0 is kept.
* ``month_index = year*12 + (month-1)`` is used for every slope/trend metric so
  that calendar gaps (missing Nov'24, Jan-Feb'25, the jump to Jan'26) are
  handled correctly instead of treating snapshots as evenly spaced.
* The trailing-window consumption columns (last_5/30/90/180/365/547_day) are
  self-contained actuals attached to each snapshot, so they carry the trend
  signal even when scope changes. ``last_90_day`` is used as the primary trend
  series (smoothed but responsive); ``last_30_day`` is kept as the responsive
  secondary; ``last_365_day`` as the annualised direction check.
* An item recurring in consecutive monthly reviews is expected (it is re-
  reviewed even if reviewed before) -- it stays the SAME item (keyed on
  item_id), and its consumption is accumulated across those reviews. Because
  the windows overlap, the consumption *since the previous review* is
  approximated with the trailing window whose length best matches the gap, and
  those non-overlapping pieces are cumulated into ``cum_cons_est``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from common import OUT, to_num

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "BOM table"
MODULE = "TCB"

# A month whose TCB consumption is byte-identical to an earlier month is treated
# as an accidental file duplicate (e.g. Mar'25 == Apr'25) and dropped so it does
# not double-count in the cumulative. Set False to keep every dated snapshot.
DROP_IDENTICAL_SNAPSHOTS = True

# ---------------------------------------------------------------------------
# Source file -> (year, month, data-sheet).  Sheet is the *full* TCB dump for
# each workbook (verified by inspection); ``None`` == CSV.  Any new file dropped
# into BOM table/ that is not listed here is auto-resolved (see resolve_files).
# ---------------------------------------------------------------------------
EXPLICIT: dict[str, tuple[int, int, str | None]] = {
    "AE_JULY'24 - BOM REVIEW - Factory Cost Rep Review_.xlsx": (2024, 7, "Upload List"),
    "AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx": (2024, 8, "Upload File"),
    "Sept_24 BOM REVIEW.xlsx": (2024, 9, "sheet1"),
    "Oct_24 BOM REVIEW.xlsx": (2024, 10, "Raw"),
    "Dec_24 BOM REVIEW.xlsx": (2024, 12, "49510_DEC'24"),
    "March_2025_BOM REVIEW_UN94X4_8ae0dbe9591.xlsx": (2025, 3, "sheet1"),
    "Apr_2025_BOM REVIEW_ww17.xlsx": (2025, 4, "Ignore this"),
    "May'2025_BOM REVIEW.xlsx": (2025, 5, "49510"),
    "BOM REVIEW_Jan'26 .csv": (2026, 1, None),
}

# Files to ignore entirely (synthetic / demo).
SKIP_SUBSTR = ("demo",)

# Consumption trailing windows present in every file (ascending days).
CONS_WINDOWS = [5, 30, 90, 180, 365, 547]
CONS_COLS = {w: f"last_{w}_day_cnsmptn_qty" for w in CONS_WINDOWS}

PRIMARY_WIN = 90          # primary trend series
SECONDARY_WIN = 30        # responsive series also pivoted
ANNUAL_WIN = 365          # long-run direction check

# Identity + review-relevant key columns (user spec).  Everything here is kept
# in the long panel; missing columns in a given file are created as NaN.
ID_COLS = ["item_id", "stockroom_id", "stockroom_name", "factory", "site"]
CAT_FEATURES = [
    "item_desc", "module", "machine_type", "replenishment_policy",
    "shared_parts", "shareable_indicator", "sfm_recommendation",
    "repair_type", "partfreq",
]
NUM_FEATURES = [
    "unitprice", "contractual_lead_time",
    "max_qty", "rop_qty", "min_qty",
    "factory_recommended_new_max", "factory_recommended_new_rop",
    "factory_recommended_new_min",
    "days_since_last_issue", "frequencymonthswithusage", "order_qty_multiple",
] + list(CONS_COLS.values())

ALL_WANTED = ID_COLS + CAT_FEATURES + NUM_FEATURES


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _find_header_row(path: Path, sheet: str, scan: int = 20) -> int:
    """Locate the header row (the one containing 'item_id') in an xlsx sheet."""
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=scan, dtype=str)
    for i in range(len(probe)):
        cells = [str(c).strip().lower() for c in probe.iloc[i].values]
        if "item_id" in cells:
            return i
    return 0


def load_one(path: Path, year: int, month: int, sheet: str | None) -> pd.DataFrame:
    """Load a single source file, filter to TCB, keep wanted columns, stamp month."""
    if path.suffix.lower() == ".csv":
        raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    else:
        hdr = _find_header_row(path, sheet)
        raw = pd.read_excel(path, sheet_name=sheet, header=hdr, dtype=str)
    raw = _norm_cols(raw)

    if "module" not in raw.columns:
        raise ValueError(f"{path.name}: no 'module' column")
    raw = raw[raw["module"].astype(str).str.strip().str.upper() == MODULE].copy()

    # Trim whitespace on string cells.
    for c in raw.columns:
        if hasattr(raw[c], "str"):
            raw[c] = raw[c].str.strip()

    # Keep only wanted columns; create any that are absent as NaN.
    keep = pd.DataFrame(index=raw.index)
    for c in ALL_WANTED:
        keep[c] = raw[c] if c in raw.columns else np.nan
    for c in NUM_FEATURES:
        keep[c] = to_num(keep[c])

    keep.insert(0, "snapshot_month", f"{year:04d}-{month:02d}")
    keep.insert(1, "snapshot_ym", year * 100 + month)
    keep.insert(2, "month_index", year * 12 + (month - 1))
    keep.insert(3, "source_file", path.name)
    keep.insert(4, "source_sheet", sheet if sheet else "(csv)")
    return keep


def _safe_to_csv(df: pd.DataFrame, path: Path) -> Path:
    """Write CSV; if the target is locked (open in Excel), fall back to *.new.csv."""
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path
    except PermissionError:
        alt = path.with_name(path.stem + ".new.csv")
        df.to_csv(alt, index=False, encoding="utf-8-sig")
        print(f"  [warn] {path.name} is locked (open in Excel?) -> wrote {alt.name} instead")
        return alt


def resolve_files() -> list[tuple[Path, int, int, str | None]]:
    """Return the ordered (path, year, month, sheet) plan for present files."""
    plan: list[tuple[Path, int, int, str | None]] = []
    for name, (y, m, sheet) in EXPLICIT.items():
        p = SRC / name
        if p.exists():
            plan.append((p, y, m, sheet))
        else:
            print(f"  [warn] configured file missing: {name}")
    plan.sort(key=lambda t: (t[1], t[2]))
    return plan


# ---------------------------------------------------------------------------
# Duplicate-snapshot detection (March'25 vs April'25)
# ---------------------------------------------------------------------------
def _fingerprint(df: pd.DataFrame) -> str:
    """Stable hash of the TCB subset: sorted (item_id, last_365_day) pairs."""
    key = CONS_COLS[365]
    pairs = (
        df[["item_id", key]]
        .assign(item_id=lambda d: d["item_id"].astype(str).str.strip(),
                v=lambda d: d[key].round(3).fillna(-1))
        .sort_values("item_id")
        .loc[:, ["item_id", "v"]]
    )
    return hashlib.md5(pairs.to_csv(index=False).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Item x month collapse (dissolve multiple stockroom rows into one item-month)
# ---------------------------------------------------------------------------
def _mode_or_first(s: pd.Series):
    s = s.dropna()
    s = s[s.astype(str).str.strip() != ""]
    if s.empty:
        return np.nan
    m = s.mode()
    return m.iloc[0] if not m.empty else s.iloc[0]


def collapse_item_month(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per (item_id, snapshot). Consumption summed across stockrooms,
    prices/lead time median, categoricals mode."""
    keys = ["snapshot_month", "snapshot_ym", "month_index", "item_id"]
    agg: dict[str, object] = {}
    for c in CONS_COLS.values():
        agg[c] = "sum"
    for c in ["unitprice", "contractual_lead_time", "max_qty", "rop_qty", "min_qty",
              "factory_recommended_new_max", "factory_recommended_new_rop",
              "factory_recommended_new_min", "days_since_last_issue",
              "frequencymonthswithusage", "order_qty_multiple"]:
        agg[c] = "median"
    for c in CAT_FEATURES:
        if c != "module":
            agg[c] = _mode_or_first
    agg["stockroom_id"] = lambda s: s.nunique(dropna=True)  # -> n_stockrooms

    out = panel.groupby(keys, as_index=False).agg(agg)
    out = out.rename(columns={"stockroom_id": "n_stockrooms"})
    out["module"] = MODULE
    out["n_rows_in_month"] = (
        panel.groupby(keys, as_index=False).size().rename(columns={"size": "n"})["n"]
    )
    return out


def add_rates(df: pd.DataFrame) -> pd.DataFrame:
    """Per-snapshot consumption rates + intra-snapshot momentum."""
    df = df.copy()
    for w in CONS_WINDOWS:
        df[f"cons_rate_{w}d"] = df[CONS_COLS[w]] / w
    with np.errstate(divide="ignore", invalid="ignore"):
        df["cons_momentum_30_over_365"] = df["cons_rate_30d"] / df["cons_rate_365d"]
        df["cons_recent_share_30_of_365"] = df[CONS_COLS[30]] / df[CONS_COLS[365]]
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    return df


def _closest_window(gap_days: float) -> int:
    """Trailing window (days) whose length best matches the inter-review gap."""
    return min(CONS_WINDOWS, key=lambda w: abs(w - gap_days))


def add_cumulative(df: pd.DataFrame) -> pd.DataFrame:
    """Running cumulative consumption per item across its recurring reviews.

    ``gap_months``  -- calendar months since the item's previous review (NaN on
                       first appearance).
    ``period_cons_est`` -- consumption since the previous review, taken from the
                       trailing window that best matches that gap (first
                       appearance uses the trailing 30-day window). A present
                       month with a blank consumption cell counts as 0.
    ``cum_cons_est`` -- cumulative sum of ``period_cons_est`` over time.
    """
    df = df.sort_values(["item_id", "month_index"]).copy()
    df["gap_months"] = df.groupby("item_id")["month_index"].diff()
    df["period_cons_window_days"] = df["gap_months"].apply(
        lambda g: 30 if pd.isna(g) else _closest_window(g * 30.4)
    ).astype(int)
    df["period_cons_est"] = 0.0
    for w in CONS_WINDOWS:
        m = df["period_cons_window_days"] == w
        if m.any():
            df.loc[m, "period_cons_est"] = df.loc[m, CONS_COLS[w]].fillna(0.0).to_numpy()
    df["cum_cons_est"] = df.groupby("item_id")["period_cons_est"].cumsum().round(3)
    return df


# ---------------------------------------------------------------------------
# Trend build (per item across months)
# ---------------------------------------------------------------------------
def _slope_per_month(month_idx: np.ndarray, vals: np.ndarray) -> float:
    mask = ~np.isnan(vals)
    if mask.sum() < 2:
        return np.nan
    x, y = month_idx[mask], vals[mask]
    if np.ptp(x) == 0:
        return np.nan
    return float(np.polyfit(x, y, 1)[0])


def _trend_label(vals: np.ndarray, slope_norm: float) -> str:
    present = vals[~np.isnan(vals)]
    n = present.size
    if n == 0:
        return "NONE"
    if n == 1:
        return "SINGLE_SNAPSHOT"
    if (present == 0).all():
        return "DORMANT"                       # present but never consumed (90d)
    zero_share = float((present == 0).mean())
    if zero_share >= 0.40:
        return "INTERMITTENT"                  # sporadic: some zero, some positive
    if np.isnan(slope_norm):
        return "STABLE"
    if slope_norm > 0.10:
        return "INCREASING"
    if slope_norm < -0.10:
        return "DECREASING"
    return "STABLE"                            # actively consumed, steady rate


def build_trend(item_month: pd.DataFrame, months: list[int]) -> pd.DataFrame:
    """Pivot monthly consumption + compute per-item trend aggregates/labels."""
    ym_by_index = dict(zip(item_month["month_index"], item_month["snapshot_ym"]))
    prim = CONS_COLS[PRIMARY_WIN]

    # Pivot the primary + secondary windows to wide monthly columns.
    def pivot(win: int, tag: str) -> pd.DataFrame:
        p = item_month.pivot_table(index="item_id", columns="snapshot_ym",
                                   values=CONS_COLS[win], aggfunc="sum")
        p.columns = [f"{tag}_{int(ym)}" for ym in p.columns]
        return p

    piv90 = pivot(PRIMARY_WIN, f"cons{PRIMARY_WIN}")
    piv30 = pivot(SECONDARY_WIN, f"cons{SECONDARY_WIN}")

    rows = []
    latest_idx = max(months)
    for item_id, g in item_month.sort_values("month_index").groupby("item_id"):
        idx = g["month_index"].to_numpy()
        prim_vals = g[prim].to_numpy(dtype=float)
        ann_vals = g[CONS_COLS[ANNUAL_WIN]].to_numpy(dtype=float)

        present_mask = ~np.isnan(prim_vals)
        present_idx = idx[present_mask]
        first_i = int(present_idx.min()) if present_idx.size else int(idx.min())
        last_i = int(present_idx.max()) if present_idx.size else int(idx.max())

        mean = float(np.nanmean(prim_vals)) if present_mask.any() else np.nan
        std = float(np.nanstd(prim_vals, ddof=0)) if present_mask.any() else np.nan
        slope = _slope_per_month(idx, prim_vals)
        slope_norm = slope / mean if (mean and mean > 0 and not np.isnan(slope)) else np.nan
        ann_slope = _slope_per_month(idx, ann_vals)

        pv = prim_vals[present_mask]
        first_v = float(pv[0]) if pv.size else np.nan
        last_v = float(pv[-1]) if pv.size else np.nan
        pct_change = ((last_v - first_v) / first_v) if (pv.size and first_v) else np.nan
        cons90_max = float(np.nanmax(prim_vals)) if present_mask.any() else np.nan
        ann_present = ann_vals[~np.isnan(ann_vals)]
        ever_consumed_365 = bool((ann_present > 0).any()) if ann_present.size else False

        # latest-snapshot acceleration: 30d rate vs 90d rate in the most recent month present
        last_row = g.loc[g["month_index"] == last_i].iloc[-1]
        r30 = last_row[CONS_COLS[30]] / 30
        r90 = last_row[CONS_COLS[90]] / 90
        is_accel = bool(r30 > r90) if pd.notna(r30) and pd.notna(r90) else np.nan

        # cumulative consumption across this item's recurring reviews
        reviews_count = int(len(g))
        cum_total = float(np.nanmax(g["cum_cons_est"].to_numpy())) if reviews_count else np.nan

        rows.append({
            "item_id": item_id,
            "item_desc": _mode_or_first(g["item_desc"]),
            "module": MODULE,
            "machine_type": _mode_or_first(g["machine_type"]),
            "replenishment_policy_latest": g.sort_values("month_index")["replenishment_policy"].dropna().iloc[-1]
            if g["replenishment_policy"].notna().any() else np.nan,
            "shared_parts_latest": g.sort_values("month_index")["shared_parts"].dropna().iloc[-1]
            if g["shared_parts"].notna().any() else np.nan,
            "unitprice_latest": g.sort_values("month_index")["unitprice"].dropna().iloc[-1]
            if g["unitprice"].notna().any() else np.nan,
            "contractual_lead_time_latest": g.sort_values("month_index")["contractual_lead_time"].dropna().iloc[-1]
            if g["contractual_lead_time"].notna().any() else np.nan,
            "n_months_present": int(present_mask.sum()),
            "coverage_ratio": round(present_mask.sum() / len(months), 3),
            "first_month": ym_by_index.get(first_i),
            "last_month": ym_by_index.get(last_i),
            "span_months": last_i - first_i,
            "is_new_recent": bool(first_i == latest_idx),
            f"cons{PRIMARY_WIN}_first": round(first_v, 3) if pd.notna(first_v) else np.nan,
            f"cons{PRIMARY_WIN}_last": round(last_v, 3) if pd.notna(last_v) else np.nan,
            f"cons{PRIMARY_WIN}_max": round(cons90_max, 3) if pd.notna(cons90_max) else np.nan,
            "ever_consumed_365": ever_consumed_365,
            f"cons{PRIMARY_WIN}_mean": round(mean, 3) if pd.notna(mean) else np.nan,
            f"cons{PRIMARY_WIN}_std": round(std, 3) if pd.notna(std) else np.nan,
            f"cons{PRIMARY_WIN}_cv": round(std / mean, 3) if (mean and mean > 0) else np.nan,
            f"cons{PRIMARY_WIN}_delta_abs": round(last_v - first_v, 3) if pd.notna(first_v) and pd.notna(last_v) else np.nan,
            f"cons{PRIMARY_WIN}_pct_change": round(pct_change, 3) if pd.notna(pct_change) else np.nan,
            f"cons{PRIMARY_WIN}_slope_per_month": round(slope, 4) if pd.notna(slope) else np.nan,
            f"cons{PRIMARY_WIN}_slope_norm": round(slope_norm, 4) if pd.notna(slope_norm) else np.nan,
            "annual365_slope_per_month": round(ann_slope, 4) if pd.notna(ann_slope) else np.nan,
            "is_accelerating_latest": is_accel,
            "reviews_count": reviews_count,
            "latest_last_365_cons": round(float(last_row[CONS_COLS[365]]), 3) if pd.notna(last_row[CONS_COLS[365]]) else np.nan,
            "latest_last_547_cons": round(float(last_row[CONS_COLS[547]]), 3) if pd.notna(last_row[CONS_COLS[547]]) else np.nan,
            "cum_cons_est_total": round(cum_total, 3) if pd.notna(cum_total) else np.nan,
            "avg_cons_per_review": round(cum_total / reviews_count, 3) if (reviews_count and pd.notna(cum_total)) else np.nan,
            "trend_label": _trend_label(prim_vals, slope_norm),
        })

    trend = pd.DataFrame(rows).set_index("item_id")
    trend = trend.join(piv90).join(piv30).reset_index()
    return trend


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main() -> None:
    plan = resolve_files()
    print(f"Extracting TCB from {len(plan)} monthly files ...\n")

    frames: list[pd.DataFrame] = []
    manifest: list[dict] = []
    seen_fp: dict[str, str] = {}

    for path, y, m, sheet in plan:
        if any(sub in path.name.lower() for sub in SKIP_SUBSTR):
            continue
        df = load_one(path, y, m, sheet)
        fp = _fingerprint(df)
        dup_of = seen_fp.get(fp)
        if dup_of and DROP_IDENTICAL_SNAPSHOTS:
            status = f"DROPPED_DUPLICATE_OF:{dup_of}"
            print(f"  [dup] {path.name} ({y}-{m:02d}) is identical to {dup_of} "
                  f"-> dropped from panel & trend (avoids double-count)")
        else:
            status = f"KEPT_DUPLICATE_OF:{dup_of}" if dup_of else "ok"
            seen_fp.setdefault(fp, f"{y}-{m:02d}")
            frames.append(df)
        manifest.append({
            "snapshot_month": f"{y}-{m:02d}", "source_file": path.name,
            "source_sheet": sheet or "(csv)", "tcb_rows": len(df),
            "unique_items": df["item_id"].nunique(), "status": status,
        })
        print(f"  {y}-{m:02d}  {path.name[:42]:42s}  TCB rows={len(df):5d}  "
              f"items={df['item_id'].nunique():5d}  [{status}]")

    panel = pd.concat(frames, ignore_index=True)

    # ---- item x month collapse + per-snapshot rates + cumulative ----
    item_month = collapse_item_month(panel)
    item_month = add_rates(item_month)
    item_month = add_cumulative(item_month)
    item_month = item_month.sort_values(["item_id", "month_index"]).reset_index(drop=True)

    months = sorted(item_month["month_index"].unique())
    trend = build_trend(item_month, months)

    # ---- persist ----
    long_path = OUT / "s10_tcb_panel_long.csv"
    trend_path = OUT / "s10_tcb_consumption_trend.csv"
    man_path = OUT / "s10_extract_manifest.csv"
    _safe_to_csv(item_month, long_path)
    _safe_to_csv(trend, trend_path)
    _safe_to_csv(pd.DataFrame(manifest), man_path)

    # ---- summary ----
    print("\n" + "=" * 70)
    print(f"panel (item x month) rows : {len(item_month)}")
    print(f"distinct TCB items        : {item_month['item_id'].nunique()}")
    print(f"snapshots (months)        : {len(months)}")
    print("\nitems by #months present:")
    counts = trend["n_months_present"].value_counts().sort_index()
    for k, v in counts.items():
        print(f"   present in {k:2d} month(s): {v:5d} items")
    print("\ntrend_label distribution:")
    for k, v in trend["trend_label"].value_counts().items():
        print(f"   {k:16s}: {v:5d}")
    multi = trend[trend["n_months_present"] >= 2]
    print(f"\nitems trackable across >=2 months: {len(multi)} "
          f"(these have a usable consumption trend)")
    consumed = trend[trend["cum_cons_est_total"] > 0]
    print(f"\ncumulative consumption (stitched, gap-matched estimate):")
    print(f"   items with cumulative consumption > 0 : {len(consumed)}")
    print(f"   total TCB units consumed (all items)  : {trend['cum_cons_est_total'].sum():,.0f}")
    print("\nwrote:")
    for p in (long_path, trend_path, man_path):
        print(f"   {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
