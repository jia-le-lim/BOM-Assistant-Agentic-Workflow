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


# --- auto-clear grading rule (s15, s17) -------------------------------------
# "Review would have added nothing": the engine value is within 1 unit or 10% of
# the engineer's, on all three levels. One definition, so two harnesses grading
# the same question cannot quietly drift apart.
#
# NO LONGER COUPLED to engine_statistical._agreement -- read this before assuming
# they match. As of 2026-08-28 the engine's agreement rule is a strict 10% band
# on Max and ROP only (engine_statistical.close_enough, AGREE_TOL/AGREE_FIELDS)
# with no absolute floor. This predicate deliberately keeps the old
# max(1.0, 0.10*|b|) on all three levels, because it answers a different
# question: not "did the engine reproduce the decision" (the scorecard) but "was
# the engine close enough that a human review would have added nothing"
# (auto-clear precision), where a 1-unit difference on a 2-unit part genuinely is
# immaterial to the reviewer.
#
# OPEN DECISION: s17 asks the ENGINE whether agreement=='match' and then computes
# precision with THIS predicate. Those are now two different rules inside one
# calculation, so s17's precision figure needs re-deriving before it is quoted
# again -- either grade both ends with the engine's rule, or stop reading the
# engine's verdict and grade entirely here. Do not quote s17_clear_threshold.csv
# until that is settled.
#
# Kept as a separate implementation rather than an import because every analysis
# script inserts backend/app on sys.path directly, so importing it here would
# load engine_statistical a second time under a different module name.
TARGET_PRECISION = 98.0
LEVELS = ("max", "rop", "min")


def agree(eng: np.ndarray, fac: np.ndarray, tol_abs=1.0, tol_rel=0.10) -> np.ndarray:
    return np.abs(eng - fac) <= np.maximum(tol_abs, tol_rel * np.abs(fac))


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


BLANK_RUN_END = 1000   # consecutive blank item_id rows that mean "data ended"


def read_module_rows(path: Path, sheet: str | None, wanted: list[str],
                     module: str = MODULE) -> pd.DataFrame:
    """One streaming pass over a snapshot -> `module` rows, `wanted` columns only.

    Two of the BOM workbooks are ~340 MB, and that size is padding, not data:
    Dec'24 carries 1,026,817 rows / 117.4M cells of which only 1.25M are
    non-empty -- Excel styled its way to the sheet limit around ~23k real rows.
    ``pd.read_excel`` pays for all of it twice (``find_header_row``'s ``nrows=20``
    probe still hands openpyxl the whole sheet first): 704s for that one file.
    Streaming read_only and stopping at the end of the data reads ~1% of it.

    Values come back as openpyxl typed them (numbers stay numbers, blanks are
    None); ``to_num`` and the string casts downstream handle both shapes.
    """
    if path.suffix.lower() == ".csv":
        raw = norm_cols(pd.read_csv(path, dtype=str, encoding="utf-8-sig",
                                    keep_default_na=False))
        raw = raw[raw["module"].astype(str).str.strip().str.upper() == module]
        return pd.DataFrame(
            {c: (raw[c] if c in raw.columns else np.nan) for c in wanted},
            index=raw.index,
        ).reset_index(drop=True)

    import openpyxl  # local: only the xlsx path needs it

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = wb[sheet].iter_rows(values_only=True)
        pos: dict[str, int] = {}
        for r in rows:                       # header == first row carrying item_id
            names = [str(c).strip().lower() if c is not None else "" for c in r]
            if "item_id" in names:
                for i, nm in enumerate(names):
                    pos.setdefault(nm, i)    # first occurrence wins, as pandas does
                break
        if "module" not in pos:
            raise ValueError(f"{path.name}: no 'module' column")
        mi, ii = pos["module"], pos["item_id"]
        take = [(c, pos[c]) for c in wanted if c in pos]
        idx = [i for _, i in take]

        data, blank_run = [], 0
        for r in rows:
            key = r[ii] if ii < len(r) else None
            if key is None or str(key).strip() == "":
                # ponytail: the padded tail is the whole cost, so stop once the
                # data has plainly ended rather than walking to row 1,048,576.
                # Ceiling: a workbook with a genuine >BLANK_RUN_END gap mid-sheet
                # would be truncated -- raise the constant if one ever shows up.
                blank_run += 1
                if blank_run >= BLANK_RUN_END:
                    break
                continue
            blank_run = 0
            mod = r[mi] if mi < len(r) else None
            if mod is None or str(mod).strip().upper() != module:
                continue
            data.append([r[i] if i < len(r) else None for i in idx])
    finally:
        wb.close()

    out = pd.DataFrame(data, columns=[c for c, _ in take], dtype=object)
    for c in wanted:
        if c not in out.columns:
            out[c] = np.nan
    return out[wanted]
