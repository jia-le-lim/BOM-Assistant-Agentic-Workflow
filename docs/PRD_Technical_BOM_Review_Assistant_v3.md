# Technical PRD v3 — Cumulative-Window Intermittent-Demand Sizing Engine (Two-Snapshot PoC)

**Trailing-window rates → lead-time-demand distribution → Min / ROP / Max for TCB + Epoxy spares**

| | |
|---|---|
| Document type | Technical PRD — **Proof of Concept** (narrow scope) |
| Version | 3.2 (cumulative-window approach) |
| Supersedes | 3.1 (statistical revision) · 3.0 (continuous-forecast draft, retired after stress test) |
| Relationship to v2 | Scoped companion to v2's engine; same statistical paradigm, cumulative-only inputs. |
| Owner | LIM |
| Audience | Data / Analytics |
| Status | Round 1 — for build |
| Date | 17 August 2026 |
| Builds | [analysis/s13_cumulative_sizing.py](../analysis/s13_cumulative_sizing.py) |
| Exploratory only | [analysis/s12_two_snapshot_monthly.py](../analysis/s12_two_snapshot_monthly.py) (trend plots — NOT an engine input) |

---

## 0. Approach

> Treat each snapshot's nested trailing-consumption windows (`last_5/30/90/180/365/547_day`) as **multi-horizon demand-rate estimators** — never as a time series. From the latest snapshot's rates, model **lead-time demand** as a discrete distribution and read off **Min / ROP / Max** at a criticality-scaled service level. The earlier snapshot supplies a **trend annotation** only.

**Two ways to use a cumulative window — only one is valid here:**

| Use | Verdict |
|---|---|
| As a **rate / level statistic** (`last_365/365` = units/day) fed to an inventory formula | ✅ valid, leakage-free — this PRD |
| As a **forecastable time series**, or de-cumulated + even-spread into monthly points for SBA/TSB | ❌ invalid — leakage, non-uniform steps, destroyed intermittency (see §8) |

This keeps v3 aligned with PRD v2 §0.1 (classical time-series forecasting does not fit this intermittent data), while fully exploiting the cumulative data we actually have.

## 1. Goals & Non-Goals

### 1.1 Goals
1. Derive a per-part **cumulative feature set** (rates, ratios, trend, dispersion proxy, routing class) from the two snapshots' windows.
2. Size explainable **Min / ROP / Max** via a lead-time-demand distribution + criticality-scaled service level.
3. **Route** dormant / active / dying parts correctly; protect critical slow-movers (fix the recom = 0 trap).
4. **Score** the engine's Min/ROP/Max against the engineers' historical `factory_recommended_new_*`.
5. Stay honest: no fabricated monthly points; **missing ≠ 0**; conservative when variability is unknown.

### 1.2 Non-Goals
- No continuous time-series point-forecast as the engine.
- No de-cumulation into monthly training data (that is s12, exploratory only).
- No SBA/TSB on two snapshots (deferred until denser data — §7).
- No production integration / WINGS writes.

### 1.3 Scope
- **Sizing anchor:** the latest snapshot, `BOM REVIEW_Jan'26 .csv` (a part's *current* state).
- **Trend context:** `AUGUST'24 - BOM REVIEW ...xlsx` (optional; supplies the ~17-month rate delta).
- **Parts:** rows whose multi-tag module field (`new_modulle`) contains **`Module-TCB`** or **`Module-Epoxy`**.
- **PoC subset:** parts present in **both** snapshots (so a trend reading exists). The engine itself needs only the latest snapshot; the overlap restriction is a PoC choice, not an engine requirement — if Aug'24 is unavailable the engine still sizes from Jan'26 alone (trend columns `NaN`).

## 2. Data foundation

Windows present in every snapshot (units consumed in the trailing N days, cumulative from snapshot date):

`last_5 / 30 / 90 / 180 / 365 / 547_day_cnsmptn_qty`

Supporting fields (latest snapshot): `contractual_lead_time`, `order_qty_multiple`, `sfm_criticality`, `days_since_last_issue`, `frequencymonthswithusage`, `aging_status`, current `min_qty/rop_qty/max_qty`, and (for scoring only) `factory_recommended_new_min/rop/max`.

**Leakage guard:** `factory_recommended_*`, `sfm_*`, and other recommendation columns are **outputs / candidates**, used only for scoring — never as engine inputs (per [common.py](../analysis/common.py) `OUTPUT_COLS` / `CANDIDATE_COLS`).

## 3. Cumulative feature dictionary

Let `c_w = last_w_day_cnsmptn_qty` and window widths `W = {5,30,90,180,365,547}`. All rates are units/day.

### 3.1 Per-snapshot signals
| Feature | Formula | Purpose |
|---|---|---|
| `rate_w` | `c_w / w` for each `w ∈ W` | six horizon rates |
| `mu_day` | `c365 / 365` (fallback chain §3.4) | **primary demand rate** for sizing |
| `momentum` | `rate_90 / rate_365` | >1 accelerating, <1 decelerating |
| `recent_share` | `c30 / c365` | demand concentrated in last month? |
| `bucket_rate_k` | de-cumulated: `c30/30`, `(c90−c30)/60`, `(c180−c90)/90`, `(c365−c180)/185`, `(c547−c365)/182` | coarse rate-over-time profile (summary stats only — **not** a series) |
| `pseudo_cv2` | `var(bucket_rate_k) / mean(bucket_rate_k)²` (guard mean>0) | **dispersion proxy** (lumpiness) |
| `active_recent` | `c90 > 0` | demand in the last quarter |
| `dying_flag` | `c365 > 0 AND c90 = 0` | demand recently ceased |

### 3.2 Cross-snapshot trend (Jan'26 vs Aug'24)
| Feature | Formula | Purpose |
|---|---|---|
| `yoy_rate` | `Jan.rate_365 / Aug.rate_365` (guard denom>0) | long-run direction over ~17 months |
| `level_delta` | `Jan.c365 − Aug.c365` | net change in annual demand |
| `trend_flag` | `increasing` (yoy>1.2) / `stable` / `decreasing` (yoy<0.8) | **review annotation only** (does not scale μ in this PoC) |

### 3.3 Sign / clamp rules
- Windows may be **negative** (returns). For sizing, clamp `mu_day = max(mu_day, 0)`; keep raw values for the trend/feature table.
- A **blank** window → `NaN` (unknown), never 0. If every window is blank, the part is `no-data` → routed to review (§4), never silently sized to 0.

### 3.4 `mu_day` fallback chain
Use the longest reliable window available: `c365/365` → else `c547/547` → else `c180/180` → else `c90/90` → else `c30/30`. If none present → `no-data`.

## 4. Routing

| Class | Condition (latest snapshot) | Handling |
|---|---|---|
| **no-data** | all windows blank | review flag; no sizing |
| **dormant** | all present windows = 0 (and Aug'24 too, if available) | **insurance policy** (§5.4) — not forecast |
| **dying** | `c90 = 0` but `c547 > 0` (demand only older than a quarter) | size on `mu_day` but flag for review (demand ceased) |
| **active** | `c90 > 0` | rate-based sizing (§5) |

## 5. Sizing math

Notation (latest snapshot): `mu_day` (§3.4), lead time `L = contractual_lead_time` days, review period `T` days (config default 30), service level `SL` (§5.3).

### 5.1 Lead-time demand distribution
- Expected lead-time demand: `mu_L = mu_day × L`. Protection-period demand: `mu_LT = mu_day × (L + T)`.
- **Distribution choice (safe default = over-dispersed):**
  - `Poisson(mu)` only when the part looks regular (`frequencymonthswithusage` high **and** `pseudo_cv2` low).
  - `NegBin(mean=mu, VMR=φ)` otherwise (the intermittent/lumpy default), with variance-to-mean ratio `φ = max(φ_seg, 1 + pseudo_cv2)`, where `φ_seg` is a conservative per-class floor (e.g. active 1.5, dying/lumpy 3.0). **`pseudo_cv2` can only raise φ, never lower it** — this prevents the cumulative-smoothing bias from under-sizing safety stock.
  - NegBin parameterization from `(mu, φ)`: `size r = mu / (φ − 1)`, `prob p = r / (r + mu)` (so `Var = μ·φ`).

### 5.2 Levels (monotonic by construction)
Let `F_h^{-1}(SL)` be the SL-quantile of demand over horizon `h` (Poisson or NegBin as above):

```
ROP  = F_L^{-1}(SL)                      # covers lead-time demand at SL
Min  = max( ROP − mu_L , min_floor )     # safety-stock buffer, or insurance floor
Max  = F_{L+T}^{-1}(SL)                   # order-up-to over protection period
Max  = max( Max , ROP + MOQ )            # respect order_qty_multiple (MOQ)
```
- `MOQ = order_qty_multiple` (default 1). All three are **ceiled to integers** (conservative), then Max rounded up to the nearest MOQ multiple.
- `min_floor = 0` for active / dying parts; the criticality `keep_alive_floor` (§5.4) applies only to dormant parts.
- Guarantees `Min ≤ ROP ≤ Max` (since `T ≥ 0` and the MOQ clamp only raises Max).

### 5.3 Service level by criticality
`sfm_criticality` (or `senstivity_tag`) → `SL`: High → 0.99, Medium → 0.95, Low → 0.90, missing → 0.95 (configurable). Higher criticality ⇒ larger safety stock.

### 5.4 Dormant / insurance policy (fix the recom = 0 trap)
`mu_day = 0` would make every quantile 0. For **critical** dormant parts, override:
```
Min = keep_alive_floor (default 1)      # insurance stock for critical machines
ROP = Min
Max = Min + MOQ
```
Non-critical dormant parts may remain `0/0/0` but are **flagged for review**, never silently zeroed.

## 6. Scoring

For each part, compare engine `{min, rop, max}` against `factory_recommended_new_{min, rop, max}` (and, secondarily, current `min_qty/rop_qty/max_qty`):
- **Direction agreement** (engine vs engineer relative to current value).
- **Absolute / relative error** per level.
- **Within-tolerance match rate** (e.g. |Δ| ≤ 1 unit or ≤ 10%).
- Breakdowns by routing class and by `trend_flag`.

Goal: show the cumulative engine reproduces engineer intent on active parts and does **not** re-create the recom = 0 trap on critical dormant parts.

## 7. Outputs (built by s13)

`analysis/output/`:
- **`s13_cumulative_features.csv`** — per item: all §3 features + routing class + `trend_flag`.
- **`s13_sizing.csv`** — per item: `mu_day, L, T, SL, distribution, dist_params, min, rop, max`, routing, plus `factory_recommended_new_*` and scoring columns.
- **`s13_manifest.csv`** — provenance, counts by routing class, scoring summary.

## 8. Known limitations (accepted, with mitigations)
1. **Mean rate is solid; variance is a proxy.** `pseudo_cv2` from cumulative windows understates true lumpiness → mitigated by the **conservative `φ_seg` floor** and the *raise-only* rule (§5.1), so safety stock is never undersized on this account.
2. **Trend is a 2-point reading** (Aug'24 vs Jan'26) → used only as a review flag, not to scale μ.
3. **Lead-time units assumed days**; if `contractual_lead_time` is missing, fall back to the class median (default 30). Flagged per row.
4. **Review period `T`** is a config default (30 d) until the true review cadence is confirmed.
5. `last_30 ≠ 1 calendar month` (~1–2 days drift) — negligible for rate estimation.
6. **Dormant ≠ obsolete.** A dormant part may still be critical; §5.4 protects it rather than zeroing it.

## 9. Next steps
1. Build & run s13; review scoring vs `factory_recommended_new_*` by routing class.
2. Confirm `T` (review cadence) and the `φ_seg` / `SL` tables with the owner.
3. Collect denser snapshots (ideally ~monthly) → then enable Croston/SBA/TSB (an upgrade of the same engine) on *observed* per-period demand.
4. Continuous time-series ML only ever at the **aggregated site level** (many parts summed ≈ near-continuous), never per intermittent part.
