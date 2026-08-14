# ML Component — Implementation Plan (TCB Spare-Parts Demand & Stocking)

| | |
|---|---|
| Document type | Engineering implementation plan |
| Owner | LIM |
| Date | 14 August 2026 |
| Implements | [PRD_Technical_BOM_Review_Assistant_v2.md](PRD_Technical_BOM_Review_Assistant_v2.md) §4–§6 |
| Input data | [analysis/output/s10_tcb_panel_long.csv](../analysis/output/s10_tcb_panel_long.csv) |
| Status | Plan — not yet built |

---

## 1. Objective & scope

Build the ML/forecasting component that turns the multi-month TCB panel into **`new_min` / `new_rop` / `new_max`** per item, using spare-parts intermittent-demand methods, a hard-rule policy layer, and a cold-start model — scored against the engineers' historical `factory_recommended_new_*` decisions.

**In scope:** demand segmentation, SBA/TSB estimation, NegBin lead-time-demand (LTD), inventory-parameter derivation, hard-rule policy, cold-start LightGBM hurdle, backtest harness, retraining pipeline.
**Out of scope (here):** WINGS write-back, NYRA/chatbot, frontend (covered by PRD v2 §7 / v1).

## 2. Design constraints (must hold)
1. **Missing ≠ zero** — absent months are NaN, never zero-filled.
2. **Unit consistency** — SBA rate is per **month** (snapshot cadence); `contractual_lead_time` is in **days**. Convert to a common unit (days) before computing LTD.
3. **avail_qty / vf_avail_qty are decision-side only** — excluded from demand-model training features (censoring/leakage); used for policy + label de-censoring (PRD §4.7).
4. **SBA is the benchmark floor** — any ML must beat SBA or SBA wins.
5. **Short history** — ≤8 irregular snapshots; no deep/long-horizon TS.

## 3. Module layout (extends `analysis/` sN convention)

```
analysis/
  s11_segment.py        # ADI, CV², demand quadrant, activity class
  s12_sba.py            # Croston / SBA / TSB per-item rate estimates
  s13_ltd_policy.py     # NegBin LTD -> min/rop/max + hard-rule policy
  s14_coldstart_ml.py   # LightGBM Tweedie hurdle for no-history parts
  s15_backtest.py       # evaluation vs factory_recommended_* + inventory sim
  s16_pipeline.py       # orchestration + continuous-learning entrypoint
  forecast/
    __init__.py
    estimators.py       # croston/sba/tsb, negbin fit, ppf helpers
    policy.py           # service-level table, keep-alive, caps, rounding
    features.py         # feature builder (demand-only; excludes stock state)
    metrics.py          # MASE, RMSSE, fill-rate, inventory-$ scorers
    config.py           # criticality->service-level map, thresholds, seeds
  output/
    s11_segmentation.csv
    s12_sba_estimates.csv
    s13_recommendations.csv     # new_min/rop/max + reason codes
    s15_backtest_scorecard.csv
```

Serving path (later): fold `forecast/` logic into `backend/app/scoring.py` (PRD v1 §6.5/§9).

## 4. Environment & dependencies
- Python 3.14 (existing interpreter), pandas 3.0, numpy.
- **New:** `statsforecast` (Croston/SBA/TSB/IMAPA/ADIDA), `scipy` (NegBin ppf, fitting), `lightgbm`, `scikit-learn` (metrics, CV), `joblib` (persist models).
- Add to `requirements.txt`; pin versions; verify offline-install availability on Intel-internal env.
- Determinism: fixed seeds in `config.py`.

> **Dependency caveat (verified 2026-08-14):** `statsforecast` 2.1.1 requires **pandas < 3** (it pulls `fugue`/`triad`), so installing it into the workspace `.venv` downgraded pandas 3.0.5 → 2.3.3. The forecasting stack therefore lives in a **pandas-2 environment**, kept separate from any component that requires pandas 3. `statsforecast` 2.x uses a compiled `coreforecast` core (no `numba`), so there is no numpy-version conflict.

## 5. Data contract
**Input:** `s10_tcb_panel_long.csv` (item × month) + latest snapshot's static fields (lead time, machine_type, price, policy, stock state).
**Target:** per-item monthly consumption series (use `last_30_day_cnsmptn_qty` as the monthly bucket; `last_90` for smoothing/cross-check).
**Label/benchmark:** `factory_recommended_new_max/rop/min` from the latest snapshot.

## 6. Phased implementation

### Phase 1 — Segmentation & SBA baseline  (`s11`, `s12`, `forecast/estimators.py`)
- Build per-item monthly demand series from the panel (respect missing≠zero).
- `compute_adi(series)`, `compute_cv2(nonzero_sizes)` → quadrant (Smooth/Erratic/Intermittent/Lumpy) + activity class (Active/Dormant/No-history).
- Implement `croston`, `sba`, `tsb` (via `statsforecast`, wrapped for our irregular cadence).
- Emit `s11_segmentation.csv` and `s12_sba_estimates.csv` (per-item rate, quadrant, method chosen).
- **Deliverable/gate:** distribution of items across quadrants; SBA rate sanity-checked vs `latest_last_365_cons/12`.

### Phase 2 — LTD distribution + inventory params + policy  (`s13`, `forecast/policy.py`)
- Convert SBA monthly rate → per-day; `μ_LTD = rate_day × contractual_lead_time`.
- Fit **Negative Binomial** dispersion `r` (method-of-moments per quadrant; fallback global). `LTD ~ NegBin(μ_LTD, r)`.
- Service level from `machine_type` criticality tier (config table; default 0.99/0.95/0.90) modulated by `vf_avail_qty` pooling factor.
- `new_rop = NegBin.ppf(SL)`, `new_min = new_rop − μ_LTD` (≥0), `new_max = new_rop + Q` (`Q` from EOQ or `order_qty_multiple`).
- **Hard rules** (`policy.py`): keep-alive `min ≥ 1` for high-criticality near-zero; floors/caps by criticality & price; no-decrease guard for low-cost recurring consumables; round to `order_qty_multiple`.
- Emit `s13_recommendations.csv` with `new_min/rop/max` + reason codes + which rule fired.
- **Gate:** every recommendation carries a reason code; no NaNs.

### Phase 3 — Cold-start model  (`s14`, `forecast/features.py`)
- `features.py`: demand-only feature builder (machine_type, lead time, price, policy, shared_parts, recency, peer/analog demand by machine_type). **Excludes** `avail_qty`/`vf_avail_qty`.
- **Hurdle:** Stage-1 LightGBM binary classifier `P(active)`; Stage-2 LightGBM `objective=tweedie` (or poisson) magnitude on active parts.
- Apply to No-history items; blend with analogy prior (machine_type peer median).
- Persist models via `joblib`.
- **Gate:** cold-start predictions beat the machine_type-median analogy baseline on held-out active parts.

### Phase 4 — Backtest & evaluation  (`s15`, `forecast/metrics.py`)
- Temporal holdout: train on snapshots ≤ 2024-12, test on 2026-01 (note limited periods; also leave-one-snapshot-out where feasible).
- **Forecast metrics:** MASE, RMSSE vs naive & vs SBA floor (never MAPE).
- **Inventory sim:** apply `new_min/rop/max` to the holdout; compute achieved fill rate vs target, over-/under-stock units & $, stockout count.
- **Decision agreement:** compare to `factory_recommended_new_*` — match rate, mean abs delta, over/under-shoot; special focus on the recom=0-trap parts (did keep-alive cover them?).
- Emit `s15_backtest_scorecard.csv`.
- **Gate / acceptance:** ML ≥ SBA on MASE; policy fill rate within tolerance of target; keep-alive coverage ~100% of critical near-zero parts.

### Phase 5 — Continuous-learning pipeline  (`s16`)
- `s16_pipeline.py` orchestration: ingest new BOM → append to panel (reuse s10) → recompute s11/s12/s13 (SBA/TSB auto-adapt; severity recomputed) → refresh recommendations.
- **Scheduled retrain** of s14 (monthly/quarterly) + **drift monitor**: PSI on feature distributions, MASE degradation vs rolling baseline → trigger off-cycle retrain.
- Versioned artifacts keyed to the s10 manifest for reproducibility.
- **Gate:** one-command re-run reproduces all outputs from the updated panel.

### Phase 6 — Serving integration (later)
- Port `forecast/` into `backend/app/scoring.py`; expose per-item recommendation + reason codes via the recommend router; keep human approval gate (PRD v1 §6.5/§7).

## 7. Key component specs

**Estimators** (`estimators.py`)
- `sba(series, alpha=0.1) -> rate_per_period` = `(1 - alpha/2) * z/p` (z=smoothed size, p=smoothed interval).
- `tsb(series, alpha, beta) -> rate` (updates demand probability every period incl. zeros — for decaying parts).
- `negbin_from_moments(mu, var) -> (r, p)`; `negbin_ppf(sl, r, p)`.

**Policy** (`policy.py`)
- `service_level(criticality, vf_avail_qty) -> float`
- `apply_hard_rules(row) -> (min, rop, max, reason_codes[])`

**Metrics** (`metrics.py`)
- `mase`, `rmsse`, `fill_rate(sim)`, `inventory_cost(sim)`, `decision_agreement(pred, factory_*)`.

## 8. Testing
- Unit tests (`backend/tests/` or `analysis/tests/`): estimator correctness on synthetic series (known Croston/SBA outputs); NegBin ppf monotonic in SL; hard-rule idempotence; missing≠zero handling; leakage guard (assert stock cols absent from feature matrix).
- Regression test: pipeline reproduces committed scorecard within tolerance.

## 9. Acceptance criteria
1. Every TCB item gets `new_min/rop/max` + reason codes.
2. ML ≥ SBA (MASE) on active parts; else fall back to SBA.
3. Simulated fill rate meets criticality target within tolerance.
4. Keep-alive covers ~100% of critical near-zero parts (recom=0 trap closed).
5. Full pipeline re-runs from an updated panel with one command.

## 10. Risks & mitigations
| Risk | Mitigation |
|---|---|
| Too few periods for stable NegBin dispersion | pool dispersion by quadrant; bootstrap cross-check; widen data over time |
| Cold-start model overfits (small active set) | strong regularization, hurdle, analogy prior, SBA fallback |
| Criticality→service-level map unavailable | config with sensible defaults; flag as blocking input |
| Unit mismatch (month vs day) | single conversion point in `ltd`; unit tests |
| Leakage of stock state into features | explicit exclusion + unit-test assertion |

## 11. Open dependencies (needed to start Phase 2)
- `machine_type` → criticality tier → target service level mapping.
- `vf_avail_qty` pooling function (how network stock scales SL/safety stock).
- Downtime-cost inputs for economic insurance sizing.
- Confirm `statsforecast`/`lightgbm` installable on the target environment.

## 12. Build order
P1 → P2 → P4 (backtest the statistical core first) → P3 (add cold-start) → re-backtest → P5 (pipeline) → P6 (serving). Rationale: prove the SBA+policy floor and its scorecard before adding ML complexity.
