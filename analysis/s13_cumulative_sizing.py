"""S13 -- Cumulative-window sizing harness (PRD v3.2).

Runs the PRODUCTION engine (backend/app/engine_statistical.py) over the BOM
review snapshots so its Min/ROP/Max can be graded against the engineers'
values. The sizing maths lives in the engine only -- this script loads,
collapses stockrooms to one row per item, and writes the CSVs s14 grades.

Engine anchor : BOM REVIEW_Jan'26 .csv        (a part's current state)
Overlap set   : AUGUST'24 ... .xlsx           (PoC subset; --all to size everything)
Parts         : new_modulle contains Module-TCB or Module-Epoxy

Outputs (analysis/output/):
    s13_sizing.csv     per-item route/mu_day/min/rop/max + engineer values
    s13_manifest.csv   counts by routing class

See docs/PRD_Technical_BOM_Review_Assistant_v3.md for the full spec.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import OUT, find_header_row, find_tag_col, norm_cols, to_num

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "BOM table"

sys.path.insert(0, str(ROOT / "backend" / "app"))
import engine_statistical  # noqa: E402  (the production sizing engine)

# --all -> size every latest-snapshot item; default -> PoC overlap subset.
OVERLAP_ONLY = "--all" not in sys.argv

# Non-capturing to avoid pandas str.contains match-group warning.
TAG = re.compile(r"module-(?:tcb|epoxy)", re.IGNORECASE)

LATEST = ("BOM REVIEW_Jan'26 .csv", None)
PRIOR = ("AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx", "Upload File")

CONS = engine_statistical.CONS
WINDOWS = engine_statistical.WINDOWS

MEDIAN = ["contractual_lead_time", "order_qty_multiple", "unitprice",
          "min_qty", "rop_qty", "max_qty",
          "factory_recommended_new_min", "factory_recommended_new_rop",
          "factory_recommended_new_max",
          "days_since_last_issue", "frequencymonthswithusage"]
CATEG = ["item_desc", "machine_type", "sfm_criticality", "aging_status",
         "replenishment_policy"]


def _first(s: pd.Series):
    s = s.dropna()
    s = s[s.astype(str).str.strip() != ""]
    return s.iloc[0] if len(s) else np.nan


def load_snapshot(name: str, sheet: str | None) -> pd.DataFrame:
    """Load, filter to TCB/Epoxy tags, collapse stockrooms -> one row per item."""
    path = SRC / name
    if sheet is None:
        raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    else:
        raw = pd.read_excel(path, sheet_name=sheet,
                            header=find_header_row(path, sheet), dtype=str)
    raw = norm_cols(raw)
    for c in raw.columns:
        if hasattr(raw[c], "str"):
            raw[c] = raw[c].str.strip()

    tag_col = find_tag_col(raw)
    keep = raw[raw[tag_col].fillna("").str.contains(TAG)].copy()
    keep = keep.rename(columns={tag_col: "module_tags"})
    keep["item_id"] = keep["item_id"].str.strip()

    for c in list(CONS.values()) + MEDIAN:
        keep[c] = to_num(keep[c]) if c in keep.columns else np.nan
    for c in CATEG + ["module_tags"]:
        if c not in keep.columns:
            keep[c] = np.nan

    agg: dict[str, object] = {CONS[w]: "sum" for w in WINDOWS}
    agg.update({c: "median" for c in MEDIAN})
    agg.update({c: _first for c in CATEG + ["module_tags"]})
    return keep.groupby("item_id", as_index=False).agg(agg)


def build() -> None:
    latest = load_snapshot(*LATEST)
    print(f"  latest (Jan'26): {latest['item_id'].nunique():,} TCB/Epoxy items")

    prior = None
    try:
        prior = load_snapshot(*PRIOR)
        print(f"  prior  (Aug'24): {prior['item_id'].nunique():,} TCB/Epoxy items")
    except (PermissionError, FileNotFoundError, ImportError) as e:
        print(f"  [warn] Aug'24 unavailable ({type(e).__name__}) -> sizing Jan'26 only")

    items = latest
    if prior is not None and OVERLAP_ONLY:
        overlap = set(latest["item_id"]) & set(prior["item_id"])
        items = latest[latest["item_id"].isin(overlap)].copy()
        print(f"  overlap (both): {len(overlap):,} items [sizing this subset]")

    items = items.reset_index(drop=True)
    # engine output reuses the factory_recommended_* names; rename to new_* so the
    # engineers' columns of the same name survive the join and stay comparable.
    sized = engine_statistical.run(items).rename(columns={
        "factory_recommended_new_min": "new_min",
        "factory_recommended_new_rop": "new_rop",
        "factory_recommended_new_max": "new_max",
    })
    out = items.drop(columns=["item_id"]).join(sized)

    cols = ["item_id", "item_desc", "module_tags", "machine_type", "sfm_criticality",
            "route", "consumable", "mu_day", "new_min", "new_rop", "new_max",
            "min_qty", "rop_qty", "max_qty",
            "factory_recommended_new_min", "factory_recommended_new_rop",
            "factory_recommended_new_max",
            "review_required", "risk_level", "confidence_score", "reason_code"]
    size_path, man_path = OUT / "s13_sizing.csv", OUT / "s13_manifest.csv"
    out[cols].to_csv(size_path, index=False, encoding="utf-8-sig")

    route_counts = out["route"].value_counts()
    cons_counts = out["consumable"].value_counts()
    manifest = {
        "latest_items": int(latest["item_id"].nunique()),
        "prior_items": int(prior["item_id"].nunique()) if prior is not None else 0,
        "scored_items": int(len(out)),
        "review_required": int((out["review_required"] == "Y").sum()),
        **{f"route_{k}": int(v) for k, v in route_counts.items()},
        **{f"consumable_{k}": int(v) for k, v in cons_counts.items()},
    }
    pd.DataFrame([manifest]).to_csv(man_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 68)
    print(f"scored items: {len(out):,}  (review_required: {manifest['review_required']:,})")
    print("\nconsumable priority:")
    for k, v in cons_counts.items():
        print(f"   {k:10s}: {v:5d}")
    print("\nwrote:")
    for p in (size_path, man_path):
        print(f"   {p.relative_to(ROOT)}")
    print("grade with: python s14_validate.py")


if __name__ == "__main__":
    build()
