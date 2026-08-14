# Technical PRD v2 — BOM Review Assistant (Spare-Parts Demand & Stocking Engine)

**Intelligent Recommendation & Review System for WINGS Stocking Parameter Updates**

| | |
|---|---|
| Document type | Technical Product Requirements Document (full) |
| Version | 2.0 (draft) |
| Supersedes | [PRD_Technical_BOM_Review_Assistant.md](PRD_Technical_BOM_Review_Assistant.md) (v1.0) |
| Owner | LIM |
| Audience | Engineering, Data, IT/Security, Solution Architecture |
| Status | For internal review |
| Date | 14 August 2026 |
| Companion analysis | [Analysis_Phase0_TCB_Jan26.md](Analysis_Phase0_TCB_Jan26.md), [log/2026-08-13_Multi-Month_TCB_Extraction.md](log/2026-08-13_Multi-Month_TCB_Extraction.md) |

---

## 0. What changed since v1.0

v1 specified a deterministic rule engine + human-in-the-loop review + explainer chatbot (NYRA), with ML deferred to Phase 4. Multi-month data work has since reframed the analytical core:

1. **This is a spare-parts / intermittent-demand problem, not smooth-demand forecasting.** 92% of TCB items show zero 90-day consumption in every snapshot; only ~213 of 3,060 move. Classical time-series methods (ARIMA/ETS/Prophet/deep learning) do not apply.
2. **The engine's numeric core becomes intermittent-demand estimation + inventory theory**, replacing v1 §6.3's placeholder inventory math.
3. **A multi-month panel now exists** ([analysis/output/s10_tcb_panel_long.csv](../analysis/output/s10_tcb_panel_long.csv)) enabling per-item demand history, trend, and cumulative consumption.
4. **Two-regime output**: statistical forecast for moving parts + hard-rule insurance policy for critical slow-movers (the "recom = 0 trap" from Phase 0, ~$1.3M).
5. **Continuous-learning pipeline** replaces any one-time trained model — severity and estimates are recomputed on every BOM ingest.

The v1 guiding principle is unchanged:

> **The engine calculates. NYRA explains. The engineer approves. WINGS updates only after approval.**

---

## 1. Purpose & Background

Engineers manually review spare-part stocking parameters (Max / ROP / Min) before updating WINGS. The incumbent SFM algorithm is not context-aware and systematically **under-stocks operationally necessary spares** — Phase 0 found 631 rows (77% of approved value, ~$1.3M) where the algorithm proposed zero stock and the engineer overrode upward, usually citing ad-hoc/bulk withdrawal on critical machines.

v2 specifies the analytical engine that produces defensible `new_max / new_rop / new_min` for **intermittent, low-volume, high-criticality spare parts**, grounded in spare-parts inventory theory rather than generic forecasting.

## 2. Goals & Non-Goals

### 2.1 Goals
1. Produce explainable `new_max`, `new_rop`, `new_min` + `review_required` per TCB item, driven by lead-time-demand estimation and service-level policy.
2. Correctly separate **dormant** parts from **active** parts and route each to the right method.
3. Protect critical slow-movers via an explicit insurance-stock policy (fix the recom = 0 trap).
4. Recompute severity and demand estimates on **every** new monthly BOM (self-updating).
5. Score every recommendation against the engineers' historical `factory_recommended_new_*` decisions.
6. Keep human approval mandatory; WINGS writes only post-approval.

### 2.2 Non-Goals
- Autonomous WINGS writes without approval.
- Deep-learning / long-horizon time-series forecasting (data does not support it).
- LLM generating stocking values.
- Cross-module rollout before TCB is validated.

### 2.3 Scope
TCB module first (2,780 items in Jan'26). Methodology generalizes to other modules later.

## 3. Data Foundation

### 3.1 Multi-month panel
Source of truth = [analysis/s10_multi_month_extract.py](../analysis/s10_multi_month_extract.py) outputs:

| Artifact | Grain | Role |
|---|---|---|
| `s10_tcb_panel_long.csv` | item × month | demand history, rates, cumulative — model input |
| `s10_tcb_consumption_trend.csv` | item | per-item trend + cumulative summary |
| `s10_extract_manifest.csv` | file | provenance, duplicate handling |

8 usable snapshots (2024-07 … 2026-01; April'25 dropped as an identical duplicate of March'25), 3,060 items, 9,424 item-months.

### 3.2 Priority features
**Primary:** consumption rate (six trailing windows), `contractual_lead_time`, `machine_type`.
**Secondary:** `unitprice`, `replenishment_policy`, `shared_parts`, `order_qty_multiple`, `days_since_last_issue`, `frequencymonthswithusage`, recency.
**Inventory state (not demand — see §4.7):** `avail_qty`, `vf_avail_qty`, `open_po_qty`, `consignment_qty`, `qry_eoh_excess_qty`, `excess_status_tf`, `avail_doi_maxd`, `other_stkrm_30d/90d/180d/365d_cons_qty`, `shareable_indicator`.

### 3.3 Labels / benchmark
`factory_recommended_new_max/rop/min` (engineer-approved) = ground truth for scoring; `review_acknowledge`, override comments = decision history.

### 3.4 Known data constraints (design must respect)
- **Heterogeneous monthly scope** (140–2,780 TCB rows/month): an absent month = out of review scope, **not** zero demand → treat as missing, never zero-fill.
- **Short, irregular history** (≤8 points, mostly 3, with calendar gaps) → per-series deep methods excluded.

## 4. Analytical Method — Spare-Parts Demand Engine

### 4.1 Part segmentation (computed every ingest)
For each item compute **ADI** (avg interval between non-zero months) and **CV²** (squared CoV of non-zero demand sizes) and assign a quadrant:

| Quadrant | Rule | Routing |
|---|---|---|
| Smooth | ADI<1.32, CV²<0.49 | SBA |
| Erratic | ADI<1.32, CV²≥0.49 | SBA |
| Intermittent | ADI≥1.32, CV²<0.49 | SBA (TSB if decaying) |
| Lumpy | ADI≥1.32, CV²≥0.49 | SBA + bootstrap check |
| Dormant | no non-zero months | Hard-rule policy (§4.4) |
| No-history | <2 snapshots | Feature model (§4.5) |

### 4.2 Point demand estimate — SBA (confirmed)
Primary per-period demand rate = **Syntetos–Boylan Approximation** (bias-corrected Croston), updated recursively each month (auto-learns new consumption). TSB variant used for parts trending to obsolescence so estimates decay correctly.

### 4.3 Lead-time-demand distribution — Negative Binomial (confirmed)
Convert rate to a **distribution of demand over `contractual_lead_time`** using **Negative Binomial** (handles over-dispersion, variance ≫ mean typical of spares). Willemain bootstrap retained as a non-parametric cross-check for lumpy parts.

$$\mu_{LTD} = \hat{d}_{SBA} \times L, \qquad \text{LTD} \sim \text{NegBin}(\mu_{LTD}, \text{dispersion})$$

### 4.4 Inventory parameters → output (confirmed: fill rop/min/max)
Service level is set by **criticality (from `machine_type`, `shared_parts`, `unitprice`)**, not by demand magnitude:

$$\text{new\_rop} = F^{-1}_{LTD}(\text{service level}), \quad \text{new\_min} = \text{safety stock} = \text{new\_rop} - \mu_{LTD}$$
$$\text{new\_max} = \text{new\_rop} + Q \quad (Q \text{ from EOQ or } \texttt{order\_qty\_multiple})$$

### 4.5 Hard-rule policy layer (confirmed: rules + forecast)
Applied over the statistical output; deterministic and explainable:
- **Insurance/keep-alive:** if criticality is high and demand ≈ 0, enforce `min ≥ 1` (base-stock / (S−1,S)) regardless of forecast — directly fixes the recom = 0 trap.
- **Floors/caps** by criticality, shared-part status, price band.
- **No-decrease guards** for low-cost critical consumables with recurring ad-hoc use.
- **Multiple/rounding** to `order_qty_multiple`.

### 4.6 Feature-based model for no-history parts (confirmed)
For items with <2 snapshots (260 single-snapshot + new parts): a **global LightGBM (Tweedie/Poisson objective) with a hurdle (activity classifier → magnitude)** using `machine_type`, `contractual_lead_time`, `unitprice`, `replenishment_policy`, peer/analog demand by `machine_type`. This is the one layer where ML clearly beats SBA (SBA needs history).

### 4.7 Inventory position & network availability (`avail_qty`, `vf_avail_qty`)
Critical distinction: `new_min/rop/max` are **parameters** (target levels from demand + lead time + service level); on-hand stock is **state**. `avail_qty`/`vf_avail_qty` therefore enter the engine in three roles, never as a demand input:

1. **Reorder trigger & validation.** Inventory position = `avail_qty` + `open_po_qty` (+ `consignment_qty`); compared to `new_rop` to flag buy-now and to check current stock is consistent with the new parameters.
2. **Excess / decrease detection.** `avail_qty` vs `new_max`, plus `qry_eoh_excess_qty`, `excess_status_tf`, `avail_doi_maxd` (days of inventory) → drives *decrease* recommendations and `review_required` on over-stock (the excess tail from Phase 0).
3. **Network risk pooling (multi-echelon).** `vf_avail_qty` (+ `shareable_indicator`, `other_stkrm_*_cons_qty`) means stock can be pulled laterally across the virtual factory. High network availability **reduces required local safety stock and insurance stock** (pooling lowers effective stockout risk); a local-only/unique part **raises** them. This modulates the §4.4 service level and the §4.5 keep-alive rule — it does **not** change the SBA demand estimate.

> **Modeling rule:** `avail_qty` / `vf_avail_qty` are **excluded from the demand model's training features**. Observed consumption is *censored* by availability (a stockout forces consumption to 0), so using stock as a feature teaches the model the reverse-causal "low stock → low demand" and biases it toward under-stocking. They are used **decision-side only** (reorder, excess, pooling). The one legitimate data-prep use is **label de-censoring** — flagging/adjusting periods where a stockout truncated true demand. (Exception: a separate *decision-mimicking* / `review_required` classifier may use current stock, since engineers genuinely decide on it.)

This reframes the target as **multi-echelon** (a network of stockrooms), not single-location; lateral-transshipment / METRIC-style pooling is the advanced extension.

## 5. Continuous-Learning Pipeline (answers "can it learn each new BOM?")

Severity and estimates are **recomputed on every ingest**, not trained once:

```
New monthly BOM
   → ingest + validate + TCB filter + append to panel (s10)
   → recompute features (rates, ADI, CV², SBA/TSB, NegBin LTD)   [auto-adaptive]
   → re-segment severity/quadrant                                 [dynamic label]
   → apply hard-rule policy → new_min/rop/max
   → score vs engineer decisions, log drift metrics
   → [scheduled/drift-triggered] refit feature-based ML model
```

- **SBA/TSB/NegBin:** update automatically each period (no retrain).
- **Severity:** dynamic monthly output.
- **Feature ML:** batch retrain on schedule (monthly/quarterly) + drift monitor; not online learning.
- Full reproducibility via the versioned panel + manifest.

## 6. Evaluation & Success Metrics

- **Do NOT use MAPE** (zero-demand division). Use **MASE / RMSSE** vs naive benchmark; SBA is the floor the ML must beat.
- **Inventory-oriented:** achieved fill rate vs target; over-/under-stock $ vs engineer `factory_recommended_new_*`; stockout count on critical machines.
- **Agreement rate** with engineer decisions; reduction in manual review touches (Phase 0: 85% no-op should auto-clear).
- **Insurance coverage:** % of critical near-zero parts correctly kept ≥1 (recom = 0 trap closure).

## 7. Architecture & Integration
Reuse v1 §4 flow and §5.3 tables; replace v1 §6.3 inventory math with §4 here. Engine remains deterministic + explainable; NYRA explains; approvals gate WINGS writes (v1 §11 phased WINGS strategy unchanged).

## 8. Risks & Mitigations
| Risk | Mitigation |
|---|---|
| Too little history for stable estimates | SBA + criticality policy floor; feature model for no-history; widen data over time |
| Under-stocking critical spares | Hard-rule insurance layer; criticality-based service levels |
| Scope heterogeneity distorts trend | Missing≠zero; coverage_ratio filter; manifest provenance |
| Model drift as consumption shifts | Recompute-every-ingest + drift-triggered retrain |
| Over-trust in ML | SBA benchmark floor; human approval mandatory |

## 9. Phased Plan
1. **P1 — Segmentation & baseline:** ADI/CV² quadrants + SBA per item; benchmark vs `factory_recommended_*`.
2. **P2 — LTD + policy:** NegBin lead-time demand → new_min/rop/max + hard-rule insurance layer.
3. **P3 — No-history model:** LightGBM Tweedie hurdle for cold-start parts.
4. **P4 — Pipeline:** wire recompute-on-ingest, drift monitoring, scorecards.
5. **P5 — Workflow/NYRA/WINGS:** surface + explain + approval-gated writes.

## 10. Open Questions
- Source of `machine_type` **criticality** mapping + target service levels per tier (needed for §4.4).
- Replenishment lead-time reliability (`contractual_lead_time` fill/accuracy).
- Downtime-cost inputs to size insurance stock economically.
- Retrain cadence + drift thresholds (monthly vs quarterly).
- **Network pooling policy:** how should `vf_avail_qty` scale local safety/insurance stock, and is lateral transshipment a sanctioned emergency channel (§4.7 multi-echelon)?
