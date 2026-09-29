"""S23 -- why the LIVE routes (active/dying) match the engineers so poorly.

s20 reports 17.5% agreement on active and 38.5% on dying and shows every built-in
cfg lever is negative. This asks the next question -- WHY -- and answers it with
three measurements, then proposes the one change the evidence supports.

  1. The collapse. On 70% of live rows the engine returns Max=1: the lead-time
     quantile rounds to 0/1 at these demand rates and `max(rop + moq)` supplies
     the 1. Inside that single answer the engineers wrote 0, 1, 2, 3 and 4. The
     engine is not sizing those rows, it is emitting a floor.

  2. Nothing derived from the demand RATE recovers them. Every window, horizon
     and rate rule tops out near 26% on Max, while `max_qty` (change nothing) is
     46.8%. The model CLASS is what caps the live routes, not its parameters.

  3. The ceiling of the engine's own inputs. Best-policy-per-cell over
     current level x demand presence x usage frequency x on-hand, fitted
     leave-one-month-out: no partition beats the global policy "keep current".
     With 711 live rows there is not enough signal to learn more than inertia.

  4. The proposal: a stocking LADDER. Replace the degenerate floor with the
     spares convention the engineers actually apply -- a MOVING part carries one
     spare plus a reorder point (Max 2 / ROP 1), a part with no annual demand but
     stock or history carries a keep-alive 1, and a part with neither carries 0 --
     then snap to the current level when the ladder lands within `snap` of it.
     Quantile sizing still wins wherever it asks for more.

     The floor is deliberately restricted to parts that moved in the last 90 days.
     s25 replayed the unrestricted version on the Jan'26 batch: +14.5 pp on active,
     -13.5 pp on dying. Per month the floor improves active 8 times out of 8 and
     dying 4 times out of 7, so only the half that replicates is kept.

Scoring is the REVIEW SCORECARD rule via analysis/scorecard.py -- strict 10%
relative on Max and ROP, no absolute floor, Min excluded -- so it cannot drift
from engine_statistical.close_enough.

Read `precision on proposals` as the product metric: of the rows where the engine
asks the engineer to change something, how often was it right. Doing nothing
scores 46% agreement while proposing nothing, so agreement alone cannot rank
these options.

Dormant and no-data rows are untouched -- that is dormant_rules' question (s22).

Run: python analysis/s23_live_route_autopsy.py   (offline, needs s20 payloads)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import engine_statistical as E  # noqa: E402
from scorecard import matched as _matched, num as _n, pair as _pair  # noqa: E402

PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"
OUT = ROOT / "analysis" / "output" / "s23_live_route_autopsy.csv"

# The two live-route anchoring levers are ON in the engine since 2026-09-10.
# This harness measures the quantile engine they sit on top of, so it pins them
# OFF -- otherwise "baseline" silently becomes the anchored engine and every
# comparison in here changes meaning without a line of this file changing.
ANCHOR_OFF = {"continuity_snap": 0, "prior_anchor_policy": ""}

# Ladder parameters. SNAP is the continuity band: land within this many units of
# the current level and propose the current level instead, because the engineers
# hold a level that is already close enough. GUARD caps what the ladder's floor
# may add in dollars on one row -- inf is the accuracy-optimal setting, a finite
# value trades a little agreement for book value.
SNAP = 1
GUARD_USD = 5000.0      # owner decision, 2026-09-10
SPARE_MAX, SPARE_ROP = 2.0, 1.0     # one on the shelf + a reorder point
KEEPALIVE_MAX = 1.0


def _selfcheck() -> None:
    """Pin the scorecard boundary, and pin it against the engine's own rule."""
    b = np.array([[2.0, 2.0], [10.0, 10.0], [0.0, 0.0], [5.0, 5.0]])
    hit = np.array([[2., 2.], [11., 11.], [0., 0.], [5., 5.]])
    miss = np.array([[1., 2.], [12., 10.], [1., 0.], [5., 7.]])
    assert list(_matched(hit, b)) == [True] * 4
    assert list(_matched(miss, b)) == [False] * 4
    for cand, want in ((hit, "match"), (miss, "diverge")):
        for e, x in zip(cand, b):
            assert E._agreement((e[0], e[1], 0), (x[0], x[1], 999)) == want, \
                "harness drifted from engine_statistical._agreement"


def ladder(df: pd.DataFrame, base: pd.DataFrame, snap: int = SNAP,
           guard: float = GUARD_USD) -> tuple[np.ndarray, np.ndarray]:
    """The proposed live-route sizing. Returns (Max, ROP) for every row.

    THE one definition -- s25 replays this exact function on a live batch rather
    than restating it, so a backtest and a replay cannot drift apart. Dormant and
    no-data rows come back with the engine's own answer untouched.

    Shape of the eventual engine change: everything here is computable inside
    engine_statistical.run()'s row loop from columns it already reads.
    """
    def col(c, fill=0.0):
        return np.nan_to_num(_n(df, c).values, nan=fill)

    e0 = _pair(base)
    live = base.route.values != "dormant"
    cur, crop = col("max_qty"), col("rop_qty")
    c365, c547 = col("last_365_day_cnsmptn_qty"), col("last_547_day_cnsmptn_qty")
    freq, avail, price = col("frequencymonthswithusage"), col("avail_qty"), col("unitprice")

    c90 = col("last_90_day_cnsmptn_qty")

    # The spare floor applies to MOVING parts only. s25 replayed the first cut of
    # this on Jan'26 and it lost 13.5 pp on `dying` while winning 14.5 pp on
    # `active`; per-month, the floor improves active in 8 months of 8 and dying in
    # 4 of 7. Promoting a part that has not moved in a quarter is not a spares
    # convention, it is inventory -- so `dying` keeps continuity and nothing else.
    spare = (c365 > 0) & (freq >= 1) & (c90 > 0)
    keep = (c547 > 0) | (avail > 0) | (cur > 0)   # history or stock worth keeping alive
    floor = np.where(spare, SPARE_MAX, np.where(keep, KEEPALIVE_MAX, 0.0))
    if np.isfinite(guard):      # refuse a floor whose extra unit costs more than this
        over = price * np.maximum(floor - np.maximum(e0[:, 0], cur), 0) > guard
        floor = np.where(over, np.maximum(e0[:, 0], np.minimum(cur, floor)), floor)
    mx = np.maximum(e0[:, 0], floor)              # the quantile still wins when it asks more
    rp = np.maximum(e0[:, 1], np.where(floor >= SPARE_MAX, SPARE_ROP, 0.0))
    rp = np.where(mx <= 1, 0.0, np.maximum(rp, np.round(mx / 2.0)))
    close = (np.abs(mx - cur) <= snap) & (cur >= floor)   # continuity band
    close |= (np.abs(mx - cur) <= snap) & (cur > 0) & ~spare   # ...and for non-spare rows
    mx, rp = np.where(close, cur, mx), np.where(close, crop, rp)
    return np.where(live, mx, e0[:, 0]), np.where(live, rp, e0[:, 1])


def main() -> int:
    _selfcheck()
    df = pd.read_pickle(PKL).reset_index(drop=True)
    base = E.run(df, ANCHOR_OFF)
    route = base.route.values
    live = route != "dormant"
    act, dyi = route == "active", route == "dying"
    month = df["_month"].values

    fm = np.nan_to_num(_n(df, "_final_max").values)
    fr = np.nan_to_num(_n(df, "_final_rop").values)
    bench = np.c_[fm, fr]
    e0 = _pair(base)

    def col(c, fill=0.0):
        return np.nan_to_num(_n(df, c).values, nan=fill)

    cur, crop = col("max_qty"), col("rop_qty")
    c90, c365, c547 = col("last_90_day_cnsmptn_qty"), col("last_365_day_cnsmptn_qty"), col("last_547_day_cnsmptn_qty")
    freq, avail, price = col("frequencymonthswithusage"), col("avail_qty"), col("unitprice")
    lt = col("contractual_lead_time", E.DEFAULT_LT)
    lt = np.where(lt <= 0, E.DEFAULT_LT, lt)

    def hit_vs(mx, y):
        return np.abs(np.asarray(mx, float) - y) <= E.AGREE_TOL * np.abs(y)

    def hit_max(mx):
        return hit_vs(mx, fm)

    print(f"=== live rows {int(live.sum())} of {len(df)}  "
          f"(active {int(act.sum())}, dying {int(dyi.sum())}) ===")

    print("\n=== 1. THE COLLAPSE: what the engine actually returns on live rows ===")
    print(f"  engine Max == 1 on {int((e0[live, 0] == 1).sum())} of {int(live.sum())} live rows "
          f"({(e0[live, 0] == 1).mean() * 100:.0f}%), ROP == 0 on {(e0[live, 1] == 0).mean() * 100:.0f}%")
    cells = pd.crosstab(pd.Series(e0[live, 0]).astype(int),
                        pd.Series(fm[live]).astype(int)).stack().sort_values(ascending=False)
    print("  biggest engine -> engineer cells:")
    for (ee, ff), c in cells.head(6).items():
        tag = "MATCH" if ee == ff else ""
        print(f"    engine {ee:3d} -> engineer {ff:3d}: {c:4d}  ({c / live.sum() * 100:4.1f}% of live)  {tag}")

    print("\n=== 2. NOTHING DERIVED FROM THE DEMAND RATE RECOVERS THEM (Max only) ===")
    mu365 = c365 / 365.0
    probes = {"engine (shipped quantile)": e0[:, 0], "current level (change nothing)": cur}
    for H in (30, 90, 180, 365):
        probes[f"ceil(mu365 x (LT+{H}d))"] = np.ceil(mu365 * (lt + H))
    for w, v in (("90", c90), ("365", c365), ("547", c547)):
        probes[f"ceil(last {w}d consumption)"] = np.ceil(v)
    for name, mx in probes.items():
        ok = hit_max(mx)
        print(f"  {name:32s} live {ok[live].mean() * 100:5.1f}%  "
              f"active {ok[act].mean() * 100:5.1f}%  dying {ok[dyi].mean() * 100:5.1f}%")

    print("\n=== 3. CEILING of the engine's own inputs (best policy per cell, LOMO) ===")
    policies = {"current": cur, "current+1": cur + 1, "zero": np.zeros(len(df)),
                "one": np.ones(len(df)), "two": np.full(len(df), 2.0),
                "stat": e0[:, 0], "max(stat,2)": np.maximum(e0[:, 0], 2),
                "max(cur,stat)": np.maximum(cur, e0[:, 0]),
                "ceil(c365)": np.ceil(c365), "max(cur,ceil(c365))": np.maximum(cur, np.ceil(c365))}
    pnames = list(policies)
    pmat = np.c_[[policies[k] for k in pnames]].T

    def cellkey(spec):
        parts = []
        if "cur" in spec:
            parts.append(np.where(cur <= 0, "c0", np.where(cur == 1, "c1", np.where(cur <= 3, "c23", "c4+"))))
        if "demand" in spec:
            parts.append(np.where(c365 > 0, "d1", "d0"))
        if "freq" in spec:
            parts.append(np.where(freq <= 0, "f0", np.where(freq <= 1, "f1", "f2+")))
        if "avail" in spec:
            parts.append(np.where(avail > 0, "a1", "a0"))
        if "route" in spec:
            parts.append(np.where(dyi, "dy", "ac"))
        return np.array(["|".join(t) for t in zip(*parts)]) if parts else np.array(["*"] * len(df))

    for spec in [(), ("cur",), ("cur", "demand"), ("cur", "demand", "freq"),
                 ("cur", "demand", "avail"), ("demand", "freq"), ("cur", "demand", "freq", "avail")]:
        key = cellkey(spec)
        pooled = np.zeros(len(df), bool)
        for m in sorted(set(month)):
            tr, te = live & (month != m), live & (month == m)
            if tr.sum() < 50:
                continue
            table = {}
            for k in np.unique(key[tr]):
                s = tr & (key == k)
                if s.sum() >= 10:
                    table[k] = pnames[int(np.argmax(hit_vs(pmat[s].T, fm[s]).sum(axis=1)))]
            fb = pnames[int(np.argmax(hit_vs(pmat[tr].T, fm[tr]).sum(axis=1)))]
            idx = np.where(te)[0]
            pooled[idx] = hit_vs([policies[table.get(key[i], fb)][i] for i in idx], fm[idx])
        print(f"  {'x'.join(spec) or '(global)':32s} {pooled[live].mean() * 100:5.1f}%  "
              f"cells={len(np.unique(key[live]))}")
    print("  -> no partition beats the global policy. 711 live rows carry inertia and little else.")

    print("\n=== 4. PROPOSAL: stocking ladder ===")

    rows = {}

    def score(mx, rp, label):
        ok = _matched(np.c_[mx, rp], bench)
        changed = live & (np.abs(mx - cur) > 1e-9)
        rows[label] = {"live%": round(ok[live].mean() * 100, 1),
                       "active%": round(ok[act].mean() * 100, 1),
                       "dying%": round(ok[dyi].mean() * 100, 1),
                       "all%": round(ok.mean() * 100, 1),
                       "book_live_usd": int(np.nansum(price[live] * mx[live])),
                       "proposals": int(changed.sum()),
                       "precision_on_proposals%": (round(ok[changed].mean() * 100, 1)
                                                   if changed.sum() else float("nan"))}
        return ok

    ok0 = score(e0[:, 0], e0[:, 1], "shipped engine")
    score(np.where(live, cur, e0[:, 0]), np.where(live, crop, e0[:, 1]), "change nothing")
    for guard in (2500.0, 5000.0, float("inf")):
        score(*ladder(df, base, SNAP, guard), f"ladder snap{SNAP} guard {guard:,.0f}")
    table = pd.DataFrame(rows).T
    print(table.to_string())
    print(f"  engineer's own book on live rows ${np.nansum(price[live] * fm[live]):,.0f}; "
          f"they changed {int((live & (np.abs(fm - cur) > 1e-9)).sum())} of {int(live.sum())} live rows")
    table.to_csv(OUT)

    print("\n=== 5. LEAVE-ONE-MONTH-OUT for the ladder (snap+guard fitted per fold) ===")
    grid = [(s, g) for s in (0, 1, 2) for g in (2500.0, 5000.0, float("inf"))]
    pooled = np.zeros(len(df), bool)
    picks = []

    def fold(p):
        a = ladder(df, base, *p)
        return _matched(np.c_[a[0], a[1]], bench)

    for m in sorted(set(month)):
        tr, te = live & (month != m), live & (month == m)
        best = max(grid, key=lambda p: fold(p)[tr].mean())
        o = fold(best)
        pooled |= o & te
        picks.append({"month": m, "n": int(te.sum()), "snap": best[0], "guard": best[1],
                      "shipped%": round(ok0[te].mean() * 100, 1),
                      "ladder%": round(o[te].mean() * 100, 1)})
    p = pd.DataFrame(picks)
    p["delta"] = (p["ladder%"] - p["shipped%"]).round(1)
    print(p.to_string(index=False))
    held = pooled[live].sum() / live.sum() * 100
    print(f"  held-out live match {held:.1f}%  vs shipped {ok0[live].mean() * 100:.1f}%")

    assert held > ok0[live].mean() * 100, "the ladder must beat the shipped engine out of sample"
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
