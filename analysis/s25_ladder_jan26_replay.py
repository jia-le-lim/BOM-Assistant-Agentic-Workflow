"""S25 -- replay the stocking ladder over the Jan'26 TCB batch, end to end.

s23 backtests the ladder across eight months of stored payloads. This runs it
over one real batch, the way a scoring run would, and answers the question a
backtest cannot: what would the engineers actually have SEEN.

  * which rows change, by route, with the part numbers and descriptions
  * what the change is worth in book value
  * whether the change was right, against that batch's own engineer decisions
  * how many extra rows land in the review queue

Reads `BOM table/BOM REVIEW_Jan'26 .csv` through analysis/common.load_typed --
the same file s1..s9 profile, no database. The engineer's answer for this batch
is its `factory_recommended_new_*` columns, read from the INPUT frame (the engine
emits columns of the same name, which is why they are read before the run).

The ladder itself is imported from s23, never restated, so the replay and the
backtest cannot drift apart.

GUARD_USD is $5,000 -- the setting chosen by the owner on 2026-09-10, trading
about a point of agreement for a materially smaller book than the unguarded arm.

Run: python analysis/s25_ladder_jan26_replay.py   (offline, needs the CSV)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "analysis"))

import common  # noqa: E402
from app import engine_statistical as E  # noqa: E402
from s23_live_route_autopsy import ladder  # noqa: E402
from scorecard import matched as _matched, num as _n, pair as _pair  # noqa: E402

OUT = ROOT / "analysis" / "output" / "s25_ladder_jan26_changed_rows.csv"
SNAP, GUARD_USD = 1, 5000.0

# The two live-route anchoring levers are ON in the engine since 2026-09-10.
# This harness measures the quantile engine they sit on top of, so it pins them
# OFF -- otherwise "baseline" silently becomes the anchored engine and every
# comparison in here changes meaning without a line of this file changing.
ANCHOR_OFF = {"continuity_snap": 0, "prior_anchor_policy": ""}


def main() -> int:
    df = common.load_typed(common.MODULE).reset_index(drop=True)

    # Engineer's own answer for this batch -- read BEFORE the run, because the
    # engine's output frame carries columns of the same name.
    bench = np.c_[_n(df, "factory_recommended_new_max"), _n(df, "factory_recommended_new_rop")]
    has_bench = ~np.isnan(bench).any(axis=1)

    base = E.run(df, ANCHOR_OFF)
    route = base.route.values
    live = route != "dormant"
    e0 = _pair(base)
    lm, lr = ladder(df, base, SNAP, GUARD_USD)

    cur = np.nan_to_num(_n(df, "max_qty").values)
    price = _n(df, "unitprice").fillna(0).values
    item = df["item_id"].astype(str).values
    desc = df.get("item_desc", pd.Series("", index=df.index)).astype(str).values

    print(f"=== Jan'26 TCB batch: {len(df)} rows ===")
    print(pd.Series(route).value_counts().to_string())
    print(f"  engineer decision present on {int(has_bench.sum())} rows")

    print("\n=== WHAT CHANGES vs the shipped engine ===")
    moved = np.abs(lm - e0[:, 0]) + np.abs(lr - e0[:, 1]) > 0
    print(f"  rows the ladder moves: {int(moved.sum())} of {len(df)} "
          f"({moved.mean() * 100:.1f}%), all inside the {int(live.sum())} live rows: "
          f"{bool(moved[~live].sum() == 0)}")
    for r in ("active", "dying"):
        m = route == r
        print(f"    {r:7s} {int((moved & m).sum()):4d} of {int(m.sum()):4d}")
    print(f"  book value (sum unitprice x Max), whole batch:")
    for nm, mx in (("shipped engine", e0[:, 0]), ("ladder", lm),
                   ("current levels", cur), ("engineer", np.nan_to_num(bench[:, 0]))):
        print(f"    {nm:16s} ${np.nansum(price * mx):>12,.0f}")

    print("\n=== WAS IT RIGHT? (this batch's own engineer decisions, scorecard rule) ===")
    ok_e = _matched(e0, bench)
    ok_l = _matched(np.c_[lm, lr], bench)
    rows = []
    for lbl, m in (("all rows", has_bench), ("live rows", live & has_bench),
                   ("active", (route == "active") & has_bench),
                   ("dying", (route == "dying") & has_bench),
                   ("dormant", (route == "dormant") & has_bench)):
        rows.append({"segment": lbl, "n": int(m.sum()),
                     "engine%": round(ok_e[m].mean() * 100, 1),
                     "ladder%": round(ok_l[m].mean() * 100, 1),
                     "delta": round((ok_l[m].mean() - ok_e[m].mean()) * 100, 1)})
    print(pd.DataFrame(rows).to_string(index=False))

    prop_e = live & has_bench & (np.abs(e0[:, 0] - cur) > 0)
    prop_l = live & has_bench & (np.abs(lm - cur) > 0)
    print(f"\n  rows where the engine asks for a change: {int(prop_e.sum())}, "
          f"right on {ok_e[prop_e].mean() * 100:.1f}%")
    print(f"  rows where the ladder asks for a change: {int(prop_l.sum())}, "
          f"right on {ok_l[prop_l].mean() * 100:.1f}%")

    print("\n=== THE MOVED ROWS (largest book impact first) ===")
    idx = np.where(moved)[0]
    chg = pd.DataFrame({
        "item_id": item[idx], "description": desc[idx], "route": route[idx],
        "current_max": cur[idx], "engine_max": e0[idx, 0], "ladder_max": lm[idx],
        "engineer_max": bench[idx, 0],
        "engine_rop": e0[idx, 1], "ladder_rop": lr[idx], "engineer_rop": bench[idx, 1],
        "unitprice": price[idx], "c365": _n(df, "last_365_day_cnsmptn_qty").values[idx],
        "freq_months_with_usage": _n(df, "frequencymonthswithusage").values[idx],
        "avail_qty": _n(df, "avail_qty").values[idx],
        "usd_impact": (lm[idx] - e0[idx, 0]) * price[idx],
        "engine_was_right": ok_e[idx], "ladder_is_right": ok_l[idx],
    }).sort_values("usd_impact", key=np.abs, ascending=False)
    with pd.option_context("display.width", 300, "display.max_colwidth", 28):
        print(chg.head(20).to_string(index=False))
    chg.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n  total book impact of the moved rows: ${chg.usd_impact.sum():,.0f}")
    flip = pd.crosstab(chg.engine_was_right, chg.ladder_is_right)
    print("\n=== flip table on the moved rows (engine right? x ladder right?) ===")
    print(flip.to_string())
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
