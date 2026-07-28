# Phase 0 Data Analysis — BOM Review Jan'26, TCB Module

**Discovery findings against the Technical PRD for the BOM Review Assistant**

| | |
|---|---|
| Document type | Data analysis report (PRD §14 Phase 0 deliverable) |
| Analyst | LIM |
| Date | 28 July 2026 |
| Source | `BOM table/BOM REVIEW_Jan'26 .csv` (17,165 rows × 116 cols) |
| Scope | `module = TCB` — **2,780 rows** (16.2% of extract) |
| Companion | [Data_Dictionary_TCB_Jan26.md](Data_Dictionary_TCB_Jan26.md) |
| Reproducible via | [analysis/](../analysis/) — `s1_profile.py` … `s5_datadict.py` |

---

## 1. Executive Summary

The Jan'26 extract is a **completed review cycle**, not a pending batch: 96.3% of TCB rows carry `review_acknowledge = Y`, and `factory_recommended_new_max/rop/min` are 100% populated. Its value is as **labelled ground truth** for calibrating the engine — not as input to score.

Five findings materially affect the PRD as written:

1. **85% of the review roster is a no-op.** In 2,362 of 2,780 rows the engineer changed **nothing** — Max, ROP and Min all identical before and after. 2,051 of those are zero-stock, zero-usage items. One engineer processed 2,709 rows across four working days. **Auto-clearing this 85% is the product**, and it is a larger, more certain win than any improvement to recommendation accuracy.

2. **The `recom = 0` trap is the core business problem, and it is worth $1.3M.** In **631 rows (22.7%)** the `recom_max` algorithm proposed **zero stock** and the engineer overrode it upward. Those rows carry **$1,298,667 — 77% of all approved value in the file**. The reverse case (algorithm says stock, engineer zeroes it) occurs **3 times**. That is a **210:1 asymmetry**: the algorithm systematically under-stocks, and the entire manual review exists to catch it. The dominant justification is literally *"Ad-hoc consumption/Bulk withdraw"* (458 rows). See §6.6.

3. **`sfm_criticality` is not machine criticality, and criticality likely keys on `machine_type`, not `module`.** `sfm_criticality` is a demand/aging classifier (`H`/`M`/`L` = active-moving parts, `D` = dead); only 6 TCB rows are `H`. Separately, **APPJ machines appear *inside* TCB-module rows** (`PLASMATREAT, APPJ_PTU1612` 64 rows, `APPJ MFM-220` 28 rows) — so the PRD's TCB-vs-APPJ criticality contrast is not a module-level distinction. `machine_criticality_config` is required, and must be keyed correctly.

4. **Three of eight business rules are not computable.** `frequencymonthswithusage` is 89.8% blank, `days_since_last_issue` 65.4% blank, `sfm_criticality` 76.4% blank. Rules 1, 3 and 5 depend on them. Two are safely imputable (§4.2); criticality is not.

5. **Layer 4 cannot compare five candidate sources.** `ds_*`, `dp_*` and `sims_recommended_*` are 100% empty across all 17,165 rows, and "SFM" itself is ambiguous: `sfm_max` is 23.6% filled while `sfm_brr_max` and `atm_recommended_max` are 100%. A single definition of "the SFM value" must be fixed before `SFM_DISAGREEMENT` can be implemented.

**Recommendation: build the MVP around auto-clearing the 85% no-op tail and forcing structured justification on the insurance-stock decisions (§7.3), rather than around preventing over-decrease.** Decrease is 1.1% of this roster; the PRD's rule set is aimed at the smallest part of the problem.

---

## 2. Dataset Characterisation

### 2.1 Shape and coverage

| Property | Value |
|---|---|
| Rows (all modules) | 17,165 |
| Columns | 116 |
| TCB rows | 2,780 |
| Site / factory | `KM` / `AT_KuAT` — single-valued |
| Stockroom | `Kulim1` (17,162), `Ekatuju` (3) |
| Distinct `item_id` in TCB | 2,780 — **no duplicates** |
| `machine_type` values in TCB | 175 distinct, dominated by `ASM-Pacific,Phoenix` (1,002) and `KnS TCX3` |

### 2.2 TCB is overwhelmingly dormant

| Segment | Rows | Share |
|---|---:|---:|
| `aging_status = Dead` | 2,481 | 89.2% |
| `aging_status = Active Moving` | 213 | 7.7% |
| `aging_status = New Part` | 86 | 3.1% |
| **Fully dormant** (max_qty = 0, new_max = 0, 365d consumption = 0) | **2,065** | **74.3%** |
| Any live signal | 715 | 25.7% |

Current stocking levels are near-zero across the board: `max_qty` median 0, p95 = 1; 87.0% of TCB rows have `max_qty = 0`. The decision surface is far smaller than 2,780 rows suggests.

### 2.3 Spend concentration

Approved exposure (`factory_recommended_new_max × unitprice`) totals **$1,686,319** across just **682 items** with non-zero value.

| Top N items | Share of approved value |
|---:|---:|
| 10 | 28.3% |
| 25 | 42.6% |
| 50 | 57.8% |
| 100 | **75.6%** |
| 200 | 90.4% |

**Implication:** 100 items carry three-quarters of the money. A value-weighted review queue is far more efficient than a rule-count queue (§7.2).

---

## 3. Schema Reconciliation vs PRD §5.1

### 3.1 PRD columns that do not exist in the file

| PRD name | Actual | Action |
|---|---|---|
| `new_module` | **`new_modulle`** (typo in source) | Correct in ingestion, do not propagate |
| `sensitivity_tag` | **`senstivity_tag`** (typo in source) | Same |
| `review_required` | **absent entirely** | Confirms it is engine output to be created |

### 3.2 Candidate sources the PRD specifies that carry no data

`ds_recommended_max/rop/min`, `dp_recommended_max/rop/min`, `sims_recommended_max/rop/min` are **100% empty** — in TCB and across all 17,165 rows. `one_msia_*` is 2.9% filled, `sfm_max/rop/min` only 23.6%.

**PRD §6.4 Layer 4 cannot compare five candidate sources.** Only three are real: `sfm_brr_*` (100%), `atm_recommended_*` (100%), `recom_*` (96.1%).

### 3.3 Columns present but undocumented in the PRD

`alert_1dlt`, `rop_1dlt_sfm`, `max_delta`, `rop_delta`, `duplicate_algo`, `bunker_type`, `atk_active_flag`, `latest_atk_valid_to`, `avail_doi_maxd`, `avail_doi_maxd_group`, `uzb_doi_group2`, `onhand_pallet_qty`, `ea_per_pallet`, `qry_msbidata_rls`, `qry_msbidatadays_since_activation`, `qry_msbidatadays_since_effective`, `qry_msbidataopen_simi_shp_qty`, `sfm_mean_lt_cd`, `sfm_criticality`, `inventory_owner`, `area_owner`, `IPN2`.

Note `max_delta` / `rop_delta` are **currency**, not quantity — they track `spending_impact`, and should not be read as qty deltas.

---

## 4. Data Quality Assessment (PRD §6.1 Layer 1)

2,503 of 2,780 TCB rows (90.0%) raise at least one High flag — but this is dominated by **missing features, not corrupt values**. Separating the two changes the picture entirely.

### 4.1 Genuine data defects

| Check | Rows | % | Severity |
|---|---:|---:|---|
| `DEAD_BUT_CONSUMING` — `aging_status = Dead` yet non-zero 365d consumption | 16 | 0.58% | High |
| `NEGATIVE_CONSUMPTION` — negative qty in a consumption window (min −20) | 12 | 0.43% | High |
| `CONSUMPTION_LADDER_BROKEN` — shorter window exceeds longer window | 12 | 0.43% | High |
| `INVALID_RECOMMENDED_STOCKING_LEVEL` — approved values violate max ≥ rop ≥ min | 1 | 0.04% | High |
| `MISSING_LEAD_TIME` / `LEAD_TIME_OUT_OF_RANGE` (max 500 days) | 1 | 0.04% | High/Med |
| `SFM_RECO_CASING` — `"maintain Algo"` vs `"Maintain Algo"` | 1 | 0.04% | Medium |
| `max_adoption` trailing-space variant — `"1_Max Adopt SFM/BRR "` (2,588) vs `"1_Max Adopt SFM/BRR"` (18) | 2,606 | — | Medium |

**Structural integrity is good.** Zero duplicate `item_id`, zero missing `item_id`, zero missing or zero `unitprice`, zero negative `open_po_qty`, and **zero violations of max ≥ rop ≥ min on current values**. The defect list above is ~40 rows of real corruption.

The trailing-space and casing variants are trivial but will silently fragment any `GROUP BY` in the future data mart — normalise on ingestion.

### 4.2 Missing features — the real blocker

| Column | Blank | Blocks |
|---|---:|---|
| `frequencymonthswithusage` | 89.8% | **Rule 1** `LOW_COST_RECURRING_USAGE` |
| `sfm_criticality` | 76.4% | **Rule 3** `CRITICAL_MACHINE_PROTECTION` |
| `days_since_last_issue` | 65.4% | **Rules 1 & 5** |
| `order_qty_multiple` | 89.7% | MOQ rounding (PRD §6.3) |
| `new_modulle` / `functional_group` | 22.7% | Module routing |

**Blank ≠ missing for two of these.** Tested directly:

- `days_since_last_issue` blank → 1,816 of 1,819 rows have **zero 547-day consumption**. Blank means *never issued in the observable window*, not lost data. Safe to impute as "≥ 547 days" or a sentinel.
- `frequencymonthswithusage` blank → 2,464 of 2,495 rows have zero 547-day consumption. Same conclusion: impute **0**.

After imputation, Rules 1 and 5 become computable. **Rule 3 does not** — see §5.

### 4.3 Findings worth an engineering conversation

| Observation | Rows | % |
|---|---:|---:|
| `STOCK_HELD_ON_DEAD_PART` — on-hand stock against a Dead part | 931 | 33.5% |
| `RECOMMEND_STOCK_NO_USAGE` — approved `new_max > 0` with zero 547d consumption | 515 | 18.5% |
| `UNACKNOWLEDGED_REVIEW` — no `review_acknowledge` | 103 | 3.7% |

These are not necessarily errors — they may be deliberate insurance stocking (§6.2). But they are unexplained by the data, and they are exactly what the engine will need a reason code for.

---

## 5. PRD Open Questions — Evidence-Based Answers

### Q9 — Is `recom_max/rop/min` final or pre-review? → **Pre-review candidate**

Agreement with the approved `factory_recommended_new_*`:

| Candidate source | Fill | max | rop | min |
|---|---:|---:|---:|---:|
| `recom_*` | 96.1% | **75.1%** | 94.1% | 98.7% |
| `sfm_brr_*` | 100% | 79.4% | 95.0% | 99.1% |
| `atm_recommended_*` | 100% | **82.6%** | 95.6% | 99.1% |

`recom_max` disagrees with the final value on a quarter of rows, so it is **not** the final answer. Note it is also the *worst* predictor of the three — `atm_recommended_max` is closest. Treat `factory_recommended_new_*` as the single source of truth and all three others as Layer-4 candidates.

### Q6 — Which machines/modules are critical? → **Not answerable from this data**

`sfm_criticality` looks like a criticality field but is not. Cross-tabulated against `aging_status`:

| `sfm_criticality` | Active Moving | Dead | New Part |
|---|---:|---:|---:|
| `H` | 6 | 0 | 0 |
| `M` | 26 | 0 | 0 |
| `L` | 159 | 7 | 0 |
| `D` | 22 | 436 | 0 |
| *(blank)* | 0 | 2,038 | 86 |

`D` maps almost perfectly to Dead; `H`/`M`/`L` only ever apply to Active Moving parts. This is a **demand-frequency tier**, not machine criticality. If TCB were the critical bottleneck the PRD describes, TCB rows would not be 76% blank with only 6 `H`.

**Conclusion: `machine_criticality_config` (PRD §5.3) must be populated by engineers.** It cannot be derived. This is now a hard Phase-1 dependency, not a nice-to-have.

### Q5 — Threshold values → derived in §7.1

### Q8 — Authoritative source of truth

Within this file, `factory_recommended_new_*` is authoritative — 100% filled and internally consistent (1 violation in 2,780). Whether it round-trips to WINGS is still unverified and remains an open IT question.

---

## 6. Decision-Behaviour Analysis

### 6.1 What actually happened in Jan'26

Action derived by comparing `max_qty` → `factory_recommended_new_max`:

| Approved action | Rows | % |
|---|---:|---:|
| Maintain | 2,366 | 85.1% |
| **Increase** | **384** | **13.8%** |
| Decrease | 30 | 1.1% |

Net effect: TCB max quantities went from **869 → 2,213 units (+1,344, +155%)**. This was an *expansion* cycle.

SFM proposal versus approved action:

| SFM ↓ / Approved → | Decrease | Increase | Maintain | Total |
|---|---:|---:|---:|---:|
| Decrease | 21 | 7 | **159** | 187 |
| Increase | 3 | 114 | 30 | 147 |
| Maintain | 6 | **263** | 2,177 | 2,446 |
| **Total** | 30 | 384 | 2,366 | 2,780 |

- Action agreement: **83.2%** (2,312 rows)
- `SFM_DISAGREEMENT`: **16.8%** (468 rows)
- Recorded `max_adoption` override rate: **6.3%** (174 rows)

The two override measures differ because `max_adoption` compares against SFM/BRR *values*, while the action derivation compares against *current stock*. Both are legitimate; the engine should log both.

### 6.2 The dominant pattern: insurance stocking on dead parts

The largest disagreement cell is **SFM = Maintain → engineer = Increase (263 rows)**:

| Characteristic | Value |
|---|---|
| Units added | 444 |
| Value added | **$285,254** |
| Currently at `max_qty = 0` | 257 (97.7%) |
| **Zero consumption in 547 days** | 257 (97.7%) |
| `aging_status = Dead` | 237 (90.1%) |
| Median unit price | $185.40 |

Engineers are stocking parts that have never moved. This is rational — it is insurance against a bottleneck-machine stockout, and the PRD's own §1 argument about TCB criticality supports it. But:

- **No PRD rule covers it.** Rules 1–8 are all framed around *protecting from decrease*.
- **Rule 5 `NO_RECENT_USAGE` would flag 738 rows (26.5%) as decrease candidates**, directly contradicting the decision engineers actually make.
- There is **no field in the data recording why** — `justification` and `comments` are free text.

This is the single most important gap between the PRD and the data.

### 6.3 Failure Mode A (false decrease on low-cost recurring items) — rare *in this month's roster*

| Price ceiling | SFM-Decrease rows | Of which recurring usage | Factory did not decrease |
|---|---:|---:|---:|
| ≤ $10 | 16 | 2 | 12 (75.0%) |
| ≤ $50 | 38 | 3 | 33 (86.8%) |
| ≤ $100 | 52 | 4 | 45 (86.5%) |
| ≤ $500 | 88 | 8 | 75 (85.2%) |

Across all 187 SFM-Decrease rows in this roster, the **median 365-day consumption is 0**. The "low-cost but operationally necessary, still being consumed" profile the PRD describes occurs on at most 8 rows here.

> **Sampling caveat — do not over-read this.** The monthly BOM review does **not** present the same items every month; the roster varies. Jan'26 happens to contain no coupon parts under `module = TCB`. That is a property of **this month's selection, not of the TCB part population**, and it is not evidence that the coupon failure mode is absent from TCB. Rule 1 (`LOW_COST_RECURRING_USAGE`) must still be built; it simply cannot be *calibrated* from this file. Confirming its thresholds needs a month whose roster includes such parts.

### 6.4 Failure Mode B (critical-machine deprioritisation) — not supported *in this month*

*Reference baseline — aggregate non-TCB figures only, outside the agreed TCB scope, included because the claim is otherwise untestable.*

| Metric | TCB | non-TCB |
|---|---:|---:|
| Rows | 2,780 | 14,385 |
| Approved Increase | **13.8%** | 9.9% |
| Approved Decrease | **1.1%** | 3.9% |
| SFM proposed Decrease | **6.7%** | 23.4% |
| Max override rate | **6.3%** | 24.5% |
| Items stocked (`new_max > 0`) | 24.5% | 38.3% |
| Median unit price | $161.81 | $158.11 |

SFM proposes decrease on TCB **3.5× less often** than elsewhere, and TCB receives more increases and fewer decreases. TCB already appears to be treated preferentially.

The low override rate is genuinely ambiguous: it may mean SFM is already well-tuned for TCB, or that engineers scrutinise TCB less. **This cannot be resolved from the data** — it needs an engineer interview. Either way, the PRD's Rule 3 premise should not be built on until it is.

### 6.5 Where engineers push back

Override rate by unit-price decile:

| Decile | Price range | Override % | Increase % |
|---:|---|---:|---:|
| 1 | $0.01–9.90 | 5.0 | 17.9 |
| 2 | $10.00–23.40 | 4.0 | 11.9 |
| 3 | $23.62–47.70 | **2.5** | 6.1 |
| 4 | $47.78–92.53 | 4.7 | 8.3 |
| 5 | $92.91–161.80 | 5.8 | 13.7 |
| 6 | $161.82–309.98 | 5.4 | 14.7 |
| 7 | $310.50–692.06 | 7.6 | 18.3 |
| 8 | $693.00–1,560.34 | 7.2 | 18.0 |
| 9 | **$1,570.50–4,164.00** | **11.2** | 14.4 |
| 10 | $4,180.41–255,465.00 | 9.4 | 14.7 |

Override rate roughly doubles above ~$1,500. By lead time the effect is sharper:

| Lead-time band | Rows | Increase % | Override % |
|---|---:|---:|---:|
| ≤ 30d | 1,931 | 8.1 | **3.6** |
| 31–60d | 481 | **29.7** | **10.8** |
| 61–90d | 265 | 20.8 | 12.5 |
| 91–180d | 93 | 31.2 | 15.1 |
| > 180d | 9 | 11.1 | 66.7 |

The behavioural break is at **~30 days**, not the 90+ days one might assume. Also note `sfm_criticality = M` has a **53.8% override rate** (26 rows) — small but the highest of any segment, and worth an engineer conversation.

---

## 6.6 The `recom = 0` Trap — the actual failure mode

The mechanism, as described by the process owner:

> A machine has not gone down for a long time → no parts consumed → the algorithm sees zero demand and sets the recommended quantity to **0** → months later the machine goes down → the part is not in stock → the line waits for the lead time.

This is directly measurable in Jan'26, and it is the largest single pattern in the file.

### 6.6.1 The override asymmetry

| Direction | Rows | Approved value |
|---|---:|---:|
| `recom_max = 0` → engineer approved **> 0** | **631 (22.7%)** | **$1,298,667** |
| `recom_max > 0` → engineer approved **0** | **3 (0.1%)** | — |

**A 210:1 asymmetry.** Engineers almost never remove stock the algorithm asked for; they constantly add stock it refused. The $1.3M represents **77% of the entire $1.69M approved exposure** in this roster.

Profile of the 631 rows:

| Characteristic | Value |
|---|---|
| Zero consumption in 547 days | 502 (79.6%) |
| `aging_status = Dead` | 426 (67.5%) |
| Median contractual lead time | 38 days |
| Top justification | **`Ad-hoc consumption/Bulk withdraw` (458 rows)** |
| Next justifications | `Budget/DOI control` (68), `Follow SFM` (61), `Flat Future Consumption` (25) |

The engineers have already named the problem: demand on these parts is **ad-hoc**, so a demand-history algorithm cannot see it. `Ad-hoc consumption/Bulk withdraw` should become a first-class `reason_code`.

### 6.6.2 The trap firing in-sample

Parts whose **entire** 547-day consumption occurred inside the last 90 days — long idle, then a burst:

| Metric | Value |
|---|---|
| Burst parts (all 547d usage within last 90d) | 30 (15.4% of the 195 parts with any usage) |
| Of those, **`recom_max` was 0** | **28 of 30** |
| Of those, had `max_qty = 0` before review | 20 of 30 |
| Engineer set `new_max > 0` | 22 of 30 |
| Median unit price / lead time | $213.81 / 45 days |

**28 of 30 parts that turned out to need stock were assigned zero by the algorithm.** That is the failure mode caught in the act, within a single month.

### 6.6.3 The residual risk pool

2,051 rows (73.8%) remain at zero stock with zero usage after review. Most are genuinely dormant, but not all are low-risk:

| Segment | Rows | Wait if the machine goes down |
|---|---:|---|
| Stays zero, CLT ≥ 60 days | **196** | 2+ months |
| Stays zero, CLT ≥ 90 days | **50** | 3+ months |

**This directly qualifies the auto-clear recommendation (§7.2).** The no-op tail is mostly safe to clear, but these 196 parts sit inside it and are exactly the population the trap will hit next. They need a `LONG_LEAD_TIME_ZERO_STOCK` watch flag, not silent clearance.

## 7. Recommendations

### 7.1 Initial threshold values for `rule_config`

Derived from TCB distributions and observed override behaviour. These are **starting points for engineer validation**, not conclusions.

| Threshold | Proposed | Basis |
|---|---:|---|
| `low_cost_threshold` | **$50** | p25 = $33.64; override rate bottoms out in decile 3 ($23–48) |
| `high_cost_threshold` | **$1,500** | p80 = $1,562; override rate doubles above decile 9 |
| `long_lead_time_threshold` | **30 days** | Increase rate jumps 8.1% → 29.7% and override 3.6% → 10.8% crossing 30d. p50 = 25d, p75 = 40d |
| `recent_usage_days` | **180 days** | p10 of observed recency = 86d; ≤180d band shows 40%+ increase rate |
| `min_usage_months` | **2** | `frequencymonthswithusage` p75 = 1 among populated rows; 2 separates recurring from incidental |
| `max_change_pct_review` | **50%** | PRD default; fires on 48 rows (1.7%) — reasonable volume |

Flagged for discussion: `long_lead_time_threshold = 30 days` is much lower than the PRD implies. It is where the behaviour actually changes, but 69% of TCB rows sit at ≤30d, so a 30-day rule fires broadly. Consider 45 days as a compromise.

### 7.2 Size the review queue by value, not by rule count

Applying PRD §6.6 as written to TCB:

| Queue definition | Rows flagged | % of TCB | Share of $1.69M covered |
|---|---:|---:|---:|
| PRD §6.6 as written | 595 | 21.4% | — |
| **Value-gated** (§6.6 **and** exposure ≥ $1k, or price ≥ $1.5k, or criticality H/M) | **256** | **9.2%** | **84.3%** |

Adding a value gate **cuts review volume by 57% while still covering 84% of the money**. Given spend concentration (§2.3), this is the single highest-leverage change to the review logic.

Rule-by-rule firing rates on TCB:

| Rule | Fires | % | Assessment |
|---|---:|---:|---|
| R1 `LOW_COST_RECURRING_USAGE` | 16 | 0.6% | Near-dead in TCB |
| R2 `HIGH_COST_INCREASE_REVIEW` | 74 | 2.7% | Well-sized |
| R3 `CRITICAL_MACHINE_PROTECTION` | 8 | 0.3% | Blocked — no criticality data |
| R4 `LONG_LEAD_TIME_RISK` | 49 | 1.8% | Well-sized |
| R5 `NO_RECENT_USAGE` | 738 | 26.5% | **Too broad, and contradicts §6.2** |
| R6 `ABANDONED_TOOL` (proxy: Dead) | 2,481 | 89.2% | **Unusable — needs a real abandoned-tool source** |
| R7 `HIGH_USAGE_TOOL` (proxy: partfreq High) | 8 | 0.3% | Near-dead |
| R8 `INSUFFICIENT_DATA` (New Part) | 86 | 3.1% | Well-sized |
| `SFM_DISAGREEMENT` | 468 | 16.8% | Dominant driver |
| `MAX_CHANGE_GT_50PCT` | 48 | 1.7% | Well-sized |

R6 needs an actual tool-status source; `aging_status = Dead` is not a substitute. R5 needs to be gated behind criticality and value or it will fight the engineers.

### 7.3 New rules required — the trap, and the risk pool

Neither pattern in §6.6 is covered by PRD §6.2. Both are needed.

**Rule 9 — `ZERO_RECOMMENDATION_OVERRIDE`**
> **Condition:** `recom_max = 0` (or SFM/BRR = 0) **AND** the part has any of: lead time ≥ threshold, prior ad-hoc consumption, or an active machine linkage
> **Action:** `review_required = Y`; surface the item as a *candidate to stock*, not a candidate to clear
> **Reason code:** `ZERO_RECOMMENDATION_OVERRIDE` / `AD_HOC_CONSUMPTION`
> **Fires on:** 631 rows carrying $1.3M in Jan'26

This inverts the PRD's framing. Rules 1–8 all defend against the algorithm recommending *too little reduction*. The measured problem is the algorithm recommending **zero**.

**Rule 10 — `LONG_LEAD_TIME_ZERO_STOCK`**
> **Condition:** approved `new_max = 0` **AND** 547-day consumption = 0 **AND** `contractual_lead_time ≥ 60`
> **Action:** watch flag; exclude from auto-clear even though it is a no-op row
> **Reason code:** `LONG_LEAD_TIME_ZERO_STOCK`
> **Fires on:** 196 rows (50 at ≥ 90 days)

This is the safety carve-out from the auto-clear tail. These parts look identical to the 2,051 dormant rows but carry a 2–3 month recovery time if their machine goes down.

Both rules convert the highest-value decisions in the dataset into structured, auditable reason codes — precisely the ML training substrate PRD §6.7 wants, and the labelled data the prediction ambition in §9.2 will need.

### 7.4 Ingestion-layer fixes

1. Normalise trailing whitespace and casing on all categorical columns (`max_adoption`, `sfm_recommendation`).
2. Map source typos `new_modulle` → `new_module`, `senstivity_tag` → `sensitivity_tag`; do not propagate.
3. Impute `days_since_last_issue` and `frequencymonthswithusage` blanks as "no usage" sentinels, and record that imputation happened as a separate flag column.
4. Drop the 9 permanently-empty candidate columns (`ds_*`, `dp_*`, `sims_recommended_*`) or confirm with IT whether they are expected to populate later.
5. Reject or quarantine the ~40 rows with negative/non-monotonic consumption before they reach the engine.

### 7.5 Where the MVP should aim

Scope is confirmed as **TCB only**, with **Jan'26 as the sole dataset for v1**. Given that, the effort ranking implied by this roster is:

| Priority | Target | Volume | Why |
|---|---|---:|---|
| **1** | Catch the `recom = 0` trap (Rule 9, §7.3) | **631 rows / $1.3M** | The actual business problem. 210:1 override asymmetry; 77% of all approved value |
| **2** | Auto-clear the no-op tail | **2,362 rows (85.0%)** | Nothing changed. Highest-certainty time saving; needs no recommendation accuracy at all |
| **3** | Carve out the long-lead-time risk pool (Rule 10) | 196 rows (50 at ≥90d) | Prevents priority 2 from silently clearing the next stockout |
| **4** | Value-gate the remaining review queue (§7.2) | 256 rows cover 84.3% of value | Cuts residual review volume 57% |
| **5** | Prevent false decrease (PRD's original aim) | 30 approved decreases (1.1%) | Real, but the smallest slice of *this* roster — build Rule 1, calibrate on a month that contains such parts |

The PRD's rule set is weighted toward priority 5. Rebalancing toward 1–3 does not require abandoning any rule; it changes what Phase 1 is measured on.

**On the prediction ambition.** The stated goal — predict which zero-usage parts will be needed, using past months — is the right target, but PRD §9 puts the data bar at 10,000+ rows for quantity prediction and 2,000–5,000 for adoption prediction. Jan'26 provides 2,780 rows from one month and one reviewer. Rules 9 and 10 are the correct MVP: they capture the same decisions deterministically **and** generate the labelled history that makes the model trainable later.

---

## 8. Limitations

- **The roster is a monthly selection, not a population.** The same items are **not** reviewed every month. Every composition statistic in this report — 89.2% Dead, 74.3% dormant, zero coupons, the module mix — describes **who was on the Jan'26 list**, not what TCB stocks. Nothing here supports a claim that a part type is absent from TCB. The selection rule itself is unknown (see §9.1) and is arguably the most important undocumented part of the process.
- **Single cycle.** One snapshot — no trend, no seasonality, no way to test whether decisions led to stockouts or excess. PRD §6.7's feedback loop cannot be validated yet, and thresholds in §7.1 are fitted to one sample.
- **Single reviewer.** `modified_user` is `pengchin` on 2,709 of 2,780 rows (`yleowx` on 71), across four days (13–21 Aug 2025). Every "engineer decision" label is effectively one person's judgement — acceptable for MVP rules, a real bias risk for the Phase 4 ML.
- **Date discrepancy.** The file is named Jan'26 but all `modified_date` values fall in **August 2025**. Whether "Jan'26" is the effective period for the parameters or a labelling error is unresolved.
- **TCB-only scope** as agreed. Non-TCB figures in §6.4 are aggregate reference only and were not quality-checked.
- **Action is derived, not recorded.** `factory_recommendation_action` does not exist in the source; Increase/Maintain/Decrease was inferred from `max_qty` → `factory_recommended_new_max`. ROP and Min may tell a different story.
- **`justification` and `comments` are unparsed.** 95%+ populated and almost certainly the richest explanation of the §6.2 insurance-stocking pattern. Text mining them is the highest-value next analysis and was outside this scope.
- **No causal claims.** Override rates by price and lead time are associations. Confounding by module, machine and supplier was not controlled for.
- Rules R6/R7/R8 were simulated with proxies (`aging_status`, `partfreq`) because no abandoned-tool or tool-usage source exists in the file. Their firing rates are indicative only.

---

## 9. Open Questions & Next Steps

### 9.1 Resolved with the process owner (28 Jul 2026)

| # | Question | Answer | Consequence |
|---|---|---|---|
| 1 | What puts an item on a month's roster? | Items have a **defined review period** — it is a rotation, not a fixed list | Composition stats describe the roster, not TCB. Items **do** recur, so per-`item_id` history is viable |
| 4 | Ground truth for `review_required`? | **Ignore it.** `review_acknowledge` is blank when the engineer has freshly received the document | Blank = not yet reviewed (workflow state), not a quality label. Drops as an ML target |
| 5 | Is `max_qty` the live WINGS value? | **Yes** | The Increase/Maintain/Decrease derivation in §6.1 is valid |
| 6 | What is `recom_*`? | A separate algorithm, provenance unknown, **known to be inaccurate** — it zeroes parts for machines that have not failed recently | Reframed the entire report — see §6.6 |
| 8 | What is `review_history` for? | A memory layer recording **when and what was reviewed** | Standard audit table; combined with #1, per-item keying works |

**Criticality (#3)** is to be set by engineers at runtime — a configurable setting, or captured from natural language, with the priority for the current month applied by the agent.

> ⚠️ **Architecture guardrail.** Capturing an engineer's stated priority into `machine_criticality_config` is safe and auditable. An LLM *inferring* criticality on its own is not — it breaks PRD §1's principle ("the engine calculates, NYRA explains") and PRD §10's determinism requirement. Recommend: the LLM writes config, a human confirms it, the engine reads only the confirmed config. Every change versioned via `rule_version`.

### 9.2 Still open

2. **Which column is "the SFM value"?** — *owner unsure.* `sfm_max` (23.6% filled), `sfm_brr_max` (100%), `atm_recommended_max` (100%, closest to approved at 82.6%). **Proposed default:** treat `sfm_brr_*` as the benchmark, since the adoption labels themselves read `Adopt SFM/BRR` — the business already treats them as one. Needs confirmation from the SFM/WINGS owner before `SFM_DISAGREEMENT` is built.
7. **Are the `DSV:` codes an official taxonomy?** — *owner to explain later.* `DSV:Dead, remain current max=0 or 1` (642 rows), `DSV:3a, min(sfm,current)` (529 rows). If documented, they should seed `reason_code` directly.
9. Resolve with IT whether `ds_*` / `dp_*` / `sims_*` are dead columns or pending integrations.
10. **Is the roster date Jan'26 or Aug 2025?** All `modified_date` values are 13–21 Aug 2025.

### 9.3 Analysis backlog

11. **Text-mine `justification` / `comments`** — 100% of rows carry at least one. `Ad-hoc consumption/Bulk withdraw` (458 rows) is already a de facto reason code; the rest of the vocabulary is sitting there unextracted. Highest-value next analysis.
12. **Obtain further cycles** to validate §7.1 thresholds and build toward the PRD §9 data bar for prediction.
