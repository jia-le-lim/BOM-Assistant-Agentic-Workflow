"""S2 -- data-quality audit of the TCB subset, framed as PRD section 6.1 Layer 1."""

import numpy as np
import pandas as pd

from common import OUT, blank, load_typed, pct

df = load_typed("TCB")
N = len(df)
print(f"TCB rows = {N}\n")

flags = pd.DataFrame(index=df.index)
findings = []


def check(name, mask, severity, note=""):
    mask = mask.fillna(False)
    flags[name] = mask
    findings.append(dict(check=name, severity=severity, n=int(mask.sum()),
                         pct=round(100 * mask.sum() / N, 2), note=note))


# --- DQ1 identity / duplicates -------------------------------------------
dup_item = df["item_id"].duplicated(keep=False)
check("DUP_ITEM_ID", dup_item, "High", "same item_id appears more than once in TCB")
key = df["item_id"].astype(str) + "|" + df["stockroom_id"].astype(str)
check("DUP_ITEM_STOCKROOM", key.duplicated(keep=False), "High", "duplicate item_id+stockroom_id key")
check("MISSING_ITEM_ID", blank(df["item_id"]), "High", "PRD 6.1: item_id not null")

# --- DQ2 stocking-level coherence (PRD 6.1: max >= rop >= min) -----------
mx, rp, mn = df["max_qty"], df["rop_qty"], df["min_qty"]
check("INVALID_CURRENT_STOCKING_LEVEL", (mx < rp) | (rp < mn), "High",
      "max_qty >= rop_qty >= min_qty violated on CURRENT values")
nmx, nrp, nmn = (df["factory_recommended_new_max"], df["factory_recommended_new_rop"],
                 df["factory_recommended_new_min"])
check("INVALID_RECOMMENDED_STOCKING_LEVEL", (nmx < nrp) | (nrp < nmn), "High",
      "same rule violated on the APPROVED factory recommendation")

# --- DQ3 consumption sanity ----------------------------------------------
cons = ["last_5_day_cnsmptn_qty", "last_30_day_cnsmptn_qty", "last_90_day_cnsmptn_qty",
        "last_180_day_cnsmptn_qty", "last_365_day_cnsmptn_qty", "last_547_day_cnsmptn_qty"]
neg = pd.Series(False, index=df.index)
for c in cons:
    neg |= df[c] < 0
check("NEGATIVE_CONSUMPTION", neg, "High", "negative qty in any consumption window")

mono = pd.Series(False, index=df.index)
for a, b in zip(cons, cons[1:]):
    mono |= df[a] > df[b]
check("CONSUMPTION_LADDER_BROKEN", mono, "High",
      "shorter window exceeds longer window (5<=30<=90<=180<=365<=547)")

check("NEGATIVE_OPEN_PO", df["open_po_qty"] < 0, "High", "PRD 6.1: open_po_qty not negative")

# --- DQ4 price & lead time ------------------------------------------------
check("MISSING_UNITPRICE", blank(df["unitprice"].astype(str)) | df["unitprice"].isna(), "High", "PRD 6.1: unitprice valid")
check("ZERO_UNITPRICE", df["unitprice"] == 0, "High", "unitprice = 0")
check("MISSING_LEAD_TIME", df["contractual_lead_time"].isna(), "High", "PRD 6.1: contractual_lead_time valid")
check("LEAD_TIME_OUT_OF_RANGE", (df["contractual_lead_time"] <= 0) | (df["contractual_lead_time"] > 365),
      "Medium", "CLT <= 0 or > 365 days")

# --- DQ5 features the business rules depend on ---------------------------
check("RULE1_UNUSABLE_missing_freq", df["frequencymonthswithusage"].isna(), "High",
      "Rule 1 LOW_COST_RECURRING_USAGE needs frequencymonthswithusage")
check("RULE1_5_UNUSABLE_missing_recency", df["days_since_last_issue"].isna(), "High",
      "Rules 1 & 5 need days_since_last_issue")
check("RULE3_UNUSABLE_missing_criticality", blank(df["sfm_criticality"]), "High",
      "Rule 3 CRITICAL_MACHINE_PROTECTION needs a criticality value")
check("MISSING_ORDER_QTY_MULTIPLE", df["order_qty_multiple"].isna(), "Medium",
      "PRD 6.1 / 6.3 MOQ rounding needs order_qty_multiple")

# --- DQ6 categorical hygiene ---------------------------------------------
sfm = df["sfm_recommendation"]
check("SFM_RECO_CASING", sfm.isin(["maintain Algo"]) | (sfm.str.strip() != sfm), "Medium",
      "inconsistent casing/whitespace in sfm_recommendation")
check("MISSING_NEW_MODULE", blank(df["new_modulle"]), "Medium", "new_modulle blank")
check("MISSING_FUNCTIONAL_GROUP", blank(df["functional_group"]), "Low", "functional_group blank")
check("UNACKNOWLEDGED_REVIEW", blank(df["review_acknowledge"]), "Medium",
      "row has no review_acknowledge = Y")

# --- DQ7 semantic contradictions -----------------------------------------
c365 = df["last_365_day_cnsmptn_qty"].fillna(0)
check("RECENCY_CONTRADICTS_CONSUMPTION",
      (c365 > 0) & df["days_since_last_issue"].isna(), "High",
      "consumed in last 365d but days_since_last_issue is blank")
check("DEAD_BUT_CONSUMING", (df["aging_status"] == "Dead") & (c365 > 0), "High",
      "aging_status = Dead but non-zero 365-day consumption")
check("STOCK_HELD_ON_DEAD_PART", (df["aging_status"] == "Dead") & (df["avail_qty"] > 0), "Medium",
      "on-hand stock against a Dead part")
check("RECOMMEND_STOCK_NO_USAGE",
      (nmx > 0) & (df["last_547_day_cnsmptn_qty"].fillna(0) == 0), "Medium",
      "approved new_max > 0 but zero consumption in 547 days")

fnd = pd.DataFrame(findings).sort_values(["severity", "n"], ascending=[True, False])
print("=== DATA-QUALITY SCORECARD (TCB, n=2780) ===")
print(fnd.to_string(index=False))

flags["any_high"] = flags[[f["check"] for f in findings if f["severity"] == "High"]].any(axis=1)
flags["n_flags"] = flags[[f["check"] for f in findings]].sum(axis=1)
print(f"\nrows with >=1 HIGH flag : {flags['any_high'].sum()} ({pct(flags['any_high'].sum(), N)})")
print(f"rows with zero flags    : {(flags['n_flags'] == 0).sum()} ({pct((flags['n_flags'] == 0).sum(), N)})")

fnd.to_csv(OUT / "s2_dq_scorecard.csv", index=False)
out = df[["item_id", "item_desc", "max_qty", "rop_qty", "min_qty", "unitprice",
          "aging_status", "sfm_recommendation"]].join(flags)
out[flags["n_flags"] > 0].to_csv(OUT / "s2_dq_flagged_rows.csv", index=False)

# --- context for interpretation ------------------------------------------
print("\n=== CONTEXT: is the missing-recency really 'never issued'? ===")
g = df.groupby(df["days_since_last_issue"].isna())["last_547_day_cnsmptn_qty"].agg(
    rows="size", zero_547=lambda s: (s.fillna(0) == 0).sum(), max_547="max")
g.index = ["days_since_last_issue PRESENT", "days_since_last_issue BLANK"]
print(g.to_string())

print("\n=== CONTEXT: aging_status vs consumption/stock ===")
print(df.groupby("aging_status").agg(
    rows=("item_id", "size"),
    pct_zero_365=("last_365_day_cnsmptn_qty", lambda s: round(100 * (s.fillna(0) == 0).mean(), 1)),
    median_unitprice=("unitprice", "median"),
    pct_with_stock=("avail_qty", lambda s: round(100 * (s.fillna(0) > 0).mean(), 1)),
    median_new_max=("factory_recommended_new_max", "median"),
).to_string())

print("\n=== CONTEXT: how much of TCB is 'live' at all? ===")
live = (df["max_qty"].fillna(0) > 0) | (df["factory_recommended_new_max"].fillna(0) > 0) | (c365 > 0)
print(f"rows with any of (max_qty>0, new_max>0, 365d consumption>0): {live.sum()} ({pct(live.sum(), N)})")
print(f"rows that are entirely dormant (all three zero)            : {(~live).sum()} ({pct((~live).sum(), N)})")
df.assign(is_live=live).to_pickle(OUT / "tcb_typed.pkl")
