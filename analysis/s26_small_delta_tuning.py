"""S26 -- tune the live-route rule against the SMALL-DELTA population.

Asked directly (owner, 2026-09-10): drop the rows where engine and engineer are
far apart, since no single statistical model predicts a bulk withdraw, then push
the matching rate as high as it goes on what is left.

DEFINING THE SUBSET. `delta = |shipped engine Max - engineer Max|`, frozen at the
SHIPPED engine and never recomputed per candidate -- otherwise a rule could win by
changing which rows it is graded on. delta <= 1 keeps 549 of 711 live rows and
covers 67% of all live divergence; delta <= 2 keeps 596.

ONE FINDING FIRST, because it contradicts the premise: ad-hoc / bulk withdraw is
NOT what sits in the big-delta band. It is 8.8% of the delta<=1 band and 10.0% of
the delta>10 band -- flat. What big delta tracks is SIZE: median engineer Max 2
below delta 2, and 26 above delta 10. So excluding big delta excludes the
high-volume parts, not the ad-hoc ones. It is still the right subset to tune on
(the engine scores 0 of 115 rows above delta 2), just not for the stated reason.

WHAT THE TUNING FOUND. A 5,184-point grid over spare floor / keep-alive /
no-initiate / snap band / guard / ROP convention, plus a direct audit of the
contested rows (floor says promote, continuity says hold). On the contested rows
the engineer sides with the CURRENT level in every split -- by frequency, by
volume, by on-hand, by recency -- so the spare floor is a losing bet against
continuity whenever the two disagree. The matching-rate ceiling for this family
is ~55.5% on delta<=1, reached by the simplest member: the engine's own number,
snapped to the current level when it lands within one unit.

That ceiling comes with a warning this script prints rather than hides: the gain
is carried by two cycles (2024-10, 2024-12) where the engineers held almost every
level, and the snap rule loses in 5 months of 8. The ladder wins 7 of 8 months and
caps at 48.6%. Matching rate and month-to-month reliability do not pick the same
rule, and neither does proposal quality -- reported side by side for that reason.

Run: python analysis/s26_small_delta_tuning.py   (offline, needs s20 payloads)
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "analysis"))

from app import engine_statistical as E  # noqa: E402
from s23_live_route_autopsy import ladder  # noqa: E402
from scorecard import matched as _matched, num as _n, pair as _pair  # noqa: E402

PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"
OUT = ROOT / "analysis" / "output" / "s26_small_delta_tuning.csv"

SNAP = 1        # continuity band, in units of Max

# The two live-route anchoring levers are ON in the engine since 2026-09-10.
# This harness measures the quantile engine they sit on top of, so it pins them
# OFF -- otherwise "baseline" silently becomes the anchored engine and every
# comparison in here changes meaning without a line of this file changing.
ANCHOR_OFF = {"continuity_snap": 0, "prior_anchor_policy": ""}


def _selfcheck() -> None:
    b = np.array([[2.0, 2.0], [10.0, 10.0], [0.0, 0.0]])
    assert list(_matched(np.array([[2., 2.], [11., 11.], [0., 0.]]), b)) == [True] * 3
    assert list(_matched(np.array([[1., 2.], [12., 10.], [1., 0.]]), b)) == [False] * 3


def main() -> int:
    _selfcheck()
    df = pd.read_pickle(PKL).reset_index(drop=True)
    base = E.run(df, ANCHOR_OFF)
    route = base.route.values
    live = route != "dormant"
    month = df["_month"].values

    fm = np.nan_to_num(_n(df, "_final_max").values)
    fr = np.nan_to_num(_n(df, "_final_rop").values)
    bench = np.c_[fm, fr]
    e0 = _pair(base)
    ok0 = _matched(e0, bench)

    def col(c, fill=0.0):
        return np.nan_to_num(_n(df, c).values, nan=fill)

    cur, crop, price = col("max_qty"), col("rop_qty"), col("unitprice")

    # The engine refuses to anchor on a level set that is incomplete or not
    # monotonic, and never anchors a critical part BELOW its quantile. Mirror
    # both here or the parity assertion grades against a rule that is not the
    # one that ships. Raw (NaN-preserving) reads: col() zero-fills.
    raw = np.c_[_n(df, "min_qty").values, _n(df, "rop_qty").values,
                _n(df, "max_qty").values]
    cur_ok = (~np.isnan(raw).any(axis=1) & (raw[:, 0] >= 0)
              & (raw[:, 0] <= raw[:, 1]) & (raw[:, 1] <= raw[:, 2]))
    is_crit = (df.get("sfm_criticality", pd.Series("", index=df.index))
               .astype(str).str.strip().str.lower().str[:1].values == "h")

    delta = np.abs(e0[:, 0] - fm)             # FROZEN at the shipped engine
    S1, S2 = live & (delta <= 1), live & (delta <= 2)
    BIG = live & (delta > 2)

    print("=== the subset ===")
    div = live & ~ok0
    for k in (1, 2, 3, 5, 10):
        print(f"  |engine - engineer| <= {k:2d}: {int((live & (delta <= k)).sum()):4d} live rows, "
              f"covering {int((div & (delta <= k)).sum()):4d} of {int(div.sum())} live divergences "
              f"({(div & (delta <= k)).sum() / div.sum() * 100:4.1f}%)")

    j = (df.get("justification", pd.Series("", index=df.index)).astype(str)
         .str.strip().str.lower().values)
    adhoc = j == "ad-hoc consumption/bulk withdraw"
    print("\n=== is big delta really the ad-hoc population? (live divergences) ===")
    for lo, hi, lbl in [(0, 1, "<=1"), (2, 2, "2"), (3, 5, "3-5"), (6, 10, "6-10"), (11, 1e9, ">10")]:
        m = div & (delta >= lo) & (delta <= hi)
        if m.sum():
            print(f"  delta {lbl:5s} n={int(m.sum()):4d}   ad-hoc {adhoc[m].mean() * 100:5.1f}%   "
                  f"median engineer Max {np.median(fm[m]):5.1f}")
    print("  -> flat. big delta is a SIZE band, not an ad-hoc band.")

    def snap_rule(snap=SNAP):
        """The matching-rate optimum: engine, snapped to current inside the band.

        Out of band nothing is rewritten -- an earlier revision replaced ROP with
        round(Max/2) there, which the parameter sweep had already scored 0.2 pp
        WORSE than keeping the engine's own ROP, and which the engine does not do.
        Removed 2026-09-10 when the parity assertion below caught the difference."""
        fire = ((np.abs(e0[:, 0] - cur) <= snap) & cur_ok
                & ~(is_crit & (cur < e0[:, 0])))
        mx = np.where(fire, cur, e0[:, 0])
        rp = np.where(fire, crop, e0[:, 1])
        return np.where(live, mx, e0[:, 0]), np.where(live, rp, e0[:, 1])

    # Each part's own previous engineer decision, strictly earlier month. The
    # engine already carries this as prior_final_* (engine_adapter attaches it as
    # a benchmark); here it is an INPUT, which is the one thing that beat snap-1.
    item = df["item_id"].astype(str).values
    fmin = np.nan_to_num(_n(df, "_final_min").values)
    pmax, prop = np.full(len(df), np.nan), np.full(len(df), np.nan)
    pmin = np.full(len(df), np.nan)
    seen: dict[str, tuple] = {}
    for i in np.argsort(month):
        if item[i] in seen:
            pmax[i], prop[i], pmin[i] = seen[item[i]]
        seen[item[i]] = (fm[i], fr[i], fmin[i])
    has_prior = ~np.isnan(pmax)
    # The engine reads prior_final_* as COLUMNS (engine_adapter attaches them);
    # the pickle has none, so the parity arm below would silently never fire the
    # anchor. Write them on now, before E.run is called with the cfg. Safe: the
    # engine also reads them as a fallback benchmark, and a benchmark never
    # changes sizing (backend/tests test_prior_benchmark_never_changes_sizing).
    df["prior_final_max"], df["prior_final_rop"], df["prior_final_min"] = pmax, prop, pmin
    pmax, prop = np.nan_to_num(pmax), np.nan_to_num(prop)

    # "Order To Demand" parts are not managed to a Max in SAP, so max_qty is not
    # a meaningful current level for them -- the engineer's own last decision is.
    # 225 of 711 live rows, 165 of them with a prior.
    on_demand = (df.get("replenishment_policy", pd.Series("", index=df.index))
                 .astype(str).str.lower().str.contains("demand").values)

    def snap_prior():
        """snap-1, except Order-To-Demand rows anchor on the prior decision.

        Mirrors the engine exactly, including its refusals: the branch is chosen
        first (prior for an order-to-demand part that has one, else current), and
        if THAT anchor is unusable the row keeps the quantile -- it does not fall
        through to the other anchor.
        """
        close = np.abs(e0[:, 0] - cur) <= SNAP
        pick_prior = close & on_demand & has_prior
        prior_ok = (has_prior & ~np.isnan(pmin) & (pmin >= 0)
                    & (pmin <= prop) & (prop <= pmax))
        anchor_max = np.where(pick_prior, pmax, cur)
        anchor_rop = np.where(pick_prior, prop, crop)
        usable = np.where(pick_prior, prior_ok, cur_ok)
        fire = close & usable & ~(is_crit & (anchor_max < e0[:, 0]))
        mx = np.where(fire, anchor_max, e0[:, 0])
        rp = np.where(fire, anchor_rop, e0[:, 1])
        return np.where(live, mx, e0[:, 0]), np.where(live, rp, e0[:, 1])

    cands = {
        "shipped engine": (e0[:, 0], e0[:, 1]),
        "hold current": (np.where(live, cur, e0[:, 0]), np.where(live, crop, e0[:, 1])),
        "snap-1 continuity": snap_rule(SNAP),
        "snap-1 + prior anchor": snap_prior(),
        "ladder (s23)": ladder(df, base),
    }

    # The shipped engine must reproduce the harness rule exactly, or the figure
    # quoted in this file is not the figure the product would ship. Same guarantee
    # analysis/scorecard.py gives for the tolerance, applied to the sizing.
    # No cfg: the SHIPPED DEFAULTS must reproduce this rule, which is the
    # whole point now that the levers are on.
    eng = _pair(E.run(df))
    want_mx, want_rp = cands["snap-1 + prior anchor"]
    assert np.array_equal(eng[live], np.c_[want_mx, want_rp][live]), \
        "engine_statistical drifted from the s26 rule"
    cands["engine cfg (parity)"] = (eng[:, 0], eng[:, 1])

    print("\n=== MATCHING RATE ===")
    table = {}
    for nm, (mx, rp) in cands.items():
        ok = _matched(np.c_[mx, rp], bench)
        ch = live & (np.abs(mx - cur) > 0)
        table[nm] = {
            "a) ALL rows (9054)": round(ok.mean() * 100, 1),
            "a) all LIVE rows (711)": round(ok[live].mean() * 100, 1),
            "b) small delta<=1 (549)": round(ok[S1].mean() * 100, 1),
            "b) small delta<=2 (596)": round(ok[S2].mean() * 100, 1),
            "   active (257)": round(ok[route == "active"].mean() * 100, 1),
            "   dying (454)": round(ok[route == "dying"].mean() * 100, 1),
            "   big delta>2 (115)": round(ok[BIG].mean() * 100, 1),
            "proposals": int(ch.sum()),
            "right on proposals%": round(ok[ch].mean() * 100, 1) if ch.sum() else float("nan"),
            "book $M": round(float(np.nansum(price * mx)) / 1e6, 2),
        }
    t = pd.DataFrame(table)
    print(t.to_string())
    t.to_csv(OUT)

    print("\n=== per-month on the delta<=1 subset -- read before quoting the headline ===")
    rows = []
    for m in sorted(set(month)):
        s = S1 & (month == m)
        if not s.sum():
            continue
        d = {"month": m, "n": int(s.sum())}
        for nm, (mx, rp) in cands.items():
            d[nm] = round(_matched(np.c_[mx, rp], bench)[s].mean() * 100, 1)
        rows.append(d)
    pm = pd.DataFrame(rows)
    print(pm.to_string(index=False))
    for nm in cands:
        if nm == "shipped engine":
            continue
        print(f"  {nm:22s} beats the shipped engine in "
              f"{int((pm[nm] > pm['shipped engine']).sum())}/8 months")
    print("\n  The snap rule's headline is carried by 2024-10 and 2024-12, two cycles where")
    print("  the engineers held nearly every level. The ladder is the steadier rule and the")
    print("  lower one. Pick on which risk you would rather carry.")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
