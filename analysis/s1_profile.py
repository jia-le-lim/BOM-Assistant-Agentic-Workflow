"""S1 -- column profiling + data dictionary input for the TCB subset."""

import numpy as np
import pandas as pd

from common import (CANDIDATE_COLS, MEMORY_COLS, OUT, OUTPUT_COLS,
                    SENSITIVE_COLS, blank, load_raw, load_typed, to_num)

pd.set_option("display.width", 200)

raw = load_raw()
raw.columns = [c.strip() for c in raw.columns]
tcb = raw[raw["module"] == "TCB"].copy()
for c in tcb.columns:
    tcb[c] = tcb[c].str.strip()

print(f"population rows={len(raw)} cols={len(raw.columns)}")
print(f"TCB rows={len(tcb)}")

# ---------- PRD column reconciliation ----------
prd_expected = """item_id item_desc stockroom_id stockroom_name supplier_name machine_type module
new_module site factory max_qty rop_qty min_qty avail_qty vf_avail_qty consignment_qty
open_po_qty qry_eoh_excess_qty last_547_day_cnsmptn_qty last_365_day_cnsmptn_qty
last_180_day_cnsmptn_qty last_90_day_cnsmptn_qty last_30_day_cnsmptn_qty
last_5_day_cnsmptn_qty days_since_last_issue frequencymonthswithusage partfreq
previous_part_freq unitprice spending_impact contractual_lead_time new_clt_change_type
repair_type replenishment_policy order_qty_multiple purchasing_group_name ownership
gl_group category_type functional_group sensitivity_tag aging_status aging_new_flag
shared_parts shareable_indicator excess_status_tf other_stkrm_cons
other_stkrm_30d_cons_qty other_stkrm_90d_cons_qty other_stkrm_180d_cons_qty
other_stkrm_365d_cons_qty alternative_part with_repeat_uzb with_uzb_case_last_8_weeks
ind_sda psi sims sfm_recommendation sfm_max sfm_rop sfm_min sfm_brr_max sfm_brr_rop
sfm_brr_min atm_recommended_max atm_recommended_rop atm_recommended_min
ds_recommended_max ds_recommended_rop ds_recommended_min dp_recommended_max
dp_recommended_rop dp_recommended_min sims_recommended_max sims_recommended_rop
sims_recommended_min one_msia_max one_msia_rop one_msia_min recom_max recom_rop
recom_min factory_recommended_new_max factory_recommended_new_rop
factory_recommended_new_min review_required justification comments review_acknowledge
rop_adoption max_adoption ooq_adoption modified_user modified_date item_gl_account""".split()

actual = set(tcb.columns)
missing = [c for c in prd_expected if c not in actual]
extra = sorted(actual - set(prd_expected))
print("\n=== PRD columns NOT in file ===")
print(missing)
print("\n=== File columns NOT in PRD ===")
print(extra)

# ---------- per-column profile ----------


def classify(col: str) -> str:
    if col in OUTPUT_COLS:
        return "OUTPUT"
    if col in MEMORY_COLS:
        return "MEMORY"
    if col in CANDIDATE_COLS:
        return "CANDIDATE"
    if col in SENSITIVE_COLS:
        return "INPUT/SENSITIVE"
    return "INPUT"


rows = []
for c in tcb.columns:
    s = tcb[c]
    nb = (~blank(s)).sum()
    num = to_num(s)
    numeric_share = num.notna().sum() / nb if nb else 0
    is_num = numeric_share > 0.95 and nb > 0
    rec = {
        "column": c,
        "prd_class": classify(c),
        "fill_n": int(nb),
        "fill_pct": round(100 * nb / len(tcb), 1),
        "n_unique": int(s[~blank(s)].nunique()),
        "type": "numeric" if is_num else ("constant" if s[~blank(s)].nunique() <= 1 else "categorical"),
    }
    if is_num:
        d = num.dropna()
        if len(d):
            rec.update(
                min=d.min(), p25=d.quantile(.25), p50=d.median(),
                p75=d.quantile(.75), p95=d.quantile(.95), max=d.max(),
                mean=round(d.mean(), 2), n_zero=int((d == 0).sum()),
                n_negative=int((d < 0).sum()),
            )
    else:
        top = s[~blank(s)].value_counts().head(4)
        rec["top_values"] = " | ".join(f"{k}({v})" for k, v in top.items())[:200]
    rows.append(rec)

prof = pd.DataFrame(rows)
prof.to_csv(OUT / "s1_column_profile_tcb.csv", index=False)

print("\n=== fill-rate: fully empty in TCB ===")
print(prof[prof.fill_n == 0]["column"].tolist())

print("\n=== fill-rate: sparse (<50%) ===")
print(prof[(prof.fill_pct < 50) & (prof.fill_n > 0)][["column", "prd_class", "fill_pct", "n_unique"]].to_string(index=False))

print("\n=== constant columns (single value) ===")
cst = prof[prof.type == "constant"]
for c in cst["column"]:
    v = tcb[c][~blank(tcb[c])].unique()
    print(f"  {c}: {v[:1]}  (fill {prof.loc[prof.column == c, 'fill_pct'].iloc[0]}%)")

print("\n=== numeric summary (key cols) ===")
keyn = ["max_qty", "rop_qty", "min_qty", "unitprice", "avail_qty", "contractual_lead_time",
        "last_365_day_cnsmptn_qty", "last_90_day_cnsmptn_qty", "days_since_last_issue",
        "frequencymonthswithusage", "spending_impact", "order_qty_multiple",
        "factory_recommended_new_max", "factory_recommended_new_rop", "factory_recommended_new_min"]
print(prof[prof.column.isin(keyn)][["column", "fill_pct", "min", "p25", "p50", "p75", "p95", "max", "n_zero", "n_negative"]].to_string(index=False))

print("\n=== categorical key columns ===")
for c in ["machine_type", "new_modulle", "sfm_recommendation", "sfm_criticality",
          "aging_status", "partfreq", "replenishment_policy", "repair_type",
          "category_type", "gl_group", "senstivity_tag", "excess_status_tf",
          "review_acknowledge", "max_adoption", "rop_adoption", "functional_group",
          "bunker_type", "alert_1dlt", "duplicate_algo", "ind_sda", "shareable_indicator"]:
    if c in tcb.columns:
        vc = tcb[c][~blank(tcb[c])].value_counts()
        blanks = int(blank(tcb[c]).sum())
        print(f"\n-- {c}  (blank={blanks}, distinct={len(vc)})")
        print(vc.head(8).to_string())
