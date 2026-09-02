# Plan: Confidence Scoring Model for BOM Review

## Summary

Train a calibrated model that, when a fresh BOM cycle is scored, estimates the probability the
engineer will accept the engine's Min/ROP/Max. The number is **displayed to the reviewer** — it does
not gate, sort, or auto-approve anything. Deliverable is a training notebook plus a persisted model
artifact.

## User Story

As a BOM review engineer, I want to see how likely the engine's recommendation is to be one I'd
accept, so that I can calibrate how much scrutiny to give each row.

## Problem → Solution

Engineers review every live row with no signal about which ones the engine tends to get right →
each row carries an honest, calibrated acceptance probability.

## Metadata

- **Complexity**: Medium
- **Source PRD**: N/A (free-form, derived from the S20 divergence analysis)
- **PRD Phase**: N/A
- **Estimated Files**: 3 created, 0 dependencies added

---

## Decisions Locked (confirmed with owner, 2026-09-01)

| Decision | Choice | Consequence |
|---|---|---|
| Output use | **Display only** — no gating, no ranking | Success metric is **calibration**, not AUC |
| Algorithm | **L2 logistic regression**, fitted with `scipy.optimize` L-BFGS-B | LightGBM dropped by owner, 2026-09-01 |
| Cold start | Missing-indicator + `has_prior` flag, single model | 29% of live rows have no prior cycle |
| Dependencies | **None added.** numpy, pandas, scipy, matplotlib all installed | Removes the Intel offline-install risk entirely |
| Model artifact | **JSON**, not a pickle | ~15 numbers; diffable in git, no version coupling, backend can score with one dot product |
| Population | Live rows only (active + dying), n=711 | Dormant is hard-ruled; including it inflates every metric |
| Target | Scorecard rule: within 10% on **Max and ROP**, no floor, Min ignored | Identical to `engine_statistical.close_enough` |
| Calibration drift | **Recalibrate every cycle** | Retrain step must be part of the monthly run |
| Prior-cycle feature | Most recent prior cycle, ungated | `_drift_ok` gating measured to add nothing |

### Measured expectations — do not promise more than this

Four experiments on this data established the ceiling. The plan is written to hit it, not beat it.

| Metric | Expected |
|---|---|
| Walk-forward AUC | **≈ 0.70–0.72** |
| Base acceptance rate (live) | 30.9% |
| Coverage at ≥85% precision | **0%** |
| Best single feature | `gap_prior_decision` (58.3% → 13.8%, monotonic) |

If the notebook reports AUC materially above ~0.75, **suspect leakage** and stop. That is the single
most likely failure mode here — see Risks.

---

## UX Design

### Before
```
┌──────────────────────────────────────────────┐
│  Row 500699364  FILTER,ASSEMBLY,DIE          │
│  Engine: Max 6 / ROP 4 / Min 2               │
│  [ Accept ]  [ Override ]                    │
│                                              │
│  Reviewer has no signal about which rows     │
│  the engine tends to get right.              │
└──────────────────────────────────────────────┘
```

### After
```
┌──────────────────────────────────────────────┐
│  Row 500699364  FILTER,ASSEMBLY,DIE          │
│  Engine: Max 6 / ROP 4 / Min 2               │
│  Acceptance likelihood: 62%                  │
│    (this cycle's average: 44%)               │
│  [ Accept ]  [ Override ]                    │
└──────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Review row | Engine numbers only | Numbers + acceptance likelihood | Display only — no sort, no gate |
| Reviewer action | Unchanged | Unchanged | Model never blocks or approves |
| Cycle context | None | Cycle base rate shown beside score | Guards against absolute misreading |

**Scope note:** the UI above is the *intent*. This plan delivers the notebook and artifact only.
Wiring into `recommendation_result` and the frontend is separate work.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `analysis/common.py` | 19-35 | `OUTPUT_COLS` / `MEMORY_COLS` — PRD §5.1 forbids these as ML inputs. Governs the whole feature list. |
| P0 | `backend/app/engine_statistical.py` | 41-52 | `SL_BY_CRIT`, `AGREE_TOL`, `AGREE_FIELDS` — the target must import these, never restate them |
| P0 | `backend/app/engine_statistical.py` | 196-225 | `close_enough` / `_agreement` — the exact match rule |
| P0 | `analysis/s20_diverge_rootcause.py` | 46-77 | `_pair`, `_matched`, `_selfcheck` — target construction, already written and self-checked |
| P1 | `analysis/s20_diverge_features.py` | 30-50 | Payload pull and the `s20_payloads.pkl` schema this notebook consumes |
| P1 | `backend/app/engine_statistical.py` | 226-260 | `_benchmark` / `_drift_ok` — the prior-review rung this feature formalises |
| P2 | `analysis/s18_engine_vs_sfm.ipynb` | cell 1 | Notebook bootstrap convention to mirror exactly |
| P2 | `backend/requirements.txt` | 21-24 | Confirms scipy is already a project dependency — no install step needed |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| Platt scaling | Platt 1999; scikit-learn calibration guide (for reference, not imported) | Platt scaling *is* a logistic regression on the raw log-odds — reuse the same fitter |
| Isotonic vs sigmoid | scikit-learn calibration guide | Isotonic needs ≳1000 samples; at n=518 it memorises. Use sigmoid |
| Reliability diagrams | scikit-learn calibration guide | 5 bins at this sample size; 10 gives near-empty buckets |
| Brier score | Standard definition | `mean((p - y)**2)`; lower is better, decomposes into calibration + refinement |
| `scipy.optimize.minimize` | SciPy docs, L-BFGS-B | Pass `jac=True` and return `(loss, grad)` together; always check `res.success` |
| Numerically stable logloss | NumPy `logaddexp` | `log(1+exp(z))` overflows above z≈700; `np.logaddexp(0, z)` does not |

```
KEY_INSIGHT: Isotonic calibration overfits below ~1000 samples and looks perfect in-sample.
APPLIES_TO: Task 5 (calibration)
GOTCHA: Use sigmoid/Platt. Fit calibration on out-of-fold predictions only, never in-sample.
```

```
KEY_INSIGHT: No ML library is required. Logistic regression + Platt + AUC + Brier + ECE is ~60
lines of numpy/scipy, and scipy is already a backend dependency for the engine's NBD quantiles.
APPLIES_TO: Tasks 3, 5, 6
GOTCHA: Use scipy L-BFGS-B, not hand-rolled gradient descent -- the learning rate becomes an
untested hyperparameter that can silently under-converge.
```

---

## Patterns to Mirror

### NOTEBOOK_BOOTSTRAP
```python
# SOURCE: analysis/s18_engine_vs_sfm.ipynb, cell 1
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path.cwd()
ROOT = next(p for p in [HERE, *HERE.parents] if (p / "backend").is_dir())
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "backend" / "app")]

import engine_statistical  # the production sizing engine
from common import LEVELS, OUT, agree, read_module_rows, to_num

pd.set_option("display.width", 160)
print("engine", engine_statistical.MODEL_VERSION, "| levels", LEVELS)
```
**Note the bare `import engine_statistical`** — notebooks put `backend/app` directly on `sys.path`.
Scripts under `analysis/` that use `from app import engine_statistical` (s20) put `backend` on the
path instead. Use the notebook form in the notebook, the script form in the module.

### SCRIPT_MODULE_HEADER
```python
# SOURCE: analysis/s20_diverge_rootcause.py:1-40
"""S20 -- root-cause decomposition of engineer-vs-engine divergence, with ablations.

<what it does, why it exists, what it costs, stated plainly>

Run: python analysis/s20_diverge_features.py   (pull, needs DB)
     python analysis/s20_diverge_rootcause.py  (offline)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import engine_statistical as E  # noqa: E402

PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"
OUT = ROOT / "analysis" / "output" / "s20_ablation.csv"
```

### TARGET_CONSTRUCTION
```python
# SOURCE: analysis/s20_diverge_rootcause.py:46-58
def _pair(out: pd.DataFrame) -> np.ndarray:
    """Max and ROP only -- Min is a derived floor, not a replenishment lever."""
    return np.c_[out.factory_recommended_new_max,
                 out.factory_recommended_new_rop].astype(float)


def _matched(engine: np.ndarray, bench: np.ndarray) -> np.ndarray:
    """Vectorised engine_statistical.close_enough over Max and ROP."""
    return (np.abs(engine - bench) <= E.AGREE_TOL * np.abs(bench)).all(axis=1)
```

### SELF_CHECK_PATTERN
```python
# SOURCE: analysis/s20_diverge_rootcause.py:68-77
def _selfcheck() -> None:
    """Pin the tolerance, and pin that it still equals the engine's own rule."""
    b = np.array([[2.0, 2.0], [10.0, 10.0], [0.0, 0.0], [5.0, 5.0]])
    hit = np.array([[2., 2.], [11., 11.], [0., 0.], [5., 5.]])
    miss = np.array([[1., 2.], [12., 10.], [1., 0.], [5., 7.]])
    assert list(_matched(hit, b)) == [True] * 4
    assert list(_matched(miss, b)) == [False] * 4
    for cand, want in ((hit, "match"), (miss, "diverge")):
        for e, x in zip(cand, b):
            assert E._agreement((e[0], e[1], 0), (x[0], x[1], 999)) == want, \
                "harness drifted from engine_statistical._agreement"
```

### WALK_FORWARD_LOOP
```python
# SOURCE: analysis/s20_diverge_rootcause.py:160-175 (adapted)
for i, mo in enumerate(months):
    if i == 0:
        continue                                   # no earlier data to train on
    te = live & (month == mo)
    tr = live & np.isin(month, months[:i])
    if te.sum() < 5 or ok[tr].sum() < 5 or (1 - ok[tr]).sum() < 5:
        continue                                   # fold has too few of a class
    ...
```

### GOVERNANCE_CONSTRAINT
```python
# SOURCE: analysis/common.py:19-35
# Columns the PRD (section 5.1) classifies as engine OUTPUT -- never an ML input.
OUTPUT_COLS = ["factory_recommended_new_max", "factory_recommended_new_rop",
               "factory_recommended_new_min"]

# PRD section 5.1 "memory / decision history" -- store as history, never as ML input.
MEMORY_COLS = ["justification", "comments", "review_acknowledge", "rop_adoption",
               "max_adoption", "ooq_adoption", "modified_user", "modified_date"]
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `analysis/s21_confidence_features.py` | CREATE | Single importable definition of features + target, shared by notebook and any future scoring path |
| `analysis/s21_confidence_model.ipynb` | CREATE | The training deliverable |
| `analysis/tests/test_s21_features.py` | CREATE | Pins the target against the engine and enforces the leakage guard |

Outputs written (not source): `analysis/output/s21_confidence_model.json`,
`analysis/output/s21_calibration.csv`, `analysis/output/s21_reliability.png`.

**No dependency file.** Everything needed is already installed: `numpy`, `pandas`, `scipy`
(`backend/requirements.txt:24`, present for the engine's NBD quantiles) and `matplotlib` 3.11.1.
`analysis/` currently imports no ML library and this plan keeps it that way.

## NOT Building

- **No auto-pass, no gating, no queue sorting.** Display only. Measured coverage at ≥85% precision is 0%.
- **No backend wiring.** Nothing writes to `recommendation_result`; `engine_adapter` untouched.
- **No frontend change.** The UX diagram is intent, not scope.
- **No SFM features.** Owner decision — SFM is being replaced, not tracked.
- **No item- or machine-level historical match rate.** Measured: both lowered walk-forward AUC.
- **No consumption-volatility feature.** Measured: points the wrong way (artifact of band width vs part size).
- **No `_drift_ok` gating on the prior-cycle feature.** Measured: 56.9% vs 60.0%, no effect.
- **No re-scoring of historical batches.** They are an audit record; `score_batch` protects reviewed rows by design.
- **No isotonic calibration.** Sample size too small.
- **No changes to `engine_statistical.py`.** Read-only in this plan.
- **No new dependencies.** No scikit-learn, no LightGBM, no joblib. The whole model is ~60 lines of
  numpy/scipy and the artifact is JSON.
- **No tree/ensemble model.** Owner decision, 2026-09-01.

---

## Step-by-Step Tasks

### Task 1: Build the feature module

- **ACTION**: Create `analysis/s21_confidence_features.py`.
- **IMPLEMENT**: Module docstring per SCRIPT_MODULE_HEADER, then:
  - `FEATURES: list[str]`, exactly: `gap_prior_decision`, `has_prior`, `delta_abs_vs_current`,
    `delta_rel_vs_current`, `mu_daily_rate`, `freq_months_usage`, `days_since_last_issue`,
    `is_dying_route`, `policy_order_to_max`
  - `build(payloads: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]`
    returning `(X, y, live_mask, month)`
  - Target: run `E.run(payloads)`, take `_pair`, compare to `np.c_[_final_max, _final_rop]` using
    `E.AGREE_TOL`. **Import the tolerance — never write `0.10`.**
  - Prior-cycle features: iterate months in sorted order; for each row read that item's history **as
    of before the current month**, then fold the current month in. Key on
    `item_id + "|" + stockroom_id`.
  - Cold start: `gap_prior_decision = -1`, `has_prior = 0`.
  - `_selfcheck()` per SELF_CHECK_PATTERN, plus the leakage assertion below.
- **MIRROR**: SCRIPT_MODULE_HEADER, TARGET_CONSTRUCTION, SELF_CHECK_PATTERN.
- **IMPORTS**: `from app import engine_statistical as E` after `sys.path.insert(0, str(ROOT / "backend"))`.
- **GOTCHA**: The two-pass loop per month is mandatory. Reading and writing history in one pass leaks
  the current row's own outcome into its own feature and inflates AUC toward 0.9.
- **GOTCHA**: `gap_prior_decision` derives from the *previous* cycle's `final_max`, an `OUTPUT_COLS`
  field. This is permitted because it is prior-cycle history known before the current review — the
  same rung `engine_statistical._benchmark()` already uses in production. Put that justification in a
  comment; a reviewer will otherwise flag it as a §5.1 violation.
- **VALIDATE**: `python analysis/s21_confidence_features.py` runs `_selfcheck()` and prints shape,
  base rate, `has_prior` coverage. Expect ~711 live rows, base ≈ 30.9%, `has_prior` ≈ 71%.

### Task 2: Notebook — load, build, describe

- **ACTION**: Create `analysis/s21_confidence_model.ipynb`, cells 0–3.
- **IMPLEMENT**:
  - Cell 0 markdown: `# S21 — Confidence scoring for BOM review`, plus a "what this is / is not"
    note stating display-only and the 0% auto-pass finding.
  - Cell 1 code: NOTEBOOK_BOOTSTRAP plus `import s21_confidence_features as F`.
  - Cell 2 markdown: `## 1. Data and target`.
  - Cell 3 code: load `OUT / "s20_payloads.pkl"`, call `F.build(...)`, print row counts, per-month
    base rate, per-month `has_prior` coverage.
- **MIRROR**: NOTEBOOK_BOOTSTRAP; numbered markdown headers as in s11/s18.
- **GOTCHA**: `s20_payloads.pkl` comes from `analysis/s20_diverge_features.py`, which needs DB
  access. If absent, fail with that instruction, not a bare `FileNotFoundError`.
- **VALIDATE**: Per-month base rates reproduce the known series: 2024-07 28.6%, 2024-09 35.0%,
  2024-10 25.5%, 2024-12 24.4%, 2025-03 15.7%, 2025-05 25.4%, 2026-01 44.0%.

### Task 3: Add the model + metrics helpers to the feature module

- **ACTION**: Extend `analysis/s21_confidence_features.py` with the fit and metric functions, so the
  notebook contains no numerics of its own and the same code can later be reused by a scoring path.
- **IMPLEMENT**:
  ```python
  from scipy.optimize import minimize   # scipy is already a backend dependency

  def fit_logistic(A: np.ndarray, y: np.ndarray, lam: float = 1.0) -> np.ndarray:
      """L2 logistic regression via L-BFGS-B. Returns [intercept, *coefs].

      scipy.optimize rather than hand-rolled gradient descent: no learning-rate
      to tune, and convergence is checked rather than assumed.
      """
      X = np.c_[np.ones(len(A)), A]

      def obj(w):
          z = X @ w
          # log(1+exp(z)) computed stably for large |z|
          ll = np.sum(np.logaddexp(0, z) - y * z)
          grad = X.T @ (1 / (1 + np.exp(-z)) - y)
          pen, gpen = 0.5 * lam * w[1:] @ w[1:], np.r_[0.0, lam * w[1:]]
          return (ll + pen) / len(X), (grad + gpen) / len(X)

      res = minimize(obj, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B")
      assert res.success, f"logistic fit did not converge: {res.message}"
      return res.x


  def predict(w: np.ndarray, A: np.ndarray) -> np.ndarray:
      return 1 / (1 + np.exp(-(np.c_[np.ones(len(A)), A] @ w)))


  def auc(y: np.ndarray, s: np.ndarray) -> float:
      """Rank-based ROC AUC -- no sklearn needed."""
      r = pd.Series(s).rank().values
      n1, n0 = y.sum(), (1 - y).sum()
      return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


  def brier(y: np.ndarray, p: np.ndarray) -> float:
      return float(np.mean((p - y) ** 2))


  def ece(y: np.ndarray, p: np.ndarray, bins: int = 5) -> float:
      """Expected calibration error over equal-width probability bins."""
      edges = np.linspace(0, 1, bins + 1)
      idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
      total = 0.0
      for b in range(bins):
          m = idx == b
          if m.any():
              total += m.mean() * abs(p[m].mean() - y[m].mean())
      return float(total)
  ```
- **GOTCHA**: Use `np.logaddexp(0, z)`, not `np.log(1 + np.exp(z))` — the latter overflows for
  `z > ~700` and silently returns `inf`, which makes L-BFGS-B fail with an unhelpful message.
- **GOTCHA**: The `assert res.success` is deliberate. Silent non-convergence is the failure mode that
  produces a plausible-looking but wrong model.
- **VALIDATE**: On a synthetic separable 2-feature set, `fit_logistic` recovers coefficients with the
  correct signs and `auc` returns 1.0.

### Task 4: Notebook — walk-forward evaluation

- **ACTION**: Cells 4–5, `## 2. Walk-forward evaluation`.
- **IMPLEMENT**: For each month index `i > 0`, train on all earlier months and predict the current
  month; collect out-of-fold probabilities. Standardise using the **training fold's** mean/std only.
  Report pooled AUC, Brier, and per-month AUC via the Task 3 helpers.
- **MIRROR**: WALK_FORWARD_LOOP.
- **IMPORTS**: `import s21_confidence_features as F` — no sklearn.
- **GOTCHA**: Never use random K-fold. Random folds leak future months into the past; that is exactly
  what made an earlier leave-one-month-out run read 0.741 instead of the honest 0.717.
- **GOTCHA**: Standardise with the training fold's statistics, never the whole dataset's — full-data
  scaling leaks test-fold distribution into training.
- **GOTCHA**: Skip a fold when the training slice has fewer than 5 of either class; 2024-07 is first
  and has no training data at all.
- **VALIDATE**: Pooled AUC in **0.68–0.75**. Outside that range, stop and investigate.

### Task 5: Notebook — calibration (the primary deliverable)

- **ACTION**: Cells 6–8, `## 3. Calibration`.
- **IMPLEMENT**:
  - **Platt scaling is itself a logistic regression on one feature** — reuse `F.fit_logistic`:
    ```python
    # z = raw out-of-fold log-odds; fit a,b so that sigmoid(a*z + b) is calibrated
    platt = F.fit_logistic(z_oof.reshape(-1, 1), y_oof)   # -> [b, a]
    p_cal = F.predict(platt, z_new.reshape(-1, 1))
    ```
  - Reliability diagram: bin `p_cal` into 5 equal-width bins, plot mean predicted vs observed rate
    against the diagonal with `matplotlib`.
  - Report `F.brier` and `F.ece` before and after calibration.
  - Per-month table of predicted-mean vs actual rate — this is where the 15.7%→44.0% drift appears,
    and it is the evidence for per-cycle recalibration.
  - Save `analysis/output/s21_reliability.png` and `analysis/output/s21_calibration.csv`.
- **IMPORTS**: `import matplotlib.pyplot as plt`, `import s21_confidence_features as F`.
- **GOTCHA**: Platt (sigmoid) not isotonic. Isotonic needs ≳1000 samples; at n=518 it looks better
  in-sample and is wrong out-of-sample. A monotonic step function fitted on 518 points memorises.
- **GOTCHA**: Calibrate on out-of-fold predictions only. In-sample calibration yields a perfect
  diagram that means nothing.
- **GOTCHA**: Fit Platt on the **log-odds**, not the probability — fitting a logistic on an already
  squashed [0,1] input is numerically poor and hard to interpret.
- **VALIDATE**: ECE after calibration < 0.10. The per-month table must visibly show drift; if it does
  not, months are being pooled incorrectly.

### Task 6: Notebook — coefficients and persistence

- **ACTION**: Cells 9–11, `## 4. What the model learned` and `## 5. Persist`.
- **IMPLEMENT**:
  - Print standardised coefficients sorted by absolute value. Sanity-check the signs against the
    measured directions: `gap_prior_decision` and `delta_abs_vs_current` must be **negative**
    (bigger gap → less likely to match). A positive sign on either means something is wrong.
  - Refit on **all** live rows, recompute the scaler and Platt parameters, then write JSON:
    ```python
    import json
    art = {
        "features": F.FEATURES,
        "coef": w[1:].tolist(), "intercept": float(w[0]),
        "feature_means": mu.tolist(), "feature_stds": sd.tolist(),
        "platt_a": float(platt[1]), "platt_b": float(platt[0]),
        "trained_at": pd.Timestamp.utcnow().isoformat(),
        "n_train": int(live.sum()), "base_rate": float(y.mean()),
        "engine_model_version": engine_statistical.MODEL_VERSION,
        "agree_tol": engine_statistical.AGREE_TOL,
        "agree_fields": list(engine_statistical.AGREE_FIELDS),
    }
    (OUT / "s21_confidence_model.json").write_text(json.dumps(art, indent=2))
    ```
- **GOTCHA**: Persisting `agree_tol` and `engine_model_version` is required. If either changes the
  model was trained against a different target and must be retrained — without this the mismatch is
  silent.
- **GOTCHA**: JSON, not pickle. Scoring is `sigmoid(platt_a * ((x - means)/stds @ coef + intercept)
  + platt_b)` — the backend can implement that in five lines with no ML import, and the file diffs
  cleanly in git when a coefficient moves.
- **VALIDATE**: Reload the JSON in a fresh cell, re-score 5 rows from the notebook, confirm the
  probabilities match to 1e-9 and that `features` equals `F.FEATURES`.

### Task 7: Tests

- **ACTION**: Create `analysis/tests/test_s21_features.py`.
- **IMPLEMENT**: three pytest tests:
  1. `test_target_matches_engine` — build the target on synthetic rows; assert it equals
     `E._agreement` verdicts (SELF_CHECK_PATTERN cases).
  2. `test_no_leakage_in_prior_features` — two cycles for one item; assert cycle 1 has
     `has_prior == 0` and `gap_prior_decision == -1`, and cycle 2's gap uses cycle 1's `final_max`,
     not its own.
  3. `test_forbidden_columns_absent` — assert no `common.OUTPUT_COLS` or `common.MEMORY_COLS` name
     appears in `F.FEATURES`.
- **MIRROR**: test style under `backend/tests/`.
- **GOTCHA**: `analysis/` has no `tests/` directory — create it. Do NOT put these in
  `backend/tests/`; that suite must keep passing without the ML dependencies installed.
- **VALIDATE**: `python -m pytest analysis/tests -q` passes.

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| target vs engine | engine (2,2), bench (2,2) | match | no |
| target vs engine | engine (1,2), bench (2,2) | diverge — 2 admits only 2 | yes |
| target vs engine | engine (11,11), bench (10,10) | match — 10% boundary inclusive | yes |
| target vs engine | engine (1,0), bench (0,0) | diverge — 0 admits only 0 | yes |
| cold start | item's first cycle | `has_prior=0`, `gap_prior_decision=-1` | yes |
| prior feature | item's second cycle | gap computed vs cycle 1 `final_max` | no |
| leakage guard | `F.FEATURES` | no `OUTPUT_COLS` / `MEMORY_COLS` names | yes |
| `fit_logistic` | synthetic separable 2-feature set | correct coefficient signs, `res.success` true | no |
| `fit_logistic` | feature with extreme magnitude (z ≈ 1e3) | finite loss via `logaddexp`, converges | yes |
| `auc` | perfectly separable scores | 1.0 | no |
| `ece` | perfectly calibrated probabilities | ≈ 0.0 | no |

### Edge Cases Checklist
- [ ] Item appearing in only one cycle (no prior)
- [ ] Item whose prior cycle had `final_max = 0`
- [ ] First month in the series (no training data — fold must be skipped)
- [ ] Month with fewer than 5 of either class
- [ ] `days_since_last_issue` null on 66% of rows — encode as -1, never drop the row
- [ ] Duplicate `(month, item_id)` — one exists; the composite key with `stockroom_id` resolves it

---

## Validation Commands

### Dependencies already present (no install step)
```bash
python -c "import numpy, pandas, scipy, matplotlib; print('all present')"
```
EXPECT: `all present` — if this fails the analysis venv is broken, not the plan

### Feature module self-check
```bash
python analysis/s21_confidence_features.py
```
EXPECT: `_selfcheck()` passes; ~711 live rows; base rate ≈ 30.9%; `has_prior` ≈ 71%

### Unit tests
```bash
python -m pytest analysis/tests -q
```
EXPECT: all pass

### Backend suite unaffected
```bash
python -m pytest backend/tests -q
```
EXPECT: 271 passed — this plan must not change backend behaviour

### Notebook execution
```bash
python -m jupyter nbconvert --to notebook --execute analysis/s21_confidence_model.ipynb --output s21_executed.ipynb
```
EXPECT: runs end to end; pooled AUC 0.68–0.75; ECE < 0.10

### Manual Validation
- [ ] Reliability diagram sits near the diagonal
- [ ] Per-month calibration table visibly shows the 15.7%→44.0% drift
- [ ] Coefficient signs match the measured directions (`gap_prior_decision` and
      `delta_abs_vs_current` both negative)
- [ ] JSON artifact reloads and reproduces notebook scores to 1e-9 in a clean kernel
- [ ] Notebook states plainly that this is display-only and auto-pass coverage is 0%

---

## Acceptance Criteria
- [ ] All tasks completed
- [ ] All validation commands pass
- [ ] Walk-forward AUC in 0.68–0.75 (higher ⇒ investigate leakage)
- [ ] ECE < 0.10 after calibration
- [ ] Artifact persists `AGREE_TOL` and `MODEL_VERSION`
- [ ] `backend/tests` still 271 passed
- [ ] No `OUTPUT_COLS` / `MEMORY_COLS` field used as a feature

## Completion Checklist
- [ ] Follows NOTEBOOK_BOOTSTRAP and SCRIPT_MODULE_HEADER exactly
- [ ] Target imports `E.AGREE_TOL` — the literal `0.10` appears nowhere
- [ ] Self-check asserts agreement with `engine_statistical._agreement`
- [ ] No hardcoded month lists — derived from the data
- [ ] Notebook markdown states the display-only scope up front
- [ ] No scope additions beyond the NOT Building list

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Leakage via prior-cycle features** | **High** | **High** | Two-pass per-month loop; leakage unit test; AUC > 0.75 is a stop signal |
| Calibration drift between cycles | High | Medium | Per-cycle recalibration (owner decision); per-month table makes drift visible |
| ~~sklearn/lightgbm unavailable offline~~ | — | — | **Eliminated** — no new dependencies; numpy/scipy/matplotlib already installed |
| Hand-written numerics contain a bug | Low | Medium | `assert res.success` on the optimiser; unit tests recover known coefficients on synthetic data; `logaddexp` for overflow safety |
| Reviewers read the number as precision | Medium | Medium | Display cycle base rate alongside; notebook states 0% auto-pass coverage |
| Model looks weak (AUC ~0.7) and gets dropped | Medium | Low | Expectation set in advance; success criterion is calibration, not discrimination |
| `s20_payloads.pkl` stale after new ingestion | Medium | Medium | Notebook prints row count and month list; refresh via `s20_diverge_features.py` |

## Notes

- **Why a separate feature module rather than inline notebook code:** this codebase has already been
  bitten three times by one rule living in several places — the agreement rule existed in
  `engine_statistical`, `analysis/common.py` and `s19` simultaneously and drifted. `FEATURES` and the
  target must have exactly one definition so a future backend scoring path cannot diverge from what
  was trained.
- **Why no ML library at all:** the whole model is a 9-coefficient logistic regression. scipy's
  L-BFGS-B fits it, Platt scaling is the same fitter on one feature, and AUC/Brier/ECE are one-liners.
  Adding scikit-learn would introduce an install that `docs/ML_Implementation_Plan_TCB.md §4` flags as
  needing offline-mirror verification on the Intel network — and that stack already forced a pandas
  downgrade once via `statsforecast`. `analysis/` imports no ML library today; this keeps it that way.
- **Why JSON rather than a pickle:** the artifact is ~15 numbers. JSON diffs in git so a coefficient
  shift is visible in review, has no library-version coupling, and lets the backend score with one
  dot product instead of importing an ML stack into the API.
- **The prior-cycle feature already half-exists:** `engine_statistical._benchmark()` computes the
  comparison against the previous decision and then discards the distance. Persisting
  `|engine − prior decision|` on `recommendation_result` would supply this feature for free on every
  future cycle. Out of scope here; worth doing separately.
- **Four prior experiments** established every expectation in this plan. They currently exist only in
  session scratch and will be lost unless saved as `analysis/s21_autopass_feasibility.py`.
