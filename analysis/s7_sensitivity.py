"""S7 -- operating-point frontier. How aggressive should the engine be?

Two dials, swept independently:
  rule9_mode      tight | balanced | wide | widest   (trap detection breadth)
  autoclear_guard on | off                           (never clear costly/slow items)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "engine"))
import engine  # noqa: E402

from common import OUT, load_typed  # noqa: E402

pd.set_option("display.width", 240)

df = load_typed("TCB")
N = len(df)
cur = df.max_qty.fillna(0)
eng_max = df.factory_recommended_new_max.fillna(0)
y_changed = ((eng_max != cur)
             | (df.factory_recommended_new_rop.fillna(0) != df.rop_qty.fillna(0))
             | (df.factory_recommended_new_min.fillna(0) != df.min_qty.fillna(0)))
value = eng_max * df.unitprice.fillna(0)
recom_zero = df.recom_max.fillna(-1) == 0
trap = recom_zero & (eng_max > 0)
trap_value = value[trap].sum()

rows = []
for mode in ["tight", "balanced", "wide", "widest"]:
    for guard in [False, True]:
        cfg = engine.load_config()
        cfg["rule9_mode"] = mode
        cfg["autoclear_guard"] = guard
        res = engine.run(df, cfg)
        rev = res.review_required.eq("Y")
        r9 = res.rc_ZERO_RECOMMENDATION_OVERRIDE
        cleared = ~rev
        miss = cleared & y_changed
        rows.append(dict(
            mode=mode, guard="on" if guard else "off",
            review_n=int(rev.sum()), review_pct=round(100 * rev.mean(), 1),
            workload_saved_pct=round(100 * cleared.mean(), 1),
            miss_n=int(miss.sum()),
            miss_rate_pct=round(100 * miss.sum() / max(cleared.sum(), 1), 1),
            miss_value_pct=round(100 * value[miss].sum() / value.sum(), 1),
            trap_catch_pct=round(100 * (r9 & trap).sum() / trap.sum(), 1),
            trap_value_pct=round(100 * value[r9 & trap].sum() / trap_value, 1),
            r9_precision=round((r9 & trap).sum() / max(r9.sum(), 1), 3),
            action_agree_pct=round(100 * (res.factory_recommendation_action == pd.Series(
                np.select([eng_max > cur, eng_max < cur], ["Increase", "Decrease"], "Maintain"),
                index=df.index)).mean(), 1),
            qty_exact_pct=round(100 * (res.factory_recommended_new_max == eng_max).mean(), 1),
        ))

f = pd.DataFrame(rows)
print("=== OPERATING-POINT FRONTIER (TCB, Jan'26) ===")
print("  review_pct        = share of roster sent to an engineer")
print("  miss_rate_pct     = of AUTO-CLEARED rows, share the engineer actually changed  <- safety metric")
print("  trap_catch_pct    = share of the 631 recom=0 overrides that Rule 9 flagged")
print()
print(f.to_string(index=False))

f.to_csv(OUT / "s7_frontier.csv", index=False)

# ---- threshold sensitivity on the chosen mode ----------------------------
print("\n=== THRESHOLD SENSITIVITY (rule9_mode=balanced, guard=on) ===")
out = []
for lt in [30, 45, 60, 90]:
    for hi in [1000, 1500, 2000, 5000]:
        cfg = engine.load_config()
        cfg.update(rule9_mode="balanced", autoclear_guard=True,
                   long_lead_time_threshold=lt, zero_stock_risk_clt=lt, high_cost_threshold=hi)
        res = engine.run(df, cfg)
        rev = res.review_required.eq("Y")
        miss = (~rev) & y_changed
        out.append(dict(long_lt=lt, high_cost=hi,
                        review_pct=round(100 * rev.mean(), 1),
                        miss_rate_pct=round(100 * miss.sum() / max((~rev).sum(), 1), 1),
                        miss_value_pct=round(100 * value[miss].sum() / value.sum(), 1)))
s = pd.DataFrame(out)
print(s.pivot(index="long_lt", columns="high_cost", values="review_pct").to_string())
print("\n  ^ review_pct | below: miss_rate_pct")
print(s.pivot(index="long_lt", columns="high_cost", values="miss_rate_pct").to_string())

# ---- does the SFM source choice matter? ----------------------------------
print("\n=== SENSITIVITY TO OPEN QUESTION 2 (which column is 'SFM') ===")
for src in ["sfm_brr", "sfm", "atm_recommended"]:
    cfg = engine.load_config(); cfg.update(rule9_mode="balanced", autoclear_guard=True)
    res = engine.run(df, cfg, sfm_source=src)
    rev = res.review_required.eq("Y")
    miss = (~rev) & y_changed
    print(f"  sfm_source={src:<16} review={100*rev.mean():5.1f}%  "
          f"miss_rate={100*miss.sum()/max((~rev).sum(),1):5.1f}%  "
          f"SFM_DISAGREEMENT fires={int(res.rc_SFM_DISAGREEMENT.sum()):4d}  "
          f"action_agree={100*(res.factory_recommendation_action==pd.Series(np.select([eng_max>cur,eng_max<cur],['Increase','Decrease'],'Maintain'),index=df.index)).mean():.1f}%")
