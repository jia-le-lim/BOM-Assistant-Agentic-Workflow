"""S14 -- Validation scorecard: s13 engine Min/ROP/Max vs Jan'26 engineer values.

Reads analysis/output/s13_sizing.csv and grades the engine against the
engineers' `factory_recommended_new_{min,rop,max}` on the tested items.

Metrics per level, per routing subset:
    n          items compared (both values present)
    exact%     engine == engineer
    within%    |Δ| <= 1 unit OR <= 10%
    MAE        mean |engine - engineer|
    bias       mean (engine - engineer)   (+ over-stock, - under-stock vs engineer)
    spearman   rank agreement
    dir%       engine and engineer move the SAME way vs current qty

Output: analysis/output/s14_validation_scorecard.csv (+ console).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "output"
SIZING = OUT / "s13_sizing.csv"

LEVELS = ["min", "rop", "max"]
CURRENT = {"min": "min_qty", "rop": "rop_qty", "max": "max_qty"}


def _within(e, f, tol_abs=1, tol_rel=0.10) -> bool:
    return abs(e - f) <= max(tol_abs, tol_rel * abs(f))


def grade(df: pd.DataFrame, level: str) -> dict:
    eng, fac, cur = f"new_{level}", f"factory_recommended_new_{level}", CURRENT[level]
    d = df[[eng, fac, cur]].apply(pd.to_numeric, errors="coerce")
    d = d[d[eng].notna() & d[fac].notna()]
    n = len(d)
    if n == 0:
        return {"level": level, "n": 0}
    e, f, c = d[eng].to_numpy(float), d[fac].to_numpy(float), d[cur].to_numpy(float)
    err = e - f
    exact = float(np.mean(e == f))
    within = float(np.mean([_within(a, b) for a, b in zip(e, f)]))
    # direction agreement vs current, only where the engineer actually moved.
    moved = f != c
    if moved.any():
        dir_ok = float(np.mean(np.sign(e[moved] - c[moved]) == np.sign(f[moved] - c[moved])))
    else:
        dir_ok = np.nan
    rho = spearmanr(e, f).statistic if (n >= 3 and np.ptp(e) > 0 and np.ptp(f) > 0) else np.nan
    return {
        "level": level, "n": n,
        "exact_%": round(100 * exact, 1),
        "within_%": round(100 * within, 1),
        "MAE": round(float(np.mean(np.abs(err))), 3),
        "bias": round(float(np.mean(err)), 3),
        "spearman": round(float(rho), 3) if pd.notna(rho) else np.nan,
        "dir_%": round(100 * dir_ok, 1) if pd.notna(dir_ok) else np.nan,
    }


def scorecard(df: pd.DataFrame, label: str) -> list[dict]:
    return [{"subset": label, **grade(df, lvl)} for lvl in LEVELS]


def volume_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Where does the error live? Stratify the priority (constant) consumers by rate."""
    d = df[df["consumable"] == "constant"].copy()
    d["mu_day"] = pd.to_numeric(d["mu_day"], errors="coerce")
    bins = [-0.001, 0.02, 0.1, 0.5, np.inf]
    labels = ["micro <0.02/d", "low .02-.1", "mid .1-.5", "high >.5"]
    d["vol"] = pd.cut(d["mu_day"], bins=bins, labels=labels)
    rows = []
    for vol, g in d.groupby("vol", observed=True):
        for lvl in ("rop", "max"):
            eng, fac = f"new_{lvl}", f"factory_recommended_new_{lvl}"
            gg = g[[eng, fac]].apply(pd.to_numeric, errors="coerce").dropna()
            if len(gg) == 0:
                continue
            e, f = gg[eng].to_numpy(float), gg[fac].to_numpy(float)
            rows.append({"vol": vol, "level": lvl, "n": len(gg),
                         "within_%": round(100 * np.mean([_within(a, b) for a, b in zip(e, f)]), 1),
                         "MAE": round(float(np.mean(np.abs(e - f))), 2),
                         "bias": round(float(np.mean(e - f)), 2)})
    return pd.DataFrame(rows)


def main() -> None:
    df = pd.read_csv(SIZING, encoding="utf-8-sig")
    subsets = {
        "constant (PRIORITY)": df[df["consumable"] == "constant"],
        "sporadic": df[df["consumable"] == "sporadic"],
        "dying": df[df["consumable"] == "dying"],
        "non-consumable": df[df["consumable"] == "none"],
        "all": df,
    }
    rows = [r for label, sub in subsets.items() for r in scorecard(sub, label)]
    card = pd.DataFrame(rows)

    card_path = OUT / "s14_validation_scorecard.csv"
    card.to_csv(card_path, index=False, encoding="utf-8-sig")

    print(f"validation of {len(df)} tested items vs Jan'26 factory_recommended_new_*\n")
    for label, sub in subsets.items():
        print(f"[{label}]  n={len(sub)}")
        print(card[card["subset"] == label]
              .drop(columns="subset").to_string(index=False))
        print()

    # critical parts must not be left at zero (recom = 0 trap check).
    crit = df[df["sfm_criticality"].astype(str).str.strip().str.lower().str[0] == "h"]
    zero_crit = crit[crit["new_max"].fillna(0) == 0]
    print(f"critical (H) items: {len(crit)}  |  left at max=0: {len(zero_crit)} "
          f"(recom = 0 trap)")

    print("\nvolume breakdown (consumable items, ROP & Max):")
    print(volume_breakdown(df).to_string(index=False))
    print(f"\nwrote: {card_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
