"""S15 -- Auto-clear calibration: measure precision vs coverage on Jan'26.

Auto-clear decides whether a HUMAN sees a row; it never changes Min/ROP/Max.
So the only risk is an un-reviewed row the engineer would actually have changed.
This harness drives the PRODUCTION engine (backend/app/engine_statistical.py)
over a grid of auto-clear configs and grades each against the engineers'
`factory_recommended_new_*` on Jan'26:

    coverage   = share of items auto-cleared (review_required == "N")
    precision  = of auto-cleared LABELLED items, share where the engine value
                 matches the engineer's (|Δ| <= 1 or <= 10% on all three levels)
                 -> i.e. review would have added nothing
    escaped_$  = exposure of auto-cleared items where engine != engineer
                 (the money at risk from skipping review)

Pick the config with the highest coverage whose precision clears the target
(default 98%). Output: analysis/output/s15_autoclear_calibration.csv (+ console).

Run: python s15_autoclear_calibration.py   (add --all to size every item)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import OUT

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "app"))
import engine_statistical  # noqa: E402

from s13_cumulative_sizing import LATEST, load_snapshot  # noqa: E402

TARGET_PRECISION = 98.0
LEVELS = ("max", "rop", "min")


def _agree(eng: np.ndarray, fac: np.ndarray, tol_abs=1.0, tol_rel=0.10) -> np.ndarray:
    return np.abs(eng - fac) <= np.maximum(tol_abs, tol_rel * np.abs(fac))


def evaluate(items: pd.DataFrame, cfg: dict) -> dict:
    res = engine_statistical.run(items, cfg).rename(columns={
        "factory_recommended_new_max": "eng_max",
        "factory_recommended_new_rop": "eng_rop",
        "factory_recommended_new_min": "eng_min",
    }).reset_index(drop=True)
    base = items.reset_index(drop=True)

    cleared = (res["review_required"] == "N").to_numpy()
    exposure = res["_exposure_usd"].to_numpy(float)

    fac = {lvl: pd.to_numeric(base.get(f"factory_recommended_new_{lvl}"),
                              errors="coerce").to_numpy(float) for lvl in LEVELS}
    eng = {lvl: res[f"eng_{lvl}"].to_numpy(float) for lvl in LEVELS}
    labelled = np.all([~np.isnan(fac[lvl]) for lvl in LEVELS], axis=0)
    agree = np.all([_agree(eng[lvl], fac[lvl]) for lvl in LEVELS], axis=0)

    cl_lab = cleared & labelled
    n_cl_lab = int(cl_lab.sum())
    precision = 100.0 * float((cl_lab & agree).sum()) / n_cl_lab if n_cl_lab else np.nan
    escaped = float(exposure[cl_lab & ~agree].sum())
    return {
        "cleared_%": round(100.0 * cleared.mean(), 1),
        "cleared_n": int(cleared.sum()),
        "precision_%": round(precision, 1) if n_cl_lab else np.nan,
        "n_labelled_cleared": n_cl_lab,
        "escaped_usd": round(escaped, 0),
    }


def main() -> None:
    items = load_snapshot(*LATEST).reset_index(drop=True)
    print(f"loaded {len(items):,} Jan'26 items for calibration\n")

    # baseline ~= pre-Phase-5 (no no-op/immaterial/reliable auto-clear expansion)
    grid = [("baseline", {"autoclear_noop_abs": 0, "autoclear_noop_rel": 0,
                          "autoclear_immaterial_usd": 0, "autoclear_reliable": False})]
    for immaterial in (100, 200, 500, 1000):
        grid.append((f"noop+immat<{immaterial}",
                     {"autoclear_immaterial_usd": immaterial, "autoclear_reliable": False}))
    for immaterial in (200, 500):
        grid.append((f"noop+immat<{immaterial}+reliable",
                     {"autoclear_immaterial_usd": immaterial, "autoclear_reliable": True}))

    rows = []
    for name, cfg in grid:
        rows.append({"config": name, **evaluate(items, cfg)})
    card = pd.DataFrame(rows)

    base_cleared = card.loc[card["config"] == "baseline", "cleared_n"].iloc[0]
    card["review_saved_vs_base"] = card["cleared_n"] - base_cleared

    out_path = OUT / "s15_autoclear_calibration.csv"
    card.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(card.to_string(index=False))

    ok = card[(card["precision_%"] >= TARGET_PRECISION) & (card["config"] != "baseline")]
    print(f"\ntarget precision >= {TARGET_PRECISION}%")
    if len(ok):
        best = ok.sort_values("cleared_n", ascending=False).iloc[0]
        print(f"recommended: {best['config']}  -> "
              f"{best['cleared_%']}% auto-cleared "
              f"({int(best['review_saved_vs_base']):+,} vs baseline), "
              f"precision {best['precision_%']}%, ${int(best['escaped_usd']):,} at risk")
    else:
        print("no config clears the target; loosen the target or tighten thresholds.")
    print(f"\nwrote: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
