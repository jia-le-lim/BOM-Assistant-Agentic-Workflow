"""S3 -- decision-behaviour EDA: what SFM proposed vs what the factory approved."""

import numpy as np
import pandas as pd

from common import OUT, blank, load_typed, pct, to_num

pd.set_option("display.width", 220)
df = load_typed("TCB")
N = len(df)

# ---------- 0. are the blank feature columns 'missing' or 'genuinely zero'? ----
print("=== BLANK-SEMANTICS TEST ===")
for col in ["frequencymonthswithusage", "days_since_last_issue", "order_qty_multiple", "partfreq"]:
    b = df[col].isna() if df[col].dtype != object else blank(df[col])
    t = df.groupby(b).agg(rows=("item_id", "size"),
                          zero_547=("last_547_day_cnsmptn_qty", lambda s: (s.fillna(0) == 0).sum()),
                          zero_365=("last_365_day_cnsmptn_qty", lambda s: (s.fillna(0) == 0).sum()))
    t.index = [f"{col} PRESENT", f"{col} BLANK"][: len(t)]
    print(t.to_string(), "\n")

# ---------- 1. derive the approved action ----------
cur, new = df["max_qty"].fillna(0), df["factory_recommended_new_max"].fillna(0)
df["action"] = np.select([new > cur, new < cur], ["Increase", "Decrease"], "Maintain")
df["sfm_action"] = (df["sfm_recommendation"].str.strip().str.title()
                    .str.replace(" Algo", "", regex=False))

print("=== APPROVED ACTION (derived from max_qty -> factory_recommended_new_max) ===")
print(df["action"].value_counts().to_string())
print(f"\nnet max_qty change across TCB: {int(new.sum() - cur.sum()):+d} units "
      f"(from {int(cur.sum())} to {int(new.sum())})")

print("\n=== SFM PROPOSAL vs FACTORY APPROVED ACTION ===")
ct = pd.crosstab(df["sfm_action"], df["action"], margins=True)
print(ct.to_string())
print("\nrow % (what the factory did with each SFM proposal):")
print((pd.crosstab(df["sfm_action"], df["action"], normalize="index") * 100).round(1).to_string())

agree = df["sfm_action"] == df["action"]
print(f"\nSFM/factory action agreement: {agree.sum()}/{N} ({pct(agree.sum(), N)})")
print(f"SFM_DISAGREEMENT rows       : {(~agree).sum()} ({pct((~agree).sum(), N)})")

# ---------- 2. recorded adoption labels ----------
print("\n=== RECORDED ADOPTION (source columns) ===")
print(pd.crosstab(df["max_adoption"], df["sfm_action"], margins=True).to_string())
override = ~df["max_adoption"].str.startswith("1_")
print(f"\nmax override rate: {override.sum()}/{N} ({pct(override.sum(), N)})")

# ---------- 3. PRD open question 9: recom_* vs factory_recommended_new_* ----
print("\n=== PRD Q9: is recom_max final or pre-review? ===")
for base in ["max", "rop", "min"]:
    r, f = df[f"recom_{base}"], df[f"factory_recommended_new_{base}"]
    both = r.notna() & f.notna()
    print(f"recom_{base}: filled={r.notna().sum()}  equal to factory_recommended={int((r[both] == f[both]).sum())}/{int(both.sum())} "
          f"({pct(int((r[both] == f[both]).sum()), int(both.sum()))})")
for base in ["max", "rop", "min"]:
    s, f = df[f"sfm_brr_{base}"], df[f"factory_recommended_new_{base}"]
    both = s.notna() & f.notna()
    print(f"sfm_brr_{base}: filled={s.notna().sum()}  equal to factory_recommended={int((s[both] == f[both]).sum())}/{int(both.sum())} "
          f"({pct(int((s[both] == f[both]).sum()), int(both.sum()))})")
for base in ["max", "rop", "min"]:
    a, f = df[f"atm_recommended_{base}"], df[f"factory_recommended_new_{base}"]
    both = a.notna() & f.notna()
    print(f"atm_recommended_{base}: filled={a.notna().sum()}  equal to factory_recommended={int((a[both] == f[both]).sum())}/{int(both.sum())} "
          f"({pct(int((a[both] == f[both]).sum()), int(both.sum()))})")

# ---------- 4. cost bands ----------
df["cost_band"] = pd.cut(df["unitprice"], [-.01, 10, 100, 1000, 10000, 1e9],
                         labels=["<=$10", "$10-100", "$100-1k", "$1k-10k", ">$10k"])
print("\n=== ACTION BY COST BAND ===")
print(pd.crosstab(df["cost_band"], df["action"], margins=True).to_string())
print("\nrow %:")
print((pd.crosstab(df["cost_band"], df["action"], normalize="index") * 100).round(1).to_string())

print("\n=== OVERRIDE RATE BY COST BAND ===")
print(df.groupby("cost_band", observed=True).apply(
    lambda g: pd.Series({"rows": len(g),
                         "override_pct": round(100 * (~g["max_adoption"].str.startswith("1_")).mean(), 1),
                         "median_price": round(g["unitprice"].median(), 2)}),
    include_groups=False).to_string())

# ---------- 5. PRD failure mode A: false decrease on low-cost recurring items ----
print("\n=== FAILURE MODE A: low-cost + recurring usage + SFM says Decrease ===")
freq = df["frequencymonthswithusage"].fillna(0)
recent = df["days_since_last_issue"]
c365 = df["last_365_day_cnsmptn_qty"].fillna(0)
for price_thr in [10, 50, 100, 500]:
    m = (df["unitprice"] <= price_thr) & (df["sfm_action"] == "Decrease")
    if m.sum() == 0:
        print(f"  unitprice<=${price_thr}: 0 SFM-Decrease rows")
        continue
    sub = df[m]
    rec = (freq[m] >= 2) | (c365[m] > 0)
    print(f"  unitprice<=${price_thr}: SFM-Decrease={m.sum()}, of which recurring-usage={rec.sum()}; "
          f"factory rescued (did not decrease)={(sub['action'] != 'Decrease').sum()} "
          f"({pct((sub['action'] != 'Decrease').sum(), m.sum())})")

print("\n  -- all SFM-Decrease rows, by whether factory followed --")
d = df[df["sfm_action"] == "Decrease"]
print(d.groupby("action").agg(rows=("item_id", "size"),
                              median_price=("unitprice", "median"),
                              median_365cons=("last_365_day_cnsmptn_qty", "median"),
                              median_clt=("contractual_lead_time", "median")).to_string())

# ---------- 6. PRD failure mode B needs a non-TCB baseline ----------
print("\n=== FAILURE MODE B REFERENCE: TCB vs non-TCB (aggregate only) ===")
allm = load_typed(None)
allm["cur"] = allm["max_qty"].fillna(0)
allm["new"] = allm["factory_recommended_new_max"].fillna(0)
allm["action"] = np.select([allm.new > allm.cur, allm.new < allm.cur], ["Increase", "Decrease"], "Maintain")
allm["grp"] = np.where(allm["module"] == "TCB", "TCB", "non-TCB")
ref = allm.groupby("grp").apply(lambda g: pd.Series({
    "rows": len(g),
    "pct_increase": round(100 * (g.action == "Increase").mean(), 1),
    "pct_decrease": round(100 * (g.action == "Decrease").mean(), 1),
    "pct_sfm_decrease": round(100 * g["sfm_recommendation"].str.strip().str.lower().eq("decrease algo").mean(), 1),
    "pct_dead": round(100 * g["aging_status"].eq("Dead").mean(), 1),
    "pct_max_override": round(100 * (~g["max_adoption"].fillna("").str.startswith("1_")).mean(), 1),
    "median_unitprice": round(g["unitprice"].median(), 2),
    "pct_stocked_new_max_gt0": round(100 * (g.new > 0).mean(), 1),
}), include_groups=False)
print(ref.to_string())

# ---------- 7. criticality ----------
print("\n=== sfm_criticality vs action (TCB) ===")
crit = df["sfm_criticality"].replace("", "(blank)")
print(pd.crosstab(crit, df["action"], margins=True).to_string())
print("\noverride rate by criticality:")
print(df.assign(crit=crit).groupby("crit").apply(
    lambda g: pd.Series({"rows": len(g),
                         "override_pct": round(100 * (~g["max_adoption"].str.startswith("1_")).mean(), 1)}),
    include_groups=False).to_string())

# ---------- 8. lead time & spend concentration ----------
print("\n=== LEAD TIME BANDS vs action ===")
df["clt_band"] = pd.cut(df["contractual_lead_time"], [-1, 30, 60, 90, 180, 1e6],
                        labels=["<=30d", "31-60d", "61-90d", "91-180d", ">180d"])
print(pd.crosstab(df["clt_band"], df["action"], margins=True).to_string())

print("\n=== SPEND CONCENTRATION (value at risk = new_max * unitprice) ===")
df["new_max_value"] = df["factory_recommended_new_max"].fillna(0) * df["unitprice"].fillna(0)
tot = df["new_max_value"].sum()
s = df.sort_values("new_max_value", ascending=False)
for k in [10, 25, 50, 100, 200]:
    print(f"  top {k:3d} items = {pct(s['new_max_value'].head(k).sum(), tot)} of ${tot:,.0f} approved max value")
print(f"  items carrying any value: {(df['new_max_value'] > 0).sum()}")

df.to_pickle(OUT / "s3_tcb.pkl")
