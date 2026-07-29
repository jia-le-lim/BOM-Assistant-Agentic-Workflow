# Feature Selection — BOM Review Jan'26, TCB Module

**Which columns actually drive `factory_recommended_new_max / rop / min`, ranked, for Phase-4 ML**

| | |
|---|---|
| Document type | Feature-influence / model-input analysis |
| Analyst | LIM |
| Date | 29 July 2026 |
| Source | `BOM table/BOM REVIEW_Jan'26 .csv` (17,165 rows × 116 cols) |
| Scope | `module = TCB` — **2,780 rows** |
| Companions | [Analysis_Phase0_TCB_Jan26.md](Analysis_Phase0_TCB_Jan26.md) · [Engine_Backtest_TCB_Jan26.md](Engine_Backtest_TCB_Jan26.md) · [Data_Dictionary_TCB_Jan26.md](Data_Dictionary_TCB_Jan26.md) |
| Reproduce | `python s9_features.py` → `analysis/output/s9_feature_ranking.csv`, `s9_oof_univariate.csv` |

---

## 1. Verdict First

**Train one model, on one target, with about five features.**

| Question | Answer |
|---|---|
| How many models? | **One.** Max only — ROP is derivable, Min is not trainable |
| Which features? | `max_qty`, `machine_type`, `vf_avail_qty`, a `has_activity_record` flag, `qry_msbidatadays_since_effective` |
| How good does that get? | **57.4%** out-of-fold log-loss reduction; features 6–8 add 2.5pp combined |
| What is the strongest single predictor? | The **status quo** (`max_qty`), not any algorithm column |
| What is the biggest surprise? | **Machine identity beats consumption history.** Which machine a part belongs to predicts stocking far better than how much it was used |
| What must never be a feature? | `comments`, `justification` — out-of-fold AUC **0.987** and **0.923** |

The ranking below is deliberately split by decision stage, because **the features that decide *whether* to stock are not the features that decide *how many***.

---

## 2. Method

No `scikit-learn` in this environment (`pip`/`uv` cannot reach `files.pythonhosted.org` through the proxy), so all three metrics are implemented in numpy/pandas in [`s9_features.py`](../analysis/s9_features.py):

| Metric | What it protects against |
|---|---|
| **Permutation-adjusted mutual information** — MI minus the MI the same column scores against a shuffled target, averaged over 25 shuffles | Cardinality inflation. Un-adjusted, `category_type` (428 levels) ranks near the top purely on level count; its null bias alone is 0.194 bits |
| **5-fold out-of-fold target encoding** → log loss + AUC | In-sample overfitting. This is the honest check on the MI ranking |
| **Greedy forward selection**, L2 logistic on one-hot bins, 5-fold OOF | Redundancy. Collinear columns drop out on their own rather than all scoring high together |

Numeric columns are binned into 8 quantiles with **exact zero held as its own level** — zero is a decision in this dataset, not a small number. Fold-noise on the AUCs is roughly ±0.005.

---

## 3. The Target Is Three Different Problems

| Target | Non-zero rows | Distinct non-zero values | Verdict |
|---|---:|---:|---|
| `factory_recommended_new_max` | 682 (24.5%) | 18 | **Trainable** |
| `factory_recommended_new_rop` | 161 (5.8%) | 15 | **Derive from Max** |
| `factory_recommended_new_min` | **35 (1.3%)** | 5 | **Not trainable** |

### 3.1 ROP is close to a deterministic function of Max

| `new_max` | `new_rop` = 0 | 1 | 2 | 3–5 | 6+ |
|---|---:|---:|---:|---:|---:|
| 0 | 2,098 | 0 | 0 | 0 | 0 |
| 1 | **511** | 0 | 0 | 0 | 0 |
| 2 | 6 | **100** | 0 | 0 | 0 |
| 3–5 | 3 | 0 | **31** | 11 | 0 |
| 6+ | 1 | 1 | 0 | 7 | **11** |

Where ROP > 0, the median ratio is exactly **0.50 × Max** (109 of 161 rows; 0.67 on a further 24). A rounding rule reproduces this better than a model fitted on 161 examples ever will.

### 3.2 Min cannot be modelled from this file

35 positive rows across 5 distinct values, against PRD §9's own bar of 10,000+ rows for quantity prediction. Any accuracy figure quoted for a Min model would be noise. **Derive it, or leave it at the current value.**

> **Consequence for the PRD.** §6.1's `max ≥ rop ≥ min` invariant is not a post-hoc validation step — it is the generating rule for two of the three outputs. Model Max; enforce the invariant to produce the rest.

---

## 4. Ranked Influence — the "whether to stock" decision

This is where the money is: 100% of the $1.69M approved exposure sits in the 682 rows that get stocked at all.

| # | Category | Best column | OOF AUC | Marginal lift in selection | Assessment |
|---:|---|---|---:|---|---|
| **1** | **Current stocking state** | `max_qty` | 0.739 | **1st pick — 33.2% of all loss reduction** | Status quo dominates. Reproduces the answer on 85.1% of rows |
| **2** | **Machine / part identity** | `machine_type` | **0.837** | **2nd pick — +16.5pp** | Highest AUC of any legitimate column. 175 levels, survives permutation adjustment |
| **3** | **Stocking-regime flag** | `replenishment_policy` | 0.735 | 28.7% alone, absorbed by 1+2 | The cleanest split in the file (§7.2) |
| **4** | **Record completeness** | `sfm_criticality` | 0.661 | **4th pick — +1.9pp** | Two latent flags, not nine columns (§8) |
| **5** | **Inventory position** | `vf_avail_qty` | 0.739 | **3rd pick — +4.5pp** | ≤1 on hand → 9.1% stocked; >10 → 47.1% |
| **6** | **Master-data age** | `qry_msbidatadays_since_effective` | 0.771 | **5th pick — +1.3pp** | Undocumented in the PRD. Genuine, unexplained (§6.3) |
| **7** | **Algorithm candidates** | `sfm_recommendation` | 0.700 | 6th pick — +0.8pp | The *categorical* beats every numeric candidate (§6.3) |
| **8** | **Consumption history** | `last_547_day_cnsmptn_qty` | 0.599 | not selected | **Weak here, #1 for magnitude** (§5) |
| **9** | **Lead time / supply** | `contractual_lead_time` | 0.751 | not selected | Contaminated by a default value (§7.1) |
| **10** | **Cost / value** | `unitprice` | 0.586 | not selected | Drives *how many* and override rate, not *whether* |
| **11** | Sharing / cross-stockroom | `excess_status_tf` | 0.593 | not selected | Group best reaches 3.6% of target entropy. **Drop** |
| — | *Org / ownership* | *`purchasing_group_name`* | *0.778* | *never picked* | **Excluded on principle** (§9) |
| — | *Engineer's decision record* | *`comments`* | *0.987* | *excluded* | **Leakage** (§6.1) |

Group roll-up by permutation-adjusted MI against the binary stock decision, for cross-reference:

| Group | Best column | % of target entropy |
|---|---|---:|
| Machine / part identity | `machine_type` | 34.6 |
| Current stocking parameters | `max_qty` | 33.3 |
| Inventory position | `uzb_doi_group2` | 31.3 |
| Lead time / supply risk | `replenishment_policy` | 29.3 |
| Algorithm candidates | `sfm_recommendation` | 26.0 |
| Age of master data | `qry_msbidatadays_since_effective` | 19.4 |
| Cost / value | `max_delta` | 18.8 |
| Consumption / demand history | `aging_status` | 17.3 |
| Sharing / cross-stockroom | `alternative_part` | 2.5 |

### 4.1 The selected set

| Step | Feature added | OOF log loss | Cumulative reduction |
|---:|---|---:|---:|
| 0 | *(intercept only)* | 0.5571 | — |
| 1 | `max_qty` | 0.3720 | **33.2%** |
| 2 | `machine_type` | 0.2804 | **49.7%** |
| 3 | `vf_avail_qty` | 0.2553 | 54.2% |
| 4 | `sfm_criticality` | 0.2445 | 56.1% |
| 5 | `qry_msbidatadays_since_effective` | 0.2376 | **57.4%** |
| 6 | `sfm_recommendation` | 0.2327 | 58.2% |
| 7 | `atm_recommended_max` | 0.2285 | 59.0% |
| 8 | `supplier_name` | 0.2235 | 59.9% |

Steps 6–8 buy **2.5pp for three more columns**. Five features is the model.

---

## 5. Ranked Influence — the "how many" decision

Among the 682 stocked rows only. Spearman ρ against `log1p(new_max)`:

| Category | Column | ρ | n |
|---|---|---:|---:|
| **Consumption history** | `frequencymonthswithusage` | **0.570** | 237 |
| Consumption history | `last_180_day_cnsmptn_qty` | 0.452 | 682 |
| Algorithm candidates | `recom_max` | 0.450 | 670 |
| Current stocking state | `rop_qty` | 0.428 | 682 |
| Algorithm candidates | `sfm_brr_max` | 0.416 | 682 |
| Inventory position | `vf_avail_qty` | 0.365 | 682 |
| Consumption history | `days_since_last_issue` | **−0.341** | 379 |
| **Cost / value** | `unitprice` | **−0.303** | 682 |
| Current stocking state | `max_qty` | 0.066 | 682 |

**The ordering inverts.** Consumption history is the weakest useful category for *whether* and the strongest for *how many*. `max_qty` flips from rank 1 to effectively zero (ρ = 0.066). And `unitprice` is **negative** — expensive parts do get stocked, just in smaller quantities.

Approved Max does track demand once the stocking decision is made:

| 547-day consumption | Rows | Median `new_max` | p90 |
|---|---:|---:|---:|
| 0 | 515 | 1.0 | 2.0 |
| 1 | 69 | 1.0 | 2.0 |
| 2–3 | 44 | 1.0 | 3.7 |
| 4–10 | 22 | 2.0 | 6.9 |
| > 10 | 32 | **4.5** | 34.7 |

Note the top row: **515 of 682 stocked parts had zero consumption in 547 days.** That is the [`recom = 0` trap](Analysis_Phase0_TCB_Jan26.md#66-the-recom--0-trap--the-actual-failure-mode) restated as a feature-selection fact — demand history cannot explain the stocking decision, because for 75% of stocked parts there is no demand to observe.

---

## 6. Leakage, Redundancy and Two Corrections

### 6.1 `comments` and `justification` are leakage — and they are near-perfect

Out-of-fold AUC **0.987** and **0.923**; 84.4% and 64.3% of the stock-decision entropy. They are the engineer's own decision record, written at decision time and unavailable at inference. [`common.py`](../analysis/common.py) already classes them `MEMORY_COLS` — that classification is load-bearing, not cosmetic.

The same applies to `max_adoption` / `rop_adoption` (definitionally a comparison against the approved value), `review_acknowledge`, `modified_user` and `modified_date`.

### 6.2 Correction — `max_delta` is *not* leakage, it is redundant

The [Data Dictionary](Data_Dictionary_TCB_Jan26.md) notes only that `max_delta` / `rop_delta` are currency rather than quantity. The exact provenance:

| Hypothesis | Match, all rows | Match, among the 236 non-zero `max_delta` rows |
|---|---:|---:|
| `(approved_new − current) × price` | 82.6% | 29.2% |
| `(recom_max − current) × price` | 91.7% | 68.2% |
| `(sfm_brr_max − current) × price` | 94.9% | 95.8% |
| **`(atm_recommended_max − current) × price`** | **100.0%** | **100.0%** |

`max_delta` is the *algorithm's* proposed money impact, computed before the engineer acts. **Safe to use, but it adds nothing** over `atm_recommended_max`, `max_qty` and `unitprice`. The 82.6% figure against the approved value is an artefact of both sides being zero on 2,093 rows.

Separately: **`spending_impact` is non-zero on 5 rows** of 2,780. Treat it as a dead column.

### 6.3 Correction — the algorithms' 82.6% accuracy is mostly matching zeros

Restricted to the rows where *either* the algorithm or the engineer wants stock:

| Candidate | Match, all rows | Both-zero rows | Non-trivial rows | **Match, non-trivial** | Said 0, engineer said >0 |
|---|---:|---:|---:|---:|---:|
| `atm_recommended_max` | 82.6% | 2,093 | 687 | **29.7%** | 446 |
| `sfm_brr_max` | 79.4% | 2,078 | 702 | **18.4%** | 506 |
| `recom_max` | 75.7% | 2,095 | 685 | **1.3%** | 643 |
| `max_qty` (do nothing) | 85.1% | 2,084 | 696 | **40.5%** | 335 |

**Doing nothing beats every algorithm on the decisions that carry money**, consistent with [Engine_Backtest §2](Engine_Backtest_TCB_Jan26.md). `recom_max` is correct on **1.3%** of them.

This changes how the candidate columns should be used: as *features*, never as an anchor or a fallback. And the categorical `sfm_recommendation` is more useful than any of the numerics —

| `sfm_recommendation` | Rows | Stock rate |
|---|---:|---:|
| `Maintain Algo` | 2,445 | **15.3%** |
| `Increase Algo` | 147 | 89.1% |
| `Decrease Algo` | 187 | **93.6%** |

`Decrease` and `Increase` both mean ~90% stocked. The column is not signalling direction; it is signalling **"the algorithm has an opinion at all"**, i.e. the part is live.

### 6.4 The identity columns are one variable

Cramér's V:

| | `machine_type` | `supplier_name` | `new_modulle` | `functional_group` | `area_owner` |
|---|---:|---:|---:|---:|---:|
| `machine_type` | 1.00 | 0.73 | **0.99** | **0.98** | **0.90** |
| `supplier_name` | 0.73 | 1.00 | 0.38 | 0.51 | **1.00** |
| `new_modulle` | **0.99** | 0.38 | 1.00 | **1.00** | 0.45 |

`new_modulle` and `functional_group` are the same variable (V = 1.00). `machine_type` subsumes both. `supplier_name` is perfectly determined by `area_owner`, `purchasing_group_name` and `inventory_owner` (V = 1.00 across all three). **Keep `machine_type` + `supplier_name`; the rest are duplicates.**

---

## 7. Data Artefacts That Will Mislead a Model

### 7.1 `contractual_lead_time` has a system default poisoning it

| CLT (days) | Rows | Stock rate |
|---:|---:|---:|
| **25** | **1,245 (44.8%)** | **5.3%** |
| 15 | 347 | 24.5% |
| 20 | 143 | 30.8% |
| 30 | 58 | 43.1% |
| 35 | 55 | 60.0% |
| 55 | 102 | 46.1% |

**25 days is a placeholder**, not a measurement. Fed raw, a model learns "25 days → do not stock", which is a statement about data entry. The genuine effect — longer lead time, higher stock rate — only appears once the default is separated out.

**Fix:** add a `clt_is_default_25` indicator and treat 25 as missing. This also qualifies [Phase 0 §7.1](Analysis_Phase0_TCB_Jan26.md)'s proposed `long_lead_time_threshold = 30 days`, which sits directly on top of the default cluster.

### 7.2 `uzb_doi_group2` carries its signal in a formatting prefix

| Form | Rows | Stock rate |
|---|---:|---:|
| `">365"` | 2,331 | **12.7%** |
| `"(I) >365"` | 303 | **86.1%** |

Same nominal band, 6.8× difference in outcome. The parenthesised prefix is very nearly `replenishment_policy`:

| | `Order to Demand` | `Order To Max` | `Order Beyond Max` |
|---|---:|---:|---:|
| No prefix | 2,342 | 0 | 0 |
| `(X)` prefix | 34 | **392** | **12** |

> ⚠️ **This directly conflicts with [Phase 0 §7.4](Analysis_Phase0_TCB_Jan26.md) item 1**, which recommends normalising whitespace and casing on all categorical columns. Applied naively to `uzb_doi_group2`, that normalisation **destroys an 86%-vs-13% separator**. Extract the prefix into its own column *before* normalising the band.

Because the prefix ≈ `replenishment_policy`, keep **one** of the two, not both:

| `replenishment_policy` | Rows | Stock rate |
|---|---:|---:|
| `Order to Demand` | 2,376 | **13.7%** |
| `Order To Max` | 392 | **88.3%** |
| `Order Beyond Max` | 12 | 91.7% |

### 7.3 `qry_msbidatadays_since_effective` is real but unexplained

Selected 5th, OOF AUC 0.771, absent from the PRD:

| Days since effective | Rows | Stock rate |
|---|---:|---:|
| 7 – 1,055 | 499 | 43.3% |
| 1,055 – 1,586 | 433 | **56.4%** |
| 1,586 – 4,079 | 1,135 | **8.4%** |
| 4,079 – 4,120 | 277 | 7.9% |
| 4,120 – 7,888 | 436 | 24.1% |

Non-monotonic, and plausibly a proxy for part generation or tool vintage. **Use it, but flag it for the WINGS owner** — if it is a load-date rather than a business date, it will not generalise across monthly extracts.

---

## 8. The Sparse Columns Are Two Flags, Not Nine Features

Jaccard overlap of *blank patterns* reveals two independent clusters (1.00 = identical missingness):

| Cluster | Columns | Internal Jaccard | P(stock \| blank) | P(stock \| present) |
|---|---|---:|---:|---:|
| **A. Activity record** | `sfm_criticality`, `days_since_last_issue`, `frequencymonthswithusage`, `partfreq`, `new_clt_change_type` | 0.69 – **1.00** | 0.167 – 0.178 | 0.394 – **0.832** |
| **B. Ownership record** | `area_owner`, `inventory_owner`, `new_modulle`, `functional_group` | 0.55 – **1.00** | **0.578 – 0.796** | 0.129 – 0.148 |

`frequencymonthswithusage` and `partfreq` have **identical** blank patterns, as do `new_modulle` and `functional_group`. Cross-cluster overlap is 0.03 – 0.25, so these are genuinely two variables.

Cluster B runs in the **opposite** direction — a *missing* owner predicts stocking at 79.6%. That group is 41.1% `Order To Max` against 9.2% elsewhere, so it is the managed-to-a-max population rather than a data-quality problem.

**Recommendation:** engineer `has_activity_record` and `has_owner_record` as explicit booleans, plus the values where present. Feeding nine correlated sparse columns spends model capacity re-learning two bits.

This refines [Phase 0 §4.2](Analysis_Phase0_TCB_Jan26.md), which established that blank means "never issued" rather than lost data. That holds — and the *value* still matters where present: `days_since_last_issue` reaches 0.983 in-sample AUC among its 961 populated rows. Impute with a sentinel **and** keep the flag; do not collapse to the flag alone.

---

## 9. Excluded on Principle — Org and Ownership

| Column | OOF AUC | Loss reduction |
|---|---:|---:|
| `purchasing_group_name` | 0.778 | 25.9% |
| `area_owner` | 0.777 | 28.4% |
| `inventory_owner` | 0.759 | 25.1% |

These score in the top ten and should still be excluded:

1. **They are redundant.** Cramér's V = 1.00 against `supplier_name`, 0.90 against `machine_type`. When allowed into forward selection they were **never picked** — machine identity absorbs them entirely, and the selected set is identical with or without them.
2. **They encode reviewer identity.** `modified_user` is `pengchin` on 2,709 of 2,780 rows. A model that learns "Tan, Jaz's parts get 10.5% stocked" learns one person's workload allocation, and will mispredict the moment ownership is reassigned.
3. **They cost nothing to drop.** Zero measured accuracy loss.

Keep them as an **audit slice** for fairness checks on model output. Never as inputs.

---

## 10. Recommended Feature Specification

### 10.1 Use

| Feature | Source | Engineering |
|---|---|---|
| `max_qty`, `rop_qty` | direct | Plus a `currently_stocked = max_qty > 0` boolean — that boolean carries most of the signal |
| `machine_type` | direct | 175 levels / 682 positives → **target encoding with strong smoothing**, or roll up to a curated family |
| `supplier_name` | direct | 25 levels, safe as one-hot |
| `replenishment_policy` | direct | 3 levels. Highest-value single categorical |
| `vf_avail_qty`, `avail_qty` | direct | Quantile-binned, zero held separate |
| `has_activity_record` | **derived** | Cluster A of §8 |
| `has_owner_record` | **derived** | Cluster B of §8 |
| `days_since_last_issue` | direct + sentinel | Keep value where present; pair with the flag |
| `sfm_recommendation` | direct | As a categorical. Read it as "algorithm has an opinion", not as direction |
| `atm_recommended_max`, `sfm_brr_max` | direct | Features only — never anchors (§6.3) |
| `contractual_lead_time` + `clt_is_default_25` | direct + **derived** | §7.1 |
| `uzb_doi_prefix` | **derived** | §7.2 — extract before normalising |
| `qry_msbidatadays_since_effective` | direct | Pending owner confirmation (§7.3) |
| Consumption windows (547/365/180/90/30d) | direct | **Magnitude model only** — near-useless for the stock/no-stock decision |
| `unitprice` | direct | **Magnitude model only**, and the sign is negative |

### 10.2 Do not use

| Reason | Columns |
|---|---|
| **Leakage** | `comments`, `justification`, `max_adoption`, `rop_adoption`, `review_acknowledge`, `modified_user`, `modified_date` |
| **Reviewer identity** | `area_owner`, `inventory_owner`, `purchasing_group_name` |
| **Redundant** | `max_delta`, `rop_delta` (§6.2), `new_modulle`, `functional_group`, `category_type` (§6.4) |
| **No signal** | All of group G (best 3.6% of entropy): `shareable_indicator` (AUC 0.499), `shared_parts` (0.487), `other_stkrm_*` (1.4% filled), `alternative_part` (0.509). Also `open_po_qty` (0.492), `rop_delta` (0.499) |
| **Dead / constant** | `spending_impact` (5 non-zero rows), `site`, `factory`, `module`, `ds_*`, `dp_*`, `sims_recommended_*`, `ind_sda`, `duplicate_algo`, `psi`, `atk_active_flag` |
| **Needs NLP first** | `item_desc` — AUC 0.525 as a raw 2,685-level categorical. Real signal likely exists in the text, but not as an ID |

---

## 11. Limitations

- **One month, one reviewer.** 2,780 rows, `pengchin` on 97.5% of them, across four days. Every ranking here is one person's judgement, and `machine_type`'s advantage over consumption history may partly be *that engineer's* mental model rather than the business's.
- **The roster rotates.** Per [Phase 0 §8](Analysis_Phase0_TCB_Jan26.md), items have a defined review period, so Jan'26 is a rotating sample. A month with a different part mix could reorder categories 3–9. Categories 1–2 and the leakage findings are structural and should hold.
- **682 positive examples.** `machine_type` has 175 levels against those 682 positives. The OOF AUC of 0.837 is honest for *this* sample, but the encoding will need heavy regularisation, and unseen machine types at inference are a live risk.
- **Forward selection is greedy**, so it finds a good set, not the optimal one. It also cannot see interactions that only pay off in pairs.
- **No sklearn.** Gradient boosting would likely rank interactions differently, particularly for the magnitude model. Worth re-running §4 with `lightgbm` once package access is resolved.
- **`sfm_criticality` was selected 4th but is 76.4% blank.** Per §8 its contribution is largely the missingness flag. It is listed under record-completeness for that reason, not as a criticality signal — [Phase 0 §5 (Q6)](Analysis_Phase0_TCB_Jan26.md) established it is a demand tier, not machine criticality.
- **Nothing here is causal.** These are associations in a single completed review cycle.

---

## 12. Next Steps

1. **Extract `uzb_doi_prefix` before the §7.4 normalisation lands** — otherwise an 86%-vs-13% separator disappears silently. Highest-urgency item, because the ingestion fix is already specified.
2. **Add `clt_is_default_25`** to ingestion, and revisit the `long_lead_time_threshold = 30` proposal in light of §7.1.
3. **Confirm `qry_msbidatadays_since_effective` with the WINGS owner** — business date or load date decides whether it survives into production.
4. **Confirm `replenishment_policy` is set upstream of the review**, not by the engineer during it. It is the cleanest feature in the file, and this is the one thing that would disqualify it.
5. **Build the ROP/Min derivation rule** (§3.1) rather than two more models.
6. **Text-mine `comments` / `justification` as *labels*, not features** — [Phase 0 §9.3](Analysis_Phase0_TCB_Jan26.md) already flags this as the highest-value analysis. Their 0.987 AUC is exactly why: they contain the reasoning the model is trying to reconstruct.
