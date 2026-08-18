"""S13 -- Cumulative-window intermittent-demand sizing engine (PRD v3.2).

Treats each snapshot's nested trailing windows (last_5/30/90/180/365/547_day)
as MULTI-HORIZON DEMAND-RATE estimators -- never a time series -- and sizes
Min / ROP / Max from a lead-time-demand distribution at a criticality-scaled
service level. The earlier snapshot supplies a trend annotation only.

Engine anchor : BOM REVIEW_Jan'26 .csv        (a part's current state)
Trend context : AUGUST'24 ... .xlsx           (optional; ~17-month rate delta)
Parts         : new_modulle contains Module-TCB or Module-Epoxy

Outputs (analysis/output/):
    s13_cumulative_features.csv   per-item rates / ratios / trend / routing
    s13_sizing.csv                per-item mu_day, dist, min/rop/max + scoring
    s13_manifest.csv              counts by routing class + scoring summary

See docs/PRD_Technical_BOM_Review_Assistant_v3.md for the full spec.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from common import OUT, to_num
from s12_two_snapshot_monthly import _find_header_row, _find_tag_col, _norm_cols

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "BOM table"

# --all -> size every latest-snapshot item (Aug'24 used for trend only);
# default -> PoC overlap subset (parts present in both snapshots).
OVERLAP_ONLY = "--all" not in sys.argv

# Non-capturing to avoid pandas str.contains match-group warning.
TAG = re.compile(r"module-(?:tcb|epoxy)", re.IGNORECASE)

LATEST = ("BOM REVIEW_Jan'26 .csv", None)
PRIOR = ("AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx", "Upload File")

WINDOWS = [5, 30, 90, 180, 365, 547]
CONS = {w: f"last_{w}_day_cnsmptn_qty" for w in WINDOWS}

# --- config (PRD v3.2 5.x) ---
T_REVIEW = 30                       # review / protection-period days (config default)
DEFAULT_LT = 30                     # fallback lead time (days) when missing
SL_DEFAULT = 0.95                   # service level when criticality is missing
PHI_SEG = {"active": 1.5, "dying": 3.0}   # conservative variance-to-mean floors
KEEP_ALIVE_FLOOR = 1               # insurance stock for critical dormant parts
REGULAR_FREQ = 6                   # frequencymonthswithusage >= this -> candidate Poisson
REGULAR_CV2 = 0.5                  # pseudo_cv2 < this (and regular freq) -> Poisson

NUMERIC = (list(CONS.values()) + [
    "contractual_lead_time", "order_qty_multiple",
    "min_qty", "rop_qty", "max_qty",
    "factory_recommended_new_min", "factory_recommended_new_rop",
    "factory_recommended_new_max",
    "days_since_last_issue", "frequencymonthswithusage",
])
CATEG = ["item_desc", "machine_type", "sfm_criticality", "aging_status",
         "replenishment_policy"]


# ---------------------------------------------------------------------------
# Loading + per-item collapse
# ---------------------------------------------------------------------------
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
        hdr = _find_header_row(path, sheet)
        raw = pd.read_excel(path, sheet_name=sheet, header=hdr, dtype=str)
    raw = _norm_cols(raw)
    for c in raw.columns:
        if hasattr(raw[c], "str"):
            raw[c] = raw[c].str.strip()

    tag_col = _find_tag_col(raw)
    keep = raw[raw[tag_col].fillna("").str.contains(TAG)].copy()
    keep = keep.rename(columns={tag_col: "module_tags"})
    keep["item_id"] = keep["item_id"].str.strip()

    for c in NUMERIC:
        keep[c] = to_num(keep[c]) if c in keep.columns else np.nan
    for c in CATEG + ["module_tags"]:
        if c not in keep.columns:
            keep[c] = np.nan

    agg: dict[str, object] = {CONS[w]: "sum" for w in WINDOWS}
    agg.update({c: "median" for c in [
        "contractual_lead_time", "order_qty_multiple",
        "min_qty", "rop_qty", "max_qty",
        "factory_recommended_new_min", "factory_recommended_new_rop",
        "factory_recommended_new_max",
        "days_since_last_issue", "frequencymonthswithusage"]})
    agg.update({c: _first for c in CATEG + ["module_tags"]})
    item = keep.groupby("item_id", as_index=False).agg(agg)
    return item


# ---------------------------------------------------------------------------
# Cumulative feature dictionary (PRD 3)
# ---------------------------------------------------------------------------
def _mu_day(w: dict[int, float]) -> float:
    """Longest reliable window rate; NaN when nothing is present."""
    for win in (365, 547, 180, 90, 30):
        v = w[win]
        if pd.notna(v):
            return max(v / win, 0.0)
    return float("nan")


def _pos(x) -> bool:
    return pd.notna(x) and x > 0


def _pseudo_cv2(w: dict[int, float]) -> float:
    """Dispersion proxy from de-cumulated (non-overlapping) bucket rates."""
    edges = [(30, 0, 30), (90, 30, 60), (180, 90, 90), (365, 180, 185), (547, 365, 182)]
    rates = []
    for hi, lo, span in edges:
        a, b = w[hi], (0.0 if lo == 0 else w[lo])
        if pd.notna(a) and pd.notna(b):
            rates.append(max(a - b, 0.0) / span)
    if len(rates) < 2:
        return 0.0
    arr = np.array(rates, float)
    m = arr.mean()
    return float(arr.var(ddof=0) / m**2) if m > 0 else 0.0


def features(item: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in item.itertuples(index=False):
        w = {win: getattr(r, CONS[win]) for win in WINDOWS}
        present = [w[win] for win in WINDOWS if pd.notna(w[win])]
        mu = _mu_day(w)
        c90, c365 = w[90], w[365]

        if not present:
            route = "no-data"
        elif max(present) <= 0:
            route = "dormant"
        elif pd.notna(c90) and c90 > 0:
            route = "active"
        else:                                   # demand only older than a quarter
            route = "dying"

        # consumable priority: constant consumers are the priority; non-consumable
        # (dormant / no-data) are not. sustained = demand in both recent + annual.
        if route in ("no-data", "dormant"):
            consumable = "none"
        elif route == "dying":
            consumable = "dying"
        elif _pos(c365) and _pos(c90):
            consumable = "constant"
        else:
            consumable = "sporadic"

        rate90 = (c90 / 90) if pd.notna(c90) else np.nan
        rate365 = (c365 / 365) if pd.notna(c365) else np.nan
        rows.append({
            "item_id": r.item_id,
            "mu_day": mu,
            "rate_90": rate90,
            "rate_365": rate365,
            "momentum": (rate90 / rate365) if (pd.notna(rate90) and pd.notna(rate365)
                                               and rate365 > 0) else np.nan,
            "recent_share": (w[30] / c365) if (pd.notna(w[30]) and pd.notna(c365)
                                               and c365 > 0) else np.nan,
            "pseudo_cv2": _pseudo_cv2(w),
            "route": route,
            "consumable": consumable,
            "priority": consumable == "constant",
        })
    return item.merge(pd.DataFrame(rows), on="item_id")


def add_trend(latest: pd.DataFrame, prior: pd.DataFrame | None) -> pd.DataFrame:
    if prior is None:
        latest["yoy_rate"] = np.nan
        latest["level_delta"] = np.nan
        latest["trend_flag"] = "no-prior"
        return latest
    p = prior[["item_id"] + [CONS[365]]].rename(columns={CONS[365]: "prior_c365"})
    out = latest.merge(p, on="item_id", how="left")
    prior_rate = out["prior_c365"] / 365
    out["yoy_rate"] = np.where(prior_rate > 0, out["rate_365"] / prior_rate, np.nan)
    out["level_delta"] = out[CONS[365]] - out["prior_c365"]
    out["trend_flag"] = np.select(
        [out["yoy_rate"] > 1.2, out["yoy_rate"] < 0.8],
        ["increasing", "decreasing"], default="stable")
    out.loc[out["yoy_rate"].isna(), "trend_flag"] = "unknown"
    return out


# ---------------------------------------------------------------------------
# Sizing (PRD 5)
# ---------------------------------------------------------------------------
def _sl(crit) -> float:
    # sfm_criticality is single-letter (H/M/L/D); map by first char.
    c = str(crit).strip().lower()[:1]
    return {"h": 0.99, "m": 0.95, "l": 0.90, "d": 0.90}.get(c, SL_DEFAULT)


def _is_critical(crit) -> bool:
    return str(crit).strip().lower()[:1] == "h"


def _quantile(mean: float, sl: float, phi: float, use_poisson: bool) -> int:
    if mean <= 0:
        return 0
    if use_poisson:
        return int(poisson.ppf(sl, mean))
    r = mean / (phi - 1.0)                       # phi > 1 guaranteed by PHI_SEG floor
    p = 1.0 / phi
    return int(nbinom.ppf(sl, r, p))


def size_row(f) -> dict:
    route = f.route
    mu = f.mu_day
    L = f.contractual_lead_time if pd.notna(f.contractual_lead_time) and f.contractual_lead_time > 0 else DEFAULT_LT
    moq = int(f.order_qty_multiple) if pd.notna(f.order_qty_multiple) and f.order_qty_multiple >= 1 else 1
    sl = _sl(f.sfm_criticality)

    out = {"L": L, "T": T_REVIEW, "SL": sl, "distribution": None,
           "phi": np.nan, "new_min": np.nan, "new_rop": np.nan, "new_max": np.nan,
           "sizing_note": ""}

    if route == "no-data":
        out["sizing_note"] = "no-data: review"
        return out

    if route == "dormant":
        if _is_critical(f.sfm_criticality):
            out.update(distribution="insurance", new_min=KEEP_ALIVE_FLOOR,
                       new_rop=KEEP_ALIVE_FLOOR, new_max=KEEP_ALIVE_FLOOR + moq,
                       sizing_note="critical dormant: keep-alive")
        else:
            out.update(distribution="none", new_min=0, new_rop=0, new_max=0,
                       sizing_note="non-critical dormant: review")
        return out

    # active / dying -> rate-based sizing
    mu_L = mu * L
    mu_LT = mu * (L + T_REVIEW)
    regular = (pd.notna(f.frequencymonthswithusage)
               and f.frequencymonthswithusage >= REGULAR_FREQ
               and f.pseudo_cv2 < REGULAR_CV2)
    if regular:
        use_poisson, phi = True, 1.0
    else:
        use_poisson = False
        phi = max(PHI_SEG["dying" if route == "dying" else "active"], 1.0 + f.pseudo_cv2)

    rop = _quantile(mu_L, sl, phi, use_poisson)
    max_ = _quantile(mu_LT, sl, phi, use_poisson)
    min_ = max(math.ceil(rop - mu_L), 0)
    max_ = max(max_, rop + moq)
    max_ = int(math.ceil(max_ / moq) * moq)      # round up to MOQ multiple

    out.update(distribution="poisson" if use_poisson else "nbinom",
               phi=round(phi, 3), new_min=int(min_), new_rop=int(rop), new_max=int(max_),
               sizing_note=("dying: demand ceased, review" if route == "dying" else "active"))
    return out


# ---------------------------------------------------------------------------
# Scoring vs engineer decisions (PRD 6)
# ---------------------------------------------------------------------------
def _match(engine, fac, tol_abs=1, tol_rel=0.10) -> bool:
    if pd.isna(engine) or pd.isna(fac):
        return False
    return abs(engine - fac) <= max(tol_abs, tol_rel * abs(fac))


def score(df: pd.DataFrame) -> pd.DataFrame:
    for lvl in ("min", "rop", "max"):
        eng, fac = f"new_{lvl}", f"factory_recommended_new_{lvl}"
        df[f"err_{lvl}"] = df[eng] - df[fac]
        df[f"match_{lvl}"] = [_match(e, fc) for e, fc in zip(df[eng], df[fac])]
    return df


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def build() -> None:
    latest = load_snapshot(*LATEST)
    print(f"  latest (Jan'26): {latest['item_id'].nunique():,} TCB/Epoxy items")

    prior = None
    try:
        prior = load_snapshot(*PRIOR)
        print(f"  prior  (Aug'24): {prior['item_id'].nunique():,} TCB/Epoxy items")
    except (PermissionError, FileNotFoundError, ImportError) as e:
        print(f"  [warn] Aug'24 unavailable ({type(e).__name__}) -> "
              f"sizing from Jan'26 only, no trend/overlap")

    feat = features(latest)
    feat = add_trend(feat, prior)

    # PoC subset: keep parts present in both snapshots (only when overlap mode on).
    if prior is not None:
        overlap = set(latest["item_id"]) & set(prior["item_id"])
        if OVERLAP_ONLY:
            feat = feat[feat["item_id"].isin(overlap)].copy()
            print(f"  overlap (both): {len(overlap):,} items [sizing this subset]")
        else:
            print(f"  overlap (both): {len(overlap):,} (trend only; sizing all {len(feat):,} latest)")

    sized = pd.DataFrame([size_row(r) for r in feat.itertuples(index=False)])
    out = pd.concat([feat.reset_index(drop=True), sized], axis=1)
    out = score(out)

    feat_cols = ["item_id", "item_desc", "module_tags", "machine_type",
                 "sfm_criticality", "route", "consumable", "priority",
                 "mu_day", "rate_90", "rate_365",
                 "momentum", "recent_share", "pseudo_cv2",
                 "yoy_rate", "level_delta", "trend_flag"]
    size_cols = ["item_id", "route", "consumable", "priority", "sfm_criticality",
                 "mu_day", "L", "T", "SL",
                 "distribution", "phi", "new_min", "new_rop", "new_max",
                 "min_qty", "rop_qty", "max_qty",
                 "factory_recommended_new_min", "factory_recommended_new_rop",
                 "factory_recommended_new_max",
                 "err_min", "err_rop", "err_max",
                 "match_min", "match_rop", "match_max", "sizing_note"]

    feat_path = OUT / "s13_cumulative_features.csv"
    size_path = OUT / "s13_sizing.csv"
    man_path = OUT / "s13_manifest.csv"
    out[feat_cols].to_csv(feat_path, index=False, encoding="utf-8-sig")
    out[size_cols].to_csv(size_path, index=False, encoding="utf-8-sig")

    route_counts = out["route"].value_counts()
    cons_counts = out["consumable"].value_counts()
    rate_based = out[out["route"].isin(["active", "dying"])]
    priority = out[out["priority"]]
    manifest = {
        "latest_items": int(latest["item_id"].nunique()),
        "prior_items": int(prior["item_id"].nunique()) if prior is not None else 0,
        "scored_items": int(len(out)),
        **{f"route_{k}": int(v) for k, v in route_counts.items()},
        **{f"consumable_{k}": int(v) for k, v in cons_counts.items()},
        "match_min_priority_%": round(100 * priority["match_min"].mean(), 1) if len(priority) else np.nan,
        "match_rop_priority_%": round(100 * priority["match_rop"].mean(), 1) if len(priority) else np.nan,
        "match_max_priority_%": round(100 * priority["match_max"].mean(), 1) if len(priority) else np.nan,
    }
    pd.DataFrame([manifest]).to_csv(man_path, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 68)
    print(f"scored items: {len(out):,}")
    print("\nconsumable priority:")
    for k, v in cons_counts.items():
        print(f"   {k:10s}: {v:5d}")
    print("\nMin/ROP/Max match vs factory_recommended (|Δ|≤1 or ≤10%):")
    print(f"   {'level':>5s}  {'all':>7s}  {'constant (priority)':>19s}")
    for lvl in ("min", "rop", "max"):
        a = 100 * out[f"match_{lvl}"].mean()
        p = 100 * priority[f"match_{lvl}"].mean() if len(priority) else float("nan")
        print(f"   {lvl:>5s}  {a:6.1f}%  {p:18.1f}%")
    print("\nwrote:")
    for p in (feat_path, size_path, man_path):
        print(f"   {p.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
