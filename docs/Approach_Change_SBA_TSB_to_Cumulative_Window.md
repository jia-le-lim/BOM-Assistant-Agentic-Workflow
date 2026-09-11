# Why the Engine Moved Off SBA/TSB — Approach-Change Record

| | |
|---|---|
| Document type | Design-decision record (retrospective) |
| Owner | LIM |
| Date | 27 August 2026 |
| Superseded method | [ML_Implementation_Plan_TCB.md](ML_Implementation_Plan_TCB.md) §6 Phase 1, [PRD v2](PRD_Technical_BOM_Review_Assistant_v2.md) §4.1–4.2 |
| Current spec | [PRD v3.2](PRD_Technical_BOM_Review_Assistant_v3.md) · [backend/app/engine_statistical.py](../backend/app/engine_statistical.py) |
| Evidence | [analysis/s11_segmentation.ipynb](../analysis/s11_segmentation.ipynb), `analysis/output/s11_segmentation.csv`, [log/2026-08-13_Multi-Month_TCB_Extraction.md](log/2026-08-13_Multi-Month_TCB_Extraction.md), `analysis/output/s14_validation_scorecard.csv` |

---

## 1. One-paragraph summary

PRD v2 routed every active part to **SBA** (Syntetos–Boylan Approximation, bias-corrected Croston), with **TSB** for decaying parts, then NegBin lead-time demand → Min/ROP/Max. S11 built that segmentation on the real multi-month panel and showed the estimator had almost nothing to estimate on: the panel is 8 irregular snapshots, ~3 points per item, and 0.05 non-zero demand events per item on average. Both methods update demand size only on positive demand; TSB additionally updates occurrence probability on every observed period, including zeros. With so few observed positive events, the demand-size estimates were poorly supported. The engine therefore moved to **cumulative-window rate estimation** (PRD v3.2): read the demand rate straight out of the trailing-consumption windows each snapshot already ships (`last_5/30/90/180/365/547_day`), and keep the rest of the pipeline — dispersion, NegBin LTD, service level, insurance policy — unchanged. **The pivot replaced the rate estimator, not the paradigm.**

## 2. What SBA/TSB was supposed to do

Per PRD v2 §4.1–4.3 and the ML plan:

```
monthly demand series per item  ->  ADI / CV2 segment  ->  SBA (or TSB if decaying)
   ->  per-period rate  ->  NegBin lead-time demand  ->  new_min / new_rop / new_max
```

Everything downstream of "per-period rate" is still in production today. Only the first two boxes changed.

## 3. Why it did not survive contact with the data

### 3.1 The panel is too short and too irregular for a recursive estimator

8 usable snapshots span 2024-07 → 2026-01 (19 months), with Nov'24, Jan–Feb'25 and Jun–Dec'25 missing, and Apr'25 dropped as a byte-identical duplicate of Mar'25. Croston/SBA/TSB are recursive per-period updaters; they assume a period grid. Ours has a 7-month hole immediately before the anchor snapshot.

Measured on `s11_segmentation.csv` (3,060 TCB items):

| Statistic | Value |
|---|---|
| Mean months present per item | **3.08** (median 3, max 7) |
| Mean non-zero demand months per item | **0.05** |
| Items with any non-zero month | ~110 |

### 3.2 The estimator would never have fired

SBA updates demand size and inter-demand interval only on a positive period. TSB updates size on positive periods but **updates occurrence probability every observed period, including zeros**, allowing its forecast to decay. At 0.05 observed non-zero periods per item in the original panel, neither method had enough positive observations to establish a reliable smoothed demand-size estimate.

### 3.3 Coverage did not justify the machinery

S11 segment counts on the same 3,060 items:

| Segment | Items | Routing under PRD v2 |
|---|---|---|
| Dormant | 2,690 | hard-rule / keep-alive policy — **not SBA** |
| No-history | 260 | cold-start feature model — **not SBA** |
| Intermittent | 106 | SBA / TSB |
| Smooth | 2 | SBA |
| Lumpy | 2 | SBA + bootstrap |

**110 of 3,060 items (3.6%)** would ever reach the SBA path — each on a ~3-point series. The other 96.4% were always going to be decided by the policy and cold-start layers that exist today.

### 3.4 ADI and CV² were measuring review scope, not demand

Monthly TCB row counts swing **140 → 2,780** because some source files are full site dumps and others are curated review lists. An absent month means "out of that month's review scope", not "zero demand" (correctly held as `NaN`, never zero-filled). But ADI is computed from *presence*, so on this panel ADI/CV² partly encode which parts a reviewer happened to pull that month — an artifact, not a demand property. The segmentation stayed useful as a **routing hint**; it was not sound enough to select a per-item statistical estimator.

### 3.5 The monthly bucket did not measure the month

The panel's monthly demand bucket is `last_30_day_cnsmptn_qty`, but snapshot gaps are 1–8 months. The bucket samples 30 days out of a multi-month gap; demand in the uncovered days is silently dropped. The alternative — de-cumulate the nested windows and spread each wide bucket evenly across its months (built in [s12_two_snapshot_monthly.py](../analysis/s12_two_snapshot_monthly.py)) — manufactures points never observed and **erases the intermittency SBA exists to model** (every month becomes non-zero, ADI collapses toward 1). S12 was kept as a plotting artifact and explicitly barred from being an engine input. PRD v3 §0 records the same rule: a cumulative window is valid as a **rate statistic**, invalid as a **forecastable series**.

### 3.6 Dependency cost was real

`statsforecast` 2.1.1 pins `pandas < 3` (via `fugue`/`triad`), which downgraded the workspace from pandas 3.0.5 to 2.3.3 and forced a split environment. Acceptable for a core method; not for one covering 3.6% of rows.

### 3.7 The rate was already in the file

Every snapshot row ships six nested trailing-consumption windows. `last_365 / 365` is a **directly measured** units/day rate over a year — leakage-free, present for 100% of rows including single-snapshot parts, needing no history reconstruction. SBA's whole job is to estimate that same μ from a series we do not reliably have. Cheaper estimator, wider coverage, no new dependency, downstream maths untouched.

## 4. What the engine does instead (PRD v3.2, `engine_statistical.py`)

```
latest snapshot row
  -> mu_day = c365/365, fallback chain c547 -> c180 -> c90 -> c30    (measured rate)
  -> pseudo_cv2 from de-cumulated bucket rates                       (dispersion PROXY)
  -> phi = max(phi_seg, 1 + pseudo_cv2)   raise-only, never lowers   (anti-undersizing)
  -> LTD ~ NegBin(mu_day * L, phi)   [Poisson only when part is regular]
  -> ROP = F_L^-1(SL);  Min = ROP - mu_L;  Max = F_{L+T}^-1(SL), MOQ-clamped
  -> route: no-data / dormant / dying / active; critical dormant -> keep-alive floor
```

Carried over from the SBA design unchanged: the intermittent-demand framing, NegBin lead-time demand, criticality-scaled service level, the insurance/keep-alive policy that closes the `recom = 0` trap, missing ≠ zero, and scoring against `factory_recommended_new_*`.

Deliberately weaker, and mitigated: the **mean rate is solid, the variance is a proxy**. Cumulative windows smooth away lumpiness, so `pseudo_cv2` understates it — hence the conservative per-class φ floor plus the raise-only rule, so this can never undersize safety stock.

Validation on the PoC subset (`s14_validation_scorecard.csv`, 77 scored items vs engineer values):

| Subset | Level | n | exact % | within-tol % |
|---|---|---|---|---|
| all | min | 77 | 75.3 | 85.7 |
| all | rop | 77 | 74.0 | 88.3 |
| all | max | 77 | 48.1 | 83.1 |
| non-consumable | min | 43 | 100.0 | 100.0 |

## 5. Where the effort went after the pivot

Sizing accuracy was never the business goal — **cutting manual review touches** is. Freeing the SBA/TSB budget moved work to the layers that do that:

| Layer | Where |
|---|---|
| Review band, per-row confidence, machine reason codes | `engine_statistical.py` |
| Agreement scoring + benchmark ladder with a demand-drift guard | `engine_statistical.py` `_benchmark` / `_agreement` |
| Auto-clear rungs, calibrated — **defaults OFF**, ~67% agreement on Jan'26 could not clear a safe precision bar | [s15_autoclear_calibration.py](../analysis/s15_autoclear_calibration.py), [s17_clear_threshold_calibration.py](../analysis/s17_clear_threshold_calibration.py) |
| Advisory triage lanes | `backend/app/agent/triage.py`, [s16_triage_backtest.py](../analysis/s16_triage_backtest.py) |
| kNN peer-history similarity (Tier 2: validate the statistical number against what engineers did on similar parts) | `similarity_neighbour` tables, branch `feat/knn-advisory-similarity-layer` |
| Engine vs incumbent SFM comparison | [s18_engine_vs_sfm.ipynb](../analysis/s18_engine_vs_sfm.ipynb) |

## 6. When SBA/TSB comes back

The pivot is an estimator swap, so the return path is narrow and cheap (PRD v3 §9.3). Bring it back when **all** hold:

1. Snapshots arrive at a stable, roughly monthly cadence with **consistent review scope** (so presence means demand, not selection).
2. ≥12–15 usable periods per item on the parts that matter.
3. Demand is read per period from observed issues, not from a trailing window straddling gaps.

Then replace `_estimate_mu` with the SBA/TSB rate and leave the dispersion, distribution, policy, and scoring layers exactly as they are. Continuous time-series ML stays reserved for the **aggregated site level**, never per intermittent part.

## 7. Retest after the additional archive (11 September 2026)

The expanded panel contains 26,302 eligible reviews across 14 observed cycles,
with a median nine snapshots per item-stockroom but at most three consecutive
review months. An offline test of 27 observed-snapshot SBA/TSB variants found no
pooled matching improvement: 54.42% current versus 47.95% SBA, 51.11% TSB and
50.44% SBA-active/TSB-declining on 1,808 active/declining reviews. The best blend
reached 53.60% and increased large undersizing. These are snapshot approximations,
not validation on continuous monthly issues. Production was unchanged.

See the [full retest and methodological limits](log/2026-09-11_SBA_TSB_Backtest.md).
