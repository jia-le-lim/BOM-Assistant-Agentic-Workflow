"""Shared loading / typing helpers for the Jan'26 BOM review analysis.

Scope: TCB module only (confirmed with owner, 2026-07-28).
Source: BOM table/BOM REVIEW_Jan'26 .csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "BOM table" / "BOM REVIEW_Jan'26 .csv"
OUT = ROOT / "analysis" / "output"
OUT.mkdir(parents=True, exist_ok=True)

MODULE = "TCB"

# Columns the PRD (section 5.1) classifies as engine OUTPUT -- never an ML input.
OUTPUT_COLS = [
    "factory_recommended_new_max",
    "factory_recommended_new_rop",
    "factory_recommended_new_min",
]

# PRD section 5.1 "memory / decision history" -- store as history, never as ML input.
MEMORY_COLS = [
    "justification",
    "comments",
    "review_acknowledge",
    "rop_adoption",
    "max_adoption",
    "ooq_adoption",
    "modified_user",
    "modified_date",
]

# PRD section 5.1 candidate recommendation signals -- inputs, not truth.
CANDIDATE_COLS = [
    "sfm_recommendation",
    "sfm_max", "sfm_rop", "sfm_min",
    "sfm_brr_max", "sfm_brr_rop", "sfm_brr_min",
    "atm_recommended_max", "atm_recommended_rop", "atm_recommended_min",
    "ds_recommended_max", "ds_recommended_rop", "ds_recommended_min",
    "dp_recommended_max", "dp_recommended_rop", "dp_recommended_min",
    "sims_recommended_max", "sims_recommended_rop", "sims_recommended_min",
    "one_msia_max", "one_msia_rop", "one_msia_min",
    "recom_max", "recom_rop", "recom_min",
]

SENSITIVE_COLS = [
    "unitprice", "supplier_name", "machine_type", "factory", "site",
    "stockroom_id", "stockroom_name", "item_gl_account", "spending_impact",
    "comments", "inventory_owner", "area_owner", "modified_user",
]

# Columns that should parse as numeric for the analysis.
NUMERIC_COLS = [
    "max_qty", "rop_qty", "min_qty", "unitprice", "avail_qty", "vf_avail_qty",
    "contractual_lead_time", "order_qty_multiple", "consignment_qty",
    "last_547_day_cnsmptn_qty", "last_365_day_cnsmptn_qty",
    "last_180_day_cnsmptn_qty", "last_90_day_cnsmptn_qty",
    "last_30_day_cnsmptn_qty", "last_5_day_cnsmptn_qty",
    "days_since_last_issue", "frequencymonthswithusage",
    "spending_impact", "max_delta", "rop_delta",
    "qry_eoh_excess_qty", "open_po_qty", "qry_msbidataopen_simi_shp_qty",
    "other_stkrm_30d_cons_qty", "other_stkrm_90d_cons_qty",
    "other_stkrm_180d_cons_qty", "other_stkrm_365d_cons_qty",
    "qry_msbidatadays_since_activation", "qry_msbidatadays_since_effective",
    "sfm_mean_lt_cd", "onhand_pallet_qty", "ea_per_pallet",
    "avail_doi_maxd",
] + OUTPUT_COLS + [c for c in CANDIDATE_COLS if c != "sfm_recommendation"]


def load_raw() -> pd.DataFrame:
    """Everything as string, so nothing is silently coerced before profiling."""
    return pd.read_csv(SRC, dtype=str, encoding="utf-8-sig", keep_default_na=False)


def to_num(s: pd.Series) -> pd.Series:
    """Blank/whitespace -> NaN, everything else numeric-coerced."""
    return pd.to_numeric(s.astype(str).str.strip().replace("", np.nan), errors="coerce")


def load_typed(module: str | None = MODULE) -> pd.DataFrame:
    df = load_raw()
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        # pandas >=3.0 infers `str` dtype, not `object` -- check for the accessor.
        if hasattr(df[c], "str"):
            df[c] = df[c].str.strip()
    if module is not None:
        df = df[df["module"] == module].copy()
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = to_num(df[c])
    return df


def blank(s: pd.Series) -> pd.Series:
    return s.isna() | (s.astype(str).str.strip() == "")


def pct(n, d) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


# --- multi-snapshot loading (BOM review workbooks drift in shape) ------------
def norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def find_header_row(path: Path, sheet: str, scan: int = 20) -> int:
    """Workbooks carry banner rows above the header; find the item_id row."""
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=scan, dtype=str)
    for i in range(len(probe)):
        if "item_id" in [str(c).strip().lower() for c in probe.iloc[i].values]:
            return i
    return 0


def find_tag_col(df: pd.DataFrame) -> str:
    """Locate the multi-tag module column, tolerating naming drift across months."""
    for cand in ("new_modulle", "new_module", "module_tags", "modules"):
        if cand in df.columns:
            return cand
    for c in df.columns:
        if not hasattr(df[c], "str"):
            continue
        sample = df[c].dropna().astype(str).head(200)
        if len(sample) and (sample.str.contains("module-", case=False)).mean() > 0.3:
            return c
    raise ValueError("no multi-tag module column (e.g. 'new_modulle') found")
