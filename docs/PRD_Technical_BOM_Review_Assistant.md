# Technical PRD — BOM Review Assistant

**Intelligent Recommendation & Review System for WINGS Stocking Parameter Updates**

| | |
|---|---|
| Document type | Technical Product Requirements Document (full) |
| Version | 1.0 (draft) |
| Owner | LIM |
| Audience | Engineering, Data, IT/Security, Solution Architecture |
| Status | For internal review |
| Date | 27 July 2026 |

---

## 1. Purpose & Background

Engineers on the TCB line spend heavy manual effort reviewing spare-part stocking parameters (Max / ROP / Min) before updating the WINGS stocking system. The existing SFM algorithm produces recommendations that are not sufficiently context-aware. Two failure modes recur:

- It recommends **decreasing low-cost but operationally necessary items** (e.g. coupons), where real recurring need still exists.
- It does not consistently **prioritise parts used by critical machines** (e.g. TCB, which is a process bottleneck relative to faster machines like APPJ).

The result is heavy manual analysis, inconsistent decisions, and operational disturbance when a critical machine needs a critical part.

This document specifies a **BOM Review Decision Support System** — not "just a chatbot." The core is a deterministic, explainable recommendation engine plus a human-in-the-loop review workflow. A chatbot (via NYRA) is an assistant layer that explains and retrieves, but never decides stocking values or writes to WINGS.

Guiding principle throughout:

> **The engine calculates. NYRA explains. The engineer approves. WINGS updates only after approval.**

---

## 2. Goals & Non-Goals

### 2.1 Goals

1. Generate explainable factory recommendations for `new_max`, `new_rop`, `new_min` and a `review_required` flag.
2. Reduce manual review time and let low-risk items be cleared quickly.
3. Attach a reason code, risk level, and human-readable explanation to every recommendation.
4. Capture engineer decisions (accept / reject / override + comment) as structured history for audit and future ML.
5. Keep human approval mandatory for high-risk, high-cost, critical-machine, or low-confidence items.
6. Provide a foundation that can later add ML without re-architecting.

### 2.2 Non-Goals (early phases)

- Fully autonomous WINGS update with no human approval.
- LLM/chatbot generating stocking values by itself.
- Real-time production-scheduling optimisation.
- Pure ML model as the primary decision mechanism at launch.

### 2.3 MVP scope decision

**MVP targets the TCB module first**, then scales to other modules once the rules, thresholds, and workflow are validated. This keeps the critical-machine logic concrete and the initial dataset manageable.

---

## 3. Users & Roles

| Role | Responsibility | Permissions |
|---|---|---|
| TCB / Module engineer | Review items, accept/reject/override, comment | Review + comment |
| Senior engineer | Approve overrides and high-risk changes | Approve |
| Inventory planner / SCM | View cost & procurement detail, approve certain classes | View cost + approve |
| Admin | Manage thresholds, rules, criticality config | Configure |
| IT | Manage integration, deployment, secrets | Integration |
| Auditor | Read-only access to history and audit log | View history |

Access is enforced by role-based access control tied to Intel SSO / Entra ID.

---

## 4. System Architecture

### 4.1 High-level flow

```text
Internal WINGS / SFM / BOM review workbook (CSV/Excel)
        |
        v
Data Ingestion Layer  (Python ETL / Power Automate / scheduled job)
        |
        v
SQL Database / Data Mart  (source of truth)
        |
        v
Recommendation Engine API (FastAPI)
  Data validation -> Business rules -> Inventory formula
  -> SFM/candidate comparison -> (ML later) -> Risk & review logic
        |
        +-----------------------------+
        |                             |
        v                             v
Power Apps Review UI            NYRA Chatbot / RAG
Approve / reject / override      Explain, search history, SOP Q&A
        |
        v
Power BI dashboard  +  Audit log
        |
        v
Approved export -> WINGS update file (Phase 1) / API (later phase)
```

### 4.2 Component responsibilities

- **Ingestion**: load workbook/CSV → validate → persist into SQL. Later, direct connectors to WINGS/SFM/procurement.
- **SQL data mart**: structured source of truth for item master, inventory, consumption, recommendations, review history, config, and audit.
- **Recommendation engine (FastAPI)**: all decision logic. Stateless service; deterministic given inputs + rule/model version.
- **Power Apps**: engineer-facing review, approval, override, comment, and admin configuration.
- **Power BI**: KPI and monitoring dashboards, embedded in Power Apps.
- **Power Automate**: notifications, approval routing, scheduled ingestion/export jobs.
- **NYRA chatbot**: RAG-based explanation and retrieval only; calls backend APIs, never writes.

### 4.3 Deployment model (Intel-internal)

Keep the chatbot in NYRA as the interface layer, but host the engine, database, rules, ML, and audit workflow as separate internal services. NYRA is not the calculation engine and is not the system of record.

---

## 5. Data Model

The current BOM review workbook (Kulim stockroom) already contains most required fields. It is closer to an **inventory review decision dataset** than a plain BOM list.

### 5.1 Column classification

Correctly separating columns is essential — several existing columns are results of prior review and must **not** be used as ML inputs (target leakage).

**Core input features (safe for rules, formulas, ML):**

- Item identity: `item_id`, `item_desc`, `stockroom_id`, `stockroom_name`, `supplier_name`
- Machine/module context: `machine_type`, `module`, `new_module`, `site`, `factory`
- Current stock setting: `max_qty`, `rop_qty`, `min_qty`
- Current inventory: `avail_qty`, `vf_avail_qty`, `consignment_qty`, `open_po_qty`, `qry_eoh_excess_qty`
- Consumption: `last_547_day_cnsmptn_qty`, `last_365_day_cnsmptn_qty`, `last_180_day_cnsmptn_qty`, `last_90_day_cnsmptn_qty`, `last_30_day_cnsmptn_qty`, `last_5_day_cnsmptn_qty`
- Usage recency/frequency: `days_since_last_issue`, `frequencymonthswithusage`, `partfreq`, `previous_part_freq`
- Cost: `unitprice`, `spending_impact`
- Lead time: `contractual_lead_time`, `new_clt_change_type`
- Procurement: `repair_type`, `replenishment_policy`, `order_qty_multiple`, `purchasing_group_name`, `ownership`
- Classification: `gl_group`, `category_type`, `functional_group`, `sensitivity_tag`, `aging_status`, `aging_new_flag`
- Sharing/excess: `shared_parts`, `shareable_indicator`, `excess_status_tf`, `other_stkrm_cons`, `other_stkrm_30d/90d/180d/365d_cons_qty`
- Operational flags: `alternative_part`, `with_repeat_uzb`, `with_uzb_case_last_8_weeks`, `ind_sda`, `psi`, `sims`

**Candidate recommendation signals (use as inputs, not as truth):** `sfm_recommendation`, `sfm_max/rop/min`, `atm_recommended_*`, `ds_recommended_*`, `dp_recommended_*`, `sims_recommended_*`, `one_msia_*`, `sfm_brr_max/rop/min`. Treat `recom_max/rop/min` as needing clarification (final vs pre-review).

**Output / target columns:** `factory_recommended_new_max/rop/min`, `review_required`, plus new fields below. These must be treated as engine output, never as ML input.

**Memory / decision-history columns (do NOT use as ML input; store as structured history):** `justification`, `comments`, `review_acknowledge`, `rop_adoption`, `max_adoption`, `ooq_adoption`, `modified_user`, `modified_date`.

**Sensitive columns (security review required):** `unitprice`, `supplier_name`, `machine_type`, `factory`/`site`, `stockroom`, `item_gl_account`, `spending_impact`, engineer `comments`.

### 5.2 Engine output schema

```text
factory_recommended_new_max
factory_recommended_new_rop
factory_recommended_new_min
review_required                 (Y/N)
factory_recommendation_action   (Increase / Maintain / Decrease)
reason_code                     (one or more, e.g. LOW_COST_RECURRING_USAGE)
risk_level                      (Low / Medium / High)
confidence_score                (0-1)
explanation                     (human-readable)
model_version
rule_version
```

Example row:

| Field | Example |
|---|---|
| factory_recommended_new_max | 10 |
| factory_recommended_new_rop | 4 |
| factory_recommended_new_min | 2 |
| review_required | Y |
| factory_recommendation_action | Maintain |
| reason_code | LOW_COST_RECURRING_USAGE, SFM_DISAGREEMENT |
| risk_level | Medium |
| confidence_score | 0.82 |
| explanation | SFM recommends decrease, but item has recent recurring usage and low cost; factory recommendation is to maintain. |

### 5.3 Database tables

```text
item_master
inventory_snapshot
consumption_snapshot
recommendation_result
review_history
machine_criticality_config
rule_config
model_prediction_log
audit_log
```

**review_history** (the true long-term memory — a table, not chatbot memory):

```text
review_id, item_id, stockroom_id, review_date, reviewer,
current_max, current_rop, current_min,
sfm_recommendation,
factory_recommended_new_max, factory_recommended_new_rop, factory_recommended_new_min,
engineer_final_max, engineer_final_rop, engineer_final_min,
adoption_status, comments, justification,
rule_version, model_version
```

**machine_criticality_config** (engineer-approved, since criticality is not fully in the source data):

```text
machine, module, criticality_score, service_level_target
TCB,  TCB,  High,        98%
APPJ, APPJ, Medium/Low,  90%
```

---

## 6. Recommendation Engine

A **hybrid, layered** engine. Do not start with pure XGBoost — spare-part demand is sparse and intermittent, historical decisions carry human bias, and rules encode operational knowledge that is not visible in the data.

```text
Layer 1: Data validation
Layer 2: Business rules
Layer 3: Inventory calculation
Layer 4: Recommendation-source comparison (SFM / ATM / DS / DP / SIMS)
Layer 5: ML prediction (later phase)
Layer 6: Risk scoring + review Y/N
Layer 7: Human review + feedback capture
```

### 6.1 Layer 1 — Data validation

```text
item_id not null; unitprice valid; contractual_lead_time valid
max_qty >= rop_qty >= min_qty
consumption values not negative; open_po_qty not negative
order_qty_multiple valid
```

On failure: `review_required = Y`, `reason_code = DATA_QUALITY_REVIEW_REQUIRED` (or `INVALID_CURRENT_STOCKING_LEVEL` when `max_qty < rop_qty`).

### 6.2 Layer 2 — Business rules (initial set)

| # | Condition | Action | Reason code |
|---|---|---|---|
| 1 | `unitprice <= low_cost_threshold` AND `frequencymonthswithusage >= min_usage_months` AND `days_since_last_issue <= recent_usage_days` | Do not decrease aggressively | `LOW_COST_RECURRING_USAGE` |
| 2 | `unitprice >= high_cost_threshold` AND action = Increase | `review_required = Y` | `HIGH_COST_INCREASE_REVIEW` |
| 3 | machine/module is High criticality AND SFM = Decrease | `review_required = Y` | `CRITICAL_MACHINE_PROTECTION` |
| 4 | `contractual_lead_time >= long_lead_time_threshold` AND recent usage exists | Protect ROP or require review | `LONG_LEAD_TIME_RISK` |
| 5 | `last_365_day_cnsmptn_qty = 0` AND `days_since_last_issue > 365` AND not critical | Candidate for decrease/review | `NO_RECENT_USAGE` |
| 6 | Part linked to abandoned/obsolete tool | Prevent increase, confirm decrease | `ABANDONED_TOOL` |
| 7 | Highly used tool | Protect stock, avoid decrease | `HIGH_USAGE_TOOL` |
| 8 | New tool / insufficient history | Require manual review | `INSUFFICIENT_DATA` |

All thresholds (`low_cost_threshold`, `high_cost_threshold`, `long_lead_time_threshold`, `recent_usage_days`, `min_usage_months`) live in `rule_config` and are admin-configurable, not hard-coded.

### 6.3 Layer 3 — Inventory calculation

```text
ROP = expected demand during lead time + safety stock
safety stock = Z(service_level) x demand_variability x lead_time_factor
```

- Service level is driven by machine criticality (e.g. 98% critical, 90% non-critical).
- Contextual lead time: `adjusted_lead_time = max(contractual_lead_time, historical P75/P90 lead time)`; use a more conservative percentile for critical items.
- Adjust for MOQ / `order_qty_multiple` / pack size after computing raw values.

### 6.4 Layer 4 — Candidate comparison

Treat SFM/ATM/DS/DP/SIMS/One-Malaysia as candidate sources. The factory engine selects/adjusts using rules + inventory logic, and records disagreement (`SFM_DISAGREEMENT`) as a signal and a review trigger.

### 6.5 Layer 5 — ML (later phase, see §9)

### 6.6 Layer 6 — Review Y/N logic

`Review = Y` when: high-cost + increase; SFM and factory disagree; critical-machine part below ROP; insufficient/first-time data; highly variable lead time; repeated prior overrides; abandoned-tool linkage; recommended change exceeds threshold (e.g. Max increase > 50%); data-quality failure.

`Review = N` when: low-cost, stable demand, action = Maintain, SFM and engine agree, strong historical confidence, no recent emergency consumption.

### 6.7 Layer 7 — Feedback capture

Every engineer action writes to `review_history`: accepted vs overridden, final values, reason/comment, machine/module, emergency flag, and (later) whether a stockout or overstock followed. This is the ML training substrate.

---

## 7. API Specification (FastAPI)

```text
POST /upload-bom-file        Upload CSV/Excel; validate; load to SQL
POST /run-recommendation     Run engine over a batch; persist results
GET  /recommendations        List recommendations (filter + paginate)
GET  /recommendations/{item_id}
POST /review/{item_id}       Submit accept/reject/override + comment
GET  /history/{item_id}      Return review history for an item
POST /chat                   NYRA/RAG entrypoint (read-only tools)
GET  /config/rules           Read rule thresholds
POST /config/rules           Update rule thresholds (admin only)
GET  /export/wings           Generate approved WINGS update file
```

Design notes: filtering/pagination happen in SQL/API (not Power Apps) for performance; every write is authenticated and audit-logged; `/chat` exposes only read/retrieval tools to the LLM.

---

## 8. Chatbot / RAG (NYRA)

Permitted uses: explain why a recommendation was made; search previous engineer comments; answer SOP/policy questions via RAG; summarise review batches; help locate items (e.g. "TCB parts with long lead time and low stock").

Hard controls: RAG answers only from approved documents with source citation; respond "I don't know" when no source exists; the LLM never generates Min/Max/ROP, never writes to WINGS, never overrides rules; all interactions logged; guard against prompt injection from uploaded documents.

---

## 9. Machine Learning (Phase 4)

Introduce ML only after enough clean, labelled review history exists.

| Target | Minimum useful | Better |
|---|---|---|
| Predict `review_required` Y/N | 1,000–3,000 rows | 10,000+ |
| Predict adoption (accept/reject SFM) | 2,000–5,000 | 20,000+ |
| Predict Increase/Maintain/Decrease | 3,000–10,000 | 30,000+ |
| Predict exact new Max/ROP/Min | 10,000+ | 50,000+ |

Candidate models: logistic regression (interpretable baseline), Random Forest, XGBoost/LightGBM for tabular targets; time-series for demand forecasting where history is rich. Requirements: explainability (SHAP), confidence scoring, **no target leakage** (exclude all §5.1 output/memory columns), and a rules fallback when confidence is low. Track experiments and versions in MLflow.

---

## 10. Non-Functional Requirements

- **Explainability**: every recommendation carries reason code(s) + explanation. Non-negotiable.
- **Auditability**: store input data used, recommendation, rule/model version, engineer decision, comment, timestamp, approval chain, and final WINGS update per item.
- **Determinism**: given the same inputs + rule/model version, the engine returns the same result.
- **Performance**: sub-second engine response per item; batch runs paginated; Power Apps queries server-side filtered.
- **Security**: Intel SSO / Entra ID auth; RBAC; encryption in transit and at rest; data masking for sensitive fields; no autonomous WINGS write.
- **Model governance** (when ML is live): validation before deployment, accuracy/drift monitoring, retraining approval, rollback, version control.

---

## 11. WINGS Integration Strategy (phased, safety-first)

```text
Phase 1: Recommendation only — no automatic WINGS update.
Phase 2: Generate a WINGS upload file after approval.
Phase 3: Controlled system-to-system update after approval, with full audit + rollback.
```

Open questions for IT: does WINGS expose an API, DB access, or file import only? Who may update WINGS? Are there transaction locks or approval flows?

---

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Poor data quality | Wrong recommendations | Validation layer + data-quality flags |
| Over-trusting chatbot | Wrong operational decision | Chatbot explains only; engine calculates |
| Missing criticality data | Weak prioritisation | Engineer-approved criticality config table |
| ML inaccuracy | Low trust | Rules first; ML added later with fallback |
| Target leakage | False high accuracy | Strictly separate input vs output/memory columns |
| Power Apps performance | Slow UI | Filter/paginate in API/SQL |
| WINGS integration limits | Manual update remains | Export file first, integrate later |
| Security restrictions | Deployment delay | Confirm NYRA/cloud/data approval early |
| Low adoption | Engineers keep manual process | Explainable output + easy override |

---

## 13. Success Metrics

**Efficiency**: manual review time reduction; items reviewed per engineer per day; % low-risk items auto-cleared; backlog reduction.

**Quality**: engineer acceptance rate; override rate; reduction in false-decrease recommendations; reduction in stockout/emergency cases; reduction in excess inventory.

**System**: recommendation generation time; API response time; data-validation failure rate; Power Apps & chatbot usage; audit completeness.

**Model (ML phase)**: precision/recall for review-required; MAE/RMSE for quantity; agreement rate with engineer final decision; demand drift; confidence calibration.

---

## 14. Phased Implementation Plan

| Phase | Focus | Duration | Key deliverables |
|---|---|---|---|
| 0 | Discovery & data validation | 2–3 wks | Data dictionary, process map, MVP scope, security/platform feasibility, initial rule list |
| 1 | Rule-based MVP (TCB) | 4–6 wks | SQL schema, ingestion, FastAPI engine, validation + rules, Max/ROP/Min + Review Y/N + reason code, Power Apps review screen, review history, WINGS export file |
| 2 | Dashboard & workflow | 3–4 wks | Power BI KPIs, Power Automate notifications, approval routing, config screen, better filtering |
| 3 | NYRA / chatbot | 3–5 wks | RAG over SOP + review history, recommendation explanation, comment lookup, item Q&A |
| 4 | ML enhancement | Data-dependent | Review-required & adoption models, confidence scoring, MLflow tracking, monitoring |

---

## 15. Open Questions / Decisions Needed

1. MVP = TCB only first (assumed), then scale — confirm.
2. Is NYRA approved for this data sensitivity level?
3. Who owns final approval before WINGS update?
4. First version: export file only, or attempt direct WINGS integration?
5. Threshold values: low-cost, high-cost, long lead time, recent usage, high-risk.
6. Which machines/modules are classified as critical?
7. Can engineer comments and adoption decisions be stored for ML training?
8. What is the authoritative source of truth (workbook vs WINGS vs SFM)?
9. Clarify whether `recom_max/rop/min` is final or pre-review.

---

## 16. Next Steps

Run a short discovery phase before development: review this PRD with supervisor + engineers, confirm MVP scope (TCB first), validate workbook data quality, confirm IT/security constraints for Power Apps + FastAPI + SQL + NYRA, define initial rule thresholds with engineers, and build a small prototype on sample data.
