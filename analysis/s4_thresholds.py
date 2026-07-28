"""S4 -- empirical threshold derivation, rule workload sizing, PRD open questions."""

import numpy as np
import pandas as pd

from common import OUT, blank, load_typed, pct

pd.set_option("display.width", 220)
df = pd.read_pickle(OUT / "s3_tcb.pkl")
N = len(df)

# ---------- A. hidden whitespace in adoption labels ----------
print("=== A. adoption label hygiene ===")
for c in ["max_adoption", "rop_adoption"]:
    vc = df[c].value_counts()
    for v, n in vc.items():
        print(f"  {c}: {n:5d}  repr={v!r}  has_nbsp={chr(160) in v}")

# ---------- B. where the disagreements actually are ----------
print("\n=== B. SFM_DISAGREEMENT breakdown (468 rows) ===")
dis = df[df["sfm_action"] != df["action"]]
print(dis.groupby(["sfm_action", "action"]).agg(
    rows=("item_id", "size"),
    median_price=("unitprice", "median"),
    median_clt=("contractual_lead_time", "median"),
    pct_dead=("aging_status", lambda s: round(100 * s.eq("Dead").mean(), 1)),
    median_avail=("avail_qty", "median"),
    total_value=("new_max_value", lambda s: round(s.sum())),
).to_string())

print("\n  dominant pattern: SFM=Maintain -> factory=Increase")
mi = df[(df["sfm_action"] == "Maintain") & (df["action"] == "Increase")]
print(f"  rows={len(mi)}  units added={int((mi.factory_recommended_new_max - mi.max_qty).sum())}  "
      f"value added=${(mi.new_max_value - mi.max_qty * mi.unitprice).sum():,.0f}")
print(f"  of these, currently max_qty=0 : {(mi.max_qty.fillna(0) == 0).sum()} ({pct((mi.max_qty.fillna(0) == 0).sum(), len(mi))})")
print(f"  of these, zero 547d usage     : {(mi.last_547_day_cnsmptn_qty.fillna(0) == 0).sum()} ({pct((mi.last_547_day_cnsmptn_qty.fillna(0) == 0).sum(), len(mi))})")
print(f"  of these, aging_status=Dead   : {mi.aging_status.eq('Dead').sum()} ({pct(mi.aging_status.eq('Dead').sum(), len(mi))})")

# ---------- C. empirical thresholds ----------
print("\n=== C. EMPIRICAL THRESHOLD CANDIDATES (TCB) ===")
p = df["unitprice"].dropna()
print("unitprice percentiles:")
for q in [.25, .5, .75, .8, .9, .95, .99]:
    print(f"  p{int(q*100):02d} = ${p.quantile(q):,.2f}")

ov = ~df["max_adoption"].str.strip().str.startswith("1_")
print("\noverride rate by unitprice decile (where engineers actually push back):")
df["price_decile"] = pd.qcut(df["unitprice"], 10, labels=False, duplicates="drop") + 1
print(df.groupby("price_decile").apply(lambda g: pd.Series({
    "rows": len(g), "price_lo": round(g.unitprice.min(), 2), "price_hi": round(g.unitprice.max(), 2),
    "override_pct": round(100 * (~g.max_adoption.str.strip().str.startswith("1_")).mean(), 1),
    "increase_pct": round(100 * g.action.eq("Increase").mean(), 1),
}), include_groups=False).to_string())

print("\ncontractual_lead_time percentiles:")
c = df["contractual_lead_time"].dropna()
for q in [.5, .75, .9, .95, .99]:
    print(f"  p{int(q*100):02d} = {c.quantile(q):.0f} days")
print("\naction/override by lead-time band:")
print(df.groupby("clt_band", observed=True).apply(lambda g: pd.Series({
    "rows": len(g),
    "increase_pct": round(100 * g.action.eq("Increase").mean(), 1),
    "override_pct": round(100 * (~g.max_adoption.str.strip().str.startswith("1_")).mean(), 1),
}), include_groups=False).to_string())

print("\ndays_since_last_issue percentiles (of the 961 rows that have it):")
r = df["days_since_last_issue"].dropna()
for q in [.1, .25, .5, .75, .9]:
    print(f"  p{int(q*100):02d} = {r.quantile(q):.0f} days")
print("\naction by recency band:")
df["recency_band"] = pd.cut(df["days_since_last_issue"], [-1, 90, 180, 365, 730, 1e6],
                            labels=["<=90d", "91-180d", "181-365d", "1-2y", ">2y"])
print(pd.crosstab(df["recency_band"], df["action"], margins=True).to_string())

# ---------- D. rule workload sizing ----------
print("\n=== D. PRD 6.2 RULE FIRING / WORKLOAD SIZING (TCB) ===")
freq = df["frequencymonthswithusage"].fillna(0)
rec = df["days_since_last_issue"]
c365 = df["last_365_day_cnsmptn_qty"].fillna(0)
c547 = df["last_547_day_cnsmptn_qty"].fillna(0)
LOW, HIGH, LONG_LT, RECENT, MINM = 100, 2000, 60, 180, 2

rules = {
    "R1 LOW_COST_RECURRING_USAGE": (df.unitprice <= LOW) & (freq >= MINM) & (rec <= RECENT),
    "R2 HIGH_COST_INCREASE_REVIEW": (df.unitprice >= HIGH) & (df.action == "Increase"),
    "R3 CRITICAL_MACHINE_PROTECTION": df["sfm_criticality"].isin(["H", "M"]) & (df.sfm_action == "Decrease"),
    "R4 LONG_LEAD_TIME_RISK": (df.contractual_lead_time >= LONG_LT) & (c365 > 0),
    "R5 NO_RECENT_USAGE": (c365 == 0) & (rec > 365) & ~df["sfm_criticality"].isin(["H", "M"]),
    "R6 ABANDONED_TOOL (proxy: aging=Dead)": df.aging_status.eq("Dead"),
    "R7 HIGH_USAGE_TOOL (proxy: partfreq High)": df.partfreq.eq("High"),
    "R8 INSUFFICIENT_DATA (proxy: New Part)": df.aging_status.eq("New Part"),
    "SFM_DISAGREEMENT": df.sfm_action != df.action,
    "MAX_CHANGE_GT_50PCT": (df.factory_recommended_new_max.fillna(0) >
                            1.5 * df.max_qty.fillna(0)) & (df.max_qty.fillna(0) > 0),
}
rr = pd.DataFrame([{"rule": k, "n": int(v.fillna(False).sum()),
                    "pct": round(100 * v.fillna(False).sum() / N, 1)} for k, v in rules.items()])
print(rr.to_string(index=False))

review = pd.Series(False, index=df.index)
for k in ["R2 HIGH_COST_INCREASE_REVIEW", "R3 CRITICAL_MACHINE_PROTECTION",
          "R4 LONG_LEAD_TIME_RISK", "R8 INSUFFICIENT_DATA (proxy: New Part)",
          "SFM_DISAGREEMENT", "MAX_CHANGE_GT_50PCT"]:
    review |= rules[k].fillna(False)
print(f"\nreview_required = Y under PRD 6.6 (as written): {review.sum()} ({pct(review.sum(), N)})")
print(f"review_required = N (auto-clearable)          : {(~review).sum()} ({pct((~review).sum(), N)})")

print("\n-- value-weighted alternative: review only where money/risk is --")
val = df["new_max_value"].fillna(0)
alt = review & ((val >= 1000) | (df.unitprice >= HIGH) | df["sfm_criticality"].isin(["H", "M"]))
print(f"review_required = Y, value-gated (>=$1k exposure): {alt.sum()} ({pct(alt.sum(), N)})")
print(f"  covers {pct(val[alt].sum(), val.sum())} of total approved max value (${val.sum():,.0f})")

# ---------- E. PRD Q6 criticality source ----------
print("\n=== E. is sfm_criticality a usable criticality source? ===")
print(pd.crosstab(df["sfm_criticality"].replace("", "(blank)"), df["aging_status"]).to_string())

# ---------- F. where does the 'coupon' failure mode actually live? ----------
print("\n=== F. locating the PRD's 'coupon' example (all modules, aggregate) ===")
allm = load_typed(None)
cp = allm[allm["item_desc"].str.contains("COUPON", case=False, na=False)]
print(f"rows with COUPON in description across all modules: {len(cp)}")
if len(cp):
    print(cp.groupby("module").agg(
        rows=("item_id", "size"),
        sfm_decrease=("sfm_recommendation", lambda s: s.str.strip().str.lower().eq("decrease algo").sum()),
        median_price=("unitprice", "median"),
        median_365cons=("last_365_day_cnsmptn_qty", "median"),
    ).sort_values("rows", ascending=False).head(10).to_string())
print(f"\ncoupon rows inside TCB module: {len(cp[cp['module'] == 'TCB'])}")
