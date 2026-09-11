"""S20 -- root-cause decomposition of engineer-vs-engine divergence, with ablations.

Re-runs the shipped engine from the stored payloads, then re-runs it under
single-lever changes. That turns "N rows diverge" into "this lever owns this
share of the gap, and this share no lever can reach".

Scoring is the REVIEW SCORECARD rule, matching s19_export_decision_vs_engine.py:
a strict 10% relative tolerance on Max and ROP, no absolute floor, Min excluded.
An engineer's 2 admits only 2 (1.8-2.2); an engineer's 0 admits only 0. This is
deliberately NOT engine_statistical._agreement, which still uses max(1, 10%)
across all three fields for reason codes and confidence -- s19 emits both so the
difference stays visible instead of one silently replacing the other.

Baseline here is the SHIPPED service-level table. SL_LEGACY is what the engine
carried before 2026-08-28, kept as the comparison arm so the calibration change
stays auditable.

Run: python analysis/s20_diverge_features.py   (pull, needs DB)
     python analysis/s20_diverge_rootcause.py  (offline)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import engine_statistical as E  # noqa: E402
# The scorecard rule lives in one module, not one copy per script. The whole
# point of it is that it cannot drift from the engine's own tolerance, which a
# third hand-written copy would quietly undo.
from scorecard import matched as _matched, num as _n, pair as _pair  # noqa: E402

PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"
OUT = ROOT / "analysis" / "output" / "s20_ablation.csv"

# The two live-route anchoring levers are ON in the engine since 2026-09-10.
# This harness measures the quantile engine they sit on top of, so it pins them
# OFF -- otherwise "baseline" silently becomes the anchored engine and every
# comparison in here changes meaning without a line of this file changing.
ANCHOR_OFF = {"continuity_snap": 0, "prior_anchor_policy": ""}

SL_LEGACY = {"h": 0.99, "m": 0.95, "l": 0.90, "d": 0.90}
SL_LEGACY_DEFAULT = 0.95


def _sl_run(df: pd.DataFrame, table: dict, default: float, cfg: dict | None = None):
    """Run the engine under a different service-level table, then restore."""
    old_t, old_d = dict(E.SL_BY_CRIT), E.SL_DEFAULT
    E.SL_BY_CRIT, E.SL_DEFAULT = table, default
    try:
        return E.run(df, {**ANCHOR_OFF, **(cfg or {})})
    finally:
        E.SL_BY_CRIT, E.SL_DEFAULT = old_t, old_d


def _selfcheck() -> None:
    """Pin the tolerance, and pin that it still equals the engine's own rule."""
    b = np.array([[2.0, 2.0], [10.0, 10.0], [0.0, 0.0], [5.0, 5.0]])
    # exact / 10% boundary is inclusive / both zero / exact
    hit = np.array([[2., 2.], [11., 11.], [0., 0.], [5., 5.]])
    # 2 admits only 2 / 12 is 2 off a base of 10 / 0 admits only 0 / ROP alone breaks it
    miss = np.array([[1., 2.], [12., 10.], [1., 0.], [5., 7.]])
    assert list(_matched(hit, b)) == [True] * 4
    assert list(_matched(miss, b)) == [False] * 4
    for cand, want in ((hit, "match"), (miss, "diverge")):
        for e, x in zip(cand, b):
            assert E._agreement((e[0], e[1], 0), (x[0], x[1], 999)) == want, \
                "harness drifted from engine_statistical._agreement"


def main() -> int:
    _selfcheck()
    df = pd.read_pickle(PKL).reset_index(drop=True)
    bench = np.c_[_n(df, "_final_max"), _n(df, "_final_rop")]
    price = _n(df, "unitprice").fillna(0).values
    base = E.run(df, ANCHOR_OFF)
    route = base.route.values
    e0 = _pair(base)
    ok0 = _matched(e0, bench)
    live = route != "dormant"
    # The 0/0-vs-0/0 dormant rows are a free win on any metric; a headline match
    # rate that includes them measures the part mix, not the engine.
    trivial = (route == "dormant") & (bench[:, 0] == 0) & (e0[:, 0] == 0) & (bench[:, 1] == 0)

    def score(e: np.ndarray) -> dict:
        ok = _matched(e, bench)
        return {"all%": round(ok.mean() * 100, 1),
                "live%": round(ok[live].mean() * 100, 1),
                "active%": round(ok[route == "active"].mean() * 100, 1),
                "dying%": round(ok[route == "dying"].mean() * 100, 1),
                "dormant%": round(ok[route == "dormant"].mean() * 100, 1),
                "diverge": int((~ok).sum()),
                "stock_usd": int((price * e[:, 0]).sum())}

    print("=== 0. DENOMINATOR: what the headline is made of ===")
    print(f"  total rows                              {len(df):6d}")
    print(f"  dormant, both sides say 0/0             {int(trivial.sum()):6d}"
          f"  ({trivial.mean() * 100:.1f}% -- free match on any engine)")
    print(f"  live rows (active + dying)              {int(live.sum()):6d}"
          f"  ({live.mean() * 100:.1f}% of rows, "
          f"{int((~ok0 & live).sum())}/{int((~ok0).sum())} of diverges)")
    print(f"  headline match                          {ok0.mean() * 100:6.1f}%")
    print(f"  match on live rows only                 {ok0[live].mean() * 100:6.1f}%")

    print("\n=== 1. DORMANT DISAGREEMENT (no longer absorbed by a +-1 floor) ===")
    d = route == "dormant"
    for lbl, m in [("engineer 0, engine 0  (true agreement)", d & (bench[:, 0] == 0)),
                   ("engineer 1, engine 0", d & (bench[:, 0] == 1)),
                   ("engineer >=2, engine 0", d & (bench[:, 0] >= 2))]:
        print(f"  {lbl:42s} {int(m.sum()):6d}")
    print(f"  -> the engine zeroes {int((d & (bench[:, 0] > 0)).sum())} parts the engineer "
          f"chose to keep; under this rule every one of them is a divergence.")

    print("\n=== 2. WHAT THE ENGINEER'S OWN JUSTIFICATION SAYS (all rows) ===")
    j = df.get("justification", pd.Series("", index=df.index)).astype(str).str.strip().str.lower()
    j = j.replace({"nan": "(blank)", "none": "(blank)", "": "(blank)"})
    t = pd.crosstab(j, np.where(ok0, "match", "diverge"))
    t["total"] = t.sum(axis=1)
    t["diverge%"] = (t["diverge"] / t["total"] * 100).round(1)
    t["share of diverges%"] = (t["diverge"] / int((~ok0).sum()) * 100).round(1)
    print(t.sort_values("diverge", ascending=False).to_string())

    print("\n=== 3. RIVAL PREDICTORS OF THE ENGINEER (live rows, same rule) ===")
    rivals = {
        "engine (shipped SL)": e0,
        "current levels (do nothing)": np.c_[_n(df, "max_qty"), _n(df, "rop_qty")],
        "SFM": np.c_[_n(df, "sfm_max"), _n(df, "sfm_rop")],
        "SFM BRR": np.c_[_n(df, "sfm_brr_max"), _n(df, "sfm_brr_rop")],
        # NOTE: atm_recommended_* equals the engineer's final on 86/98% of rows
        # for Max/ROP. It is the review's OUTPUT column, not an input -- scoring
        # against it would be target leakage. Reported to show the leak only.
        "ATM rec (LEAKY -- see note)": np.c_[_n(df, "atm_recommended_max"),
                                             _n(df, "atm_recommended_rop")],
    }
    for name, cand in rivals.items():
        cov = ~np.isnan(cand).any(axis=1)
        sel = live & cov
        print(f"  {name:32s} coverage {int(cov.sum()):5d}  "
              f"match {_matched(cand, bench)[sel].mean() * 100:5.1f}%  (n={int(sel.sum())})")

    print("\n=== 4. SERVICE-LEVEL GRID ===")
    grid = {}
    for sl in (0.99, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70):
        grid[f"flat SL {sl:.2f}"] = score(_pair(_sl_run(df, {k: sl for k in "hmld"}, sl)))
    grid["legacy (.99/.95/.90)"] = score(_pair(_sl_run(df, SL_LEGACY, SL_LEGACY_DEFAULT)))
    grid["SHIPPED (.90/.85/.80)"] = score(e0)
    print(pd.DataFrame(grid).T.to_string())

    print("\n=== 5. ABLATION: every built-in lever, one at a time ===")
    rows = {"baseline (shipped)": score(e0)}
    for name, cfg in [
        ("A1 blended demand estimator", {"demand_estimator": "blended"}),
        ("A2 trend adjust", {"trend_adjust": True}),
        ("A4 lead-time sigma", {"lead_time_sigma": True}),
        ("B  cost-aware service level", {"service_level_mode": "cost_aware"}),
        ("P1 Max DOI cap 180d", {"policy_max_doi_days": 180}),
        ("P2 excess netting", {"policy_excess_netting": True}),
    ]:
        rows[name] = score(_pair(E.run(df, {**ANCHOR_OFF, **cfg})))
    rows["SL legacy (.99/.95/.90)"] = score(_pair(_sl_run(df, SL_LEGACY, SL_LEGACY_DEFAULT)))
    t = pd.DataFrame(rows).T
    t["d live"] = (t["live%"] - t.loc["baseline (shipped)", "live%"]).round(1)
    t["d stock %"] = ((t["stock_usd"] / t.loc["baseline (shipped)", "stock_usd"] - 1) * 100).round(1)
    print(t.to_string())
    t.to_csv(OUT)

    print("\n=== 6. MONTH-BY-MONTH: shipped vs legacy service level (live rows) ===")
    ok_legacy = _matched(_pair(_sl_run(df, SL_LEGACY, SL_LEGACY_DEFAULT)), bench)
    g = pd.DataFrame({"m": df._month, "legacy": ok_legacy, "shipped": ok0})[live].groupby("m").agg(
        n=("legacy", "size"), lg=("legacy", "mean"), sh=("shipped", "mean"))
    g["legacy%"], g["shipped%"] = (g.lg * 100).round(1), (g.sh * 100).round(1)
    g["delta"] = (g["shipped%"] - g["legacy%"]).round(1)
    print(g[["n", "legacy%", "shipped%", "delta"]].to_string())
    # Under the old max(1, 10%)-on-three-fields rule this change won every month.
    # Under the strict Max+ROP rule it does not: near-misses no longer count in
    # either direction, so a month can regress. Surface those months instead of
    # hiding them, and hold the guard at the claim the data supports -- the
    # aggregate live-row rate must not go backwards.
    worse = g.index[g["delta"] < 0].tolist()
    if worse:
        print(f"  NOTE: shipped SL is worse than legacy in {len(worse)} of {len(g)} months: "
              f"{', '.join(worse)}. Aggregate still improves; per-month it does not.")
    lg_all, sh_all = ok_legacy[live].mean(), ok0[live].mean()
    print(f"  aggregate live-row match: legacy {lg_all * 100:.1f}% -> shipped {sh_all * 100:.1f}%")
    assert sh_all >= lg_all, "shipped SL must not lower the aggregate live-row match"

    print("\n=== 7. RESIDUAL under the shipped configuration ===")
    res = ~ok0
    print(pd.Series(route[res]).value_counts().to_string())
    fm = _n(df, "_final_max")
    for lbl, m in [
        ("dormant: all 6 windows 0, engineer keeps stock", res & (route == "dormant")),
        ("engineer copied SFM exactly", res & (fm == _n(df, "sfm_max")).values),
        ("engineer kept current levels", res & (fm == _n(df, "max_qty")).values),
        ("engineer stated a reason the engine cannot read", res & (j != "(blank)").values),
    ]:
        print(f"  {lbl:50s} {int(m.sum()):4d}  {m.sum() / res.sum() * 100:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
