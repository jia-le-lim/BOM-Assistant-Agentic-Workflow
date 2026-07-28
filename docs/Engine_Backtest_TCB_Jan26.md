# Rule Engine Backtest — TCB, Jan'26

**Rule-based MVP engine scored against the decisions the engineer actually made**

| | |
|---|---|
| Document type | Backtest / model evaluation report |
| Analyst | LIM |
| Date | 28 July 2026 |
| Engine | `analysis/engine/engine.py`, `rule_version` **0.2.0-tcb**, `model_version` `rules-only` |
| Scope | `module = TCB`, 2,780 rows |
| Companion | [Analysis_Phase0_TCB_Jan26.md](Analysis_Phase0_TCB_Jan26.md) |
| Reproduce | `python s6_backtest.py` / `s7_sensitivity.py` |

---

## 1. Verdict First

**Deploy it as a value-protection triage layer. Do not deploy it as a recommendation engine.**

| What it does | Result | Deployable? |
|---|---|---|
| Protects value from being wrongly auto-cleared | **96.9%** of value correctly routed | ✅ Yes |
| Catches the `recom = 0` trap | **85.6%** of trap value, 68.1% of trap rows | ✅ Yes |
| Reduces engineer workload | **61.5%** of roster auto-cleared | ✅ Yes |
| Explains every decision (reason code + text) | 100% coverage, deterministic | ✅ Yes |
| Picks the *right rows* to review (row-level) | precision **0.221**, recall 0.565 | ⚠️ Weak |
| Predicts the right quantity | 77.7% exact — **worse than doing nothing** | ❌ No |

The honest headline: **on quantity prediction, the engine loses to a trivial baseline**, and that finding should shape Phase 1 more than any tuning result.

---

## 2. The Baseline That Matters

Because the engineer changed only 15.0% of rows, "predict no change" is a very strong trivial baseline. Measured on the same data:

| Strategy | Quantity exact match | MAE | Action agreement |
|---|---:|---:|---:|
| **Do nothing (keep current values)** | **85.1%** | 0.61 | **85.1%** |
| Follow `atm_recommended_max` | 82.6% | **0.40** | 83.1% |
| Follow `sfm_brr_max` | 79.4% | 0.46 | 80.3% |
| **Engine v0.2.0** | 77.7% | 0.60 | 80.0% |
| Follow `recom_max` | 75.7% | 0.69 | 76.7% |

**Doing nothing beats every algorithm on accuracy.** This is class imbalance, not a defect — but it means:

1. Any future ML model must be benchmarked against *do-nothing*, not against 50%. PRD §13's "agreement rate with engineer final decision" will read ~85% for a model that does nothing at all.
2. The engine scores *below* do-nothing **by design** — it applies a protective floor to the trap population, deliberately trading quantity accuracy for stockout protection. That trade is the point, but it must be stated, not hidden behind an accuracy number.
3. **Quantity output must stay advisory in MVP.** The engineer sets the number; the engine flags and explains.

---

## 3. Task A — Triage (which rows need a human?)

Label: engineer changed Max, ROP or Min. 418 of 2,780 rows (15.0%).

| Metric | Value |
|---|---:|
| Flagged for review | 1,070 (38.5%) |
| Auto-cleared | 1,710 (61.5%) |
| TP / FP / FN / TN | 236 / 834 / 182 / 1,528 |
| Precision | **0.221** |
| Recall | **0.565** |
| Lift over random | 1.47× |
| **Auto-clear miss rate (rows)** | **182 of 1,710 — 10.6%** |
| **Auto-clear miss rate (value)** | **$53,029 of $1,686,319 — 3.1%** |

The row/value gap is the whole story. **10.6% of auto-cleared rows were wrong, but they carried only 3.1% of the money.** The auto-clear guard (never clear anything with `unitprice ≥ $1,500`, `lead time ≥ 60d`, or exposure `≥ $1,000`) is what produces that gap — see §5.

Precision of 0.221 means engineers see roughly 4 items for every 1 that genuinely needed changing. That is a real cost, and §6 explains why it cannot currently be improved.

---

## 4. Task B — Catching the `recom = 0` Trap

Population: 2,630 rows where `recom_max = 0`. Base rate of engineer overriding upward: 24.0%.

| Metric | Value |
|---|---:|
| Rule 9 fired on | 681 rows |
| Real traps caught | **430 of 631 (68.1%)** |
| **Value protected** | **$1,111,321 of $1,298,667 (85.6%)** |
| Rule 9 precision | 0.631 |
| Traps missed | 201 rows ($187,346) |

Profile of the 201 misses: **every one is `aging_status = Dead`, with zero 547-day usage, median lead time 25 days, median price $141.** They are genuinely indistinguishable from the ~2,000 dormant parts the engineer left at zero — no input feature separates them. Catching them needs data the file does not contain (machine linkage, tool status, planned maintenance).

### What predicts a real trap

Measured within the `recom = 0` population:

| Signal | Fires | Precision | Recall |
|---|---:|---:|---:|
| `atm_recommended_max > 0` | 199 | **0.990** | 0.312 |
| `max_qty > 0` (stocked today) | 325 | **0.963** | 0.496 |
| `sfm_brr_max > 0` | 153 | 0.889 | 0.216 |
| `replenishment_policy = Order To Max` | 360 | 0.878 | 0.501 |
| any 547-day usage | 152 | 0.849 | 0.204 |
| `aging_status ≠ Dead` | 247 | 0.830 | 0.325 |
| `contractual_lead_time ≥ 45` | 604 | 0.452 | 0.433 |
| `avail_qty > 0` | 1,061 | 0.375 | 0.631 |

**The single most useful rule is the simplest: if a part is stocked today and `recom` says zero, do not zero it.** That alone is 96.3% precise. `recom` proposed zeroing 325 currently-stocked parts; the engineer refused on 313 of them.

---

## 5. Operating-Point Frontier

Two dials were swept. `review_pct` is the share sent to an engineer; `miss_rate` is the share of *auto-cleared* rows that were actually changed.

| Rule 9 mode | Guard | Review % | Miss rate (rows) | **Miss (value)** | Trap catch | **Trap value** | R9 precision | Action agree |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| tight | off | 25.5 | 10.8% | 12.3% | 56.4% | 73.4% | 0.925 | 80.6% |
| tight | **on** | 37.8 | 10.8% | **3.2%** | 56.4% | 73.4% | 0.925 | 80.6% |
| balanced | off | 26.4 | 10.7% | 12.1% | 61.5% | 77.1% | 0.824 | 80.0% |
| balanced | **on** | 38.5 | 10.6% | **3.1%** | 61.5% | 77.1% | 0.824 | 80.0% |
| **wide** | off | 26.4 | 10.7% | 12.1% | 68.1% | 85.6% | 0.631 | 80.0% |
| **wide** | **on** ← selected | **38.5** | **10.6%** | **3.1%** | **68.1%** | **85.6%** | 0.631 | 80.0% |
| widest | on | 58.4 | 11.2% | 2.1% | 78.0% | 90.4% | 0.378 | 62.3% |

Two clean conclusions:

1. **`wide` strictly dominates `balanced` and `tight`** — identical review volume, identical miss rate, identical action agreement, but 12pp more trap rows and 8pp more trap value caught. Free improvement, because those extra rows were already being flagged by other rules; `wide` simply labels them with the correct reason code and applies the protective floor.
2. **The auto-clear guard is the single best change available** — it cuts value wrongly auto-cleared from 12.1% to 3.1%, a **4× reduction**, for +12pp review volume. It barely changes the row-level miss rate, which is exactly the point: it protects money, not row counts.

`widest` is rejected: R9 precision collapses to 0.378 and action agreement falls to 62.3% (it over-stocks broadly).

### Threshold sensitivity

`long_lead_time_threshold` is the dominant dial; `high_cost_threshold` barely matters.

| `long_lt` | Review % (@ high_cost $1.5k) | Miss rate |
|---:|---:|---:|
| 30 | 47.2% | **7.6%** |
| 45 | 43.6% | 8.6% |
| **60** (selected) | **38.5%** | 10.7% |
| 90 | 34.5% | 11.2% |

This is the workload/safety dial and it is **an engineering decision, not a statistical one**. Moving from 60 → 30 days buys a 3pp lower miss rate for 9pp more review work. Recommend engineers choose it explicitly.

### Open Question 2 is low-risk

| `sfm_source` | Review % | Miss rate | `SFM_DISAGREEMENT` fires | Action agree |
|---|---:|---:|---:|---:|
| `sfm_brr` (default) | 38.5% | 10.6% | 365 | 80.0% |
| `atm_recommended` | 38.5% | 10.7% | 237 | 80.0% |
| `sfm` | 45.6% | 9.0% | 521 | 75.1% |

Choosing between `sfm_brr` and `atm_recommended` changes essentially nothing. **The open question can be resolved later without rework** — only picking the sparse `sfm_max` (23.6% filled) would materially shift behaviour.

---

## 6. Why Row-Level Precision Is Stuck At ~0.22

This is not a tuning failure. An independent predictor built purely from candidate-source disagreement (`any candidate ≠ current`, plus the same value guard) lands in the same place: **37.2% review, 10.8% miss rate, 3.3% value miss** — statistically indistinguishable from the engine's 38.5% / 10.6% / 3.1%.

Two different approaches hitting the same ceiling means the ceiling is in **the data, not the rules**:

- `frequencymonthswithusage` — 89.8% imputed
- `days_since_last_issue` — 65.4% imputed
- `sfm_criticality` — 76.4% blank, and not machine criticality anyway
- No machine linkage, no tool status, no planned-maintenance signal

The engineer is using knowledge that is **not in the file** — which machine a part serves, whether that tool is still running, what is scheduled. Until that is captured, no rule set and no ML model will materially beat precision 0.22.

**Implication for Phase 4:** more modelling will not fix this. Better features will. The highest-value work is capturing machine linkage and mining the 100%-populated `justification` / `comments` fields, not training a model on Jan'26.

---

## 7. Engineering Verification

| Check | Result |
|---|---|
| **Leakage-free** — output byte-identical when all PRD §5.1 output/memory columns are physically removed from the input | ✅ `True` |
| Forbidden columns hard-dropped at entry | `factory_recommended_new_max/rop/min`, `justification`, `comments`, `review_acknowledge`, `*_adoption`, `modified_user`, `modified_date` |
| **Deterministic** (PRD §10) — two runs identical | ✅ `True` |
| Config-driven thresholds (PRD §6.2) | ✅ `rule_config.json`, no hard-coded values |
| Every row carries reason code + explanation (PRD §10) | ✅ 100% |
| Criticality config keyed on `machine_type`, empty until engineers populate | ✅ R3 correctly inert |

Sample output:

```
item 500699374 | max 1 -> 5 | review=Y risk=High conf=0.80
  codes: LONG_LEAD_TIME_RISK, ZERO_RECOMMENDATION_OVERRIDE, SFM_DISAGREEMENT
  why  : Source algorithm recommends 0 but the part carries risk (lead time 83d,
         on-hand 0); protective stock proposed instead of zero.
```

### Reason codes fired

| Code | Rows | % |
|---|---:|---:|
| `NO_RECENT_USAGE` | 2,555 | 91.9% |
| `IMPUTED_USAGE_FEATURES` | 2,495 | 89.7% |
| `ABANDONED_TOOL` | 1,386 | 49.9% |
| **`ZERO_RECOMMENDATION_OVERRIDE`** | **681** | **24.5%** |
| `SFM_DISAGREEMENT` | 365 | 13.1% |
| `AD_HOC_CONSUMPTION_RISK` | 349 | 12.6% |
| `LONG_LEAD_TIME_RISK` | 91 | 3.3% |
| `INSUFFICIENT_DATA` | 86 | 3.1% |
| `MAX_CHANGE_EXCEEDS_THRESHOLD` | 47 | 1.7% |
| `HIGH_COST_INCREASE_REVIEW` | 40 | 1.4% |
| `DATA_QUALITY_REVIEW_REQUIRED` | 14 | 0.5% |
| `LONG_LEAD_TIME_ZERO_STOCK` | 12 | 0.4% |
| `LOW_COST_RECURRING_USAGE` | 11 | 0.4% |
| `HIGH_USAGE_TOOL` | 8 | 0.3% |

`NO_RECENT_USAGE` and `ABANDONED_TOOL` fire on 92% and 50% of the roster respectively. They are emitted as **informational codes only** and deliberately do not force a review — as written in PRD §6.2 they would swamp the queue. Both need better source data before they can gate anything.

---

## 8. Limitations

- **Single cycle, single reviewer.** Every metric is fitted to one month reviewed almost entirely by one person. Thresholds in §5 are not validated out-of-sample. Treat the frontier as indicative until a second cycle exists.
- **`recom_max` is an engine input.** The trap rule depends on a column whose provenance is unknown. If `recom` changes or is retired, Rule 9 must be re-derived.
- **Rule 3 is untested.** `machine_criticality` config is empty, so `CRITICAL_MACHINE_PROTECTION` never fired. It cannot be evaluated until engineers populate it.
- **The label is imperfect.** "Engineer changed something" is a proxy for "needed review". An engineer may have reviewed a row carefully and correctly left it unchanged — that counts as a false positive here, which understates precision by an unknown amount.
- **Quantity metrics are advisory.** Given §2, do not report the 77.7% figure without the do-nothing baseline beside it.

---

## 9. Recommendation

**Ship `rule_version 0.2.0-tcb` as a triage-and-explain layer for Phase 1**, with the quantity column presented as a suggestion the engineer overwrites, never as a pre-filled answer.

Then, in priority order:

1. **Have engineers choose `long_lead_time_threshold`** (30/45/60/90) — it is the workload/safety dial and it is their call, not a statistical one. §5.
2. **Capture machine linkage and tool status.** This is the binding constraint on every metric in this report. §6.
3. **Mine `justification` / `comments`** — 100% populated, and the only place the engineer's real reasoning currently exists.
4. **Populate `machine_criticality_config`** so Rule 3 can be evaluated at all.
5. **Log every engineer accept/override against `rule_version`** so the next cycle produces out-of-sample validation for free.
6. Re-derive Rule 9 if `recom` provenance changes.
