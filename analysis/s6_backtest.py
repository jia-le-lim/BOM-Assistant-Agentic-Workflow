"""S6 -- backtest the rule engine against what the TCB engineer actually decided."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "engine"))
import engine  # noqa: E402

from common import OUT, load_typed, pct  # noqa: E402

pd.set_option("display.width", 220)

df = load_typed("TCB")
N = len(df)
cfg = engine.load_config()

# ---- ground truth (labels -- never seen by the engine) --------------------
cur_max, cur_rop, cur_min = df.max_qty.fillna(0), df.rop_qty.fillna(0), df.min_qty.fillna(0)
eng_max = df.factory_recommended_new_max.fillna(0)
eng_rop = df.factory_recommended_new_rop.fillna(0)
eng_min = df.factory_recommended_new_min.fillna(0)

y_changed = (eng_max != cur_max) | (eng_rop != cur_rop) | (eng_min != cur_min)
y_action = pd.Series(np.select([eng_max > cur_max, eng_max < cur_max],
                               ["Increase", "Decrease"], "Maintain"), index=df.index)
value_at_stake = eng_max * df.unitprice.fillna(0)

print(f"TCB rows={N} | engineer changed something on {y_changed.sum()} ({pct(y_changed.sum(), N)})")

res = engine.run(df, cfg)
rev = res.review_required.eq("Y")

# ---- Task A: triage quality ----------------------------------------------
tp = (rev & y_changed).sum(); fp = (rev & ~y_changed).sum()
fn = (~rev & y_changed).sum(); tn = (~rev & ~y_changed).sum()
prec = tp / (tp + fp) if tp + fp else 0
rec = tp / (tp + fn) if tp + fn else 0
f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0

print("\n=== TASK A: should this row be reviewed? (label = engineer changed something) ===")
print(f"  flagged for review : {rev.sum()} ({pct(rev.sum(), N)})")
print(f"  auto-cleared       : {(~rev).sum()} ({pct((~rev).sum(), N)})")
print(f"\n  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
print(f"  precision={prec:.3f}  recall={rec:.3f}  F1={f1:.3f}")
print(f"\n  *** AUTO-CLEAR MISS RATE: {fn} of {(~rev).sum()} auto-cleared rows were actually changed "
      f"({pct(fn, (~rev).sum())}) ***")
print(f"  value wrongly auto-cleared: ${value_at_stake[~rev & y_changed].sum():,.0f} "
      f"of ${value_at_stake.sum():,.0f} ({pct(value_at_stake[~rev & y_changed].sum(), value_at_stake.sum())})")
print(f"  workload saved vs reviewing everything: {pct((~rev).sum(), N)}")

# ---- Task B: the trap ------------------------------------------------------
recom_zero = df.recom_max.fillna(-1) == 0
trap = recom_zero & (eng_max > 0)          # engineer overruled the zero
print("\n=== TASK B: catching the recom=0 trap (label = engineer stocked it anyway) ===")
print(f"  population with recom_max=0 : {recom_zero.sum()}")
print(f"  of those, engineer stocked  : {trap.sum()} (${value_at_stake[trap].sum():,.0f})")
r9 = res.rc_ZERO_RECOMMENDATION_OVERRIDE
caught = (r9 & trap).sum()
print(f"  Rule 9 fired on             : {r9.sum()} rows")
print(f"  *** TRAP CATCH RATE: {caught}/{trap.sum()} ({pct(caught, trap.sum())}) ***")
print(f"  value protected             : ${value_at_stake[r9 & trap].sum():,.0f} "
      f"({pct(value_at_stake[r9 & trap].sum(), value_at_stake[trap].sum())})")
r9_prec = caught / r9.sum() if r9.sum() else 0
print(f"  Rule 9 precision            : {r9_prec:.3f}  (fires on {r9.sum()}, {caught} were real)")
missed = trap & ~r9
print(f"  missed traps                : {missed.sum()}  (${value_at_stake[missed].sum():,.0f})")
if missed.sum():
    print("    profile of misses:")
    mm = df[missed]
    print(f"      median CLT {mm.contractual_lead_time.median():.0f}d | all Dead={mm.aging_status.eq('Dead').all()} "
          f"| median price ${mm.unitprice.median():,.2f} | any usage={(mm.last_547_day_cnsmptn_qty.fillna(0) > 0).sum()}")

# ---- Task C: action agreement ---------------------------------------------
print("\n=== TASK C: action agreement (engine vs engineer) ===")
print(pd.crosstab(res.factory_recommendation_action, y_action, margins=True).to_string())
agree = (res.factory_recommendation_action == y_action).sum()
print(f"  exact action agreement: {agree}/{N} ({pct(agree, N)})")

# ---- Task D: quantity accuracy (advisory only) ----------------------------
print("\n=== TASK D: quantity proposal accuracy (advisory) ===")
err = res.factory_recommended_new_max - eng_max
print(f"  exact match on new_max : {(err == 0).sum()} ({pct((err == 0).sum(), N)})")
print(f"  MAE={err.abs().mean():.2f}  RMSE={np.sqrt((err ** 2).mean()):.2f}  "
      f"median error={err.median():.1f}")
print(f"  engine over-stocks vs engineer on {(err > 0).sum()} rows, under-stocks on {(err < 0).sum()}")
print("  -> quantity output should stay ADVISORY in MVP; triage is the deployable part.")

# ---- reason code distribution ---------------------------------------------
print("\n=== reason codes fired ===")
rc = res[[c for c in res.columns if c.startswith("rc_")]].sum().sort_values(ascending=False)
for k, v in rc.items():
    if v:
        print(f"  {k[3:]:<34} {int(v):5d}  ({pct(v, N)})")

print("\n=== risk / confidence ===")
print(res.risk_level.value_counts().to_string())
print(f"  mean confidence {res.confidence_score.mean():.2f} | "
      f"below 0.6: {(res.confidence_score < 0.6).sum()}")

res.join(df[["item_desc", "unitprice", "contractual_lead_time"]]).to_csv(
    OUT / "s6_engine_output.csv", index=False)
print(f"\nwrote {OUT / 's6_engine_output.csv'}")
