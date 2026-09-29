# Implementation Report: Confidence Scoring Model for BOM Review

## Summary

A calibrated acceptance-probability model for live BOM rows: given a scored cycle, it estimates
the probability the engineer accepts the engine's Max/ROP. Delivered as a feature+model module,
a training notebook, and a JSON artifact. **Display only** — nothing gates, sorts or
auto-approves. No dependency added; the whole model is numpy/scipy.

Every number the plan predicted in advance reproduced exactly on the real data, which is the
strongest evidence available that the target and the leakage controls were built as specified.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Files created | 3 | 4 (added `analysis/tests/conftest.py`) |
| Dependencies added | 0 | 0 |
| Walk-forward AUC | 0.70–0.72 (band 0.68–0.75) | **0.7179** |
| Base acceptance rate (live) | 30.9% | **30.9%** (711 rows) |
| `has_prior` coverage | ~71% | **71.0%** |
| ECE after calibration | < 0.10 | **0.0350** (from 0.0713 raw) |
| Backend suite | 271 passed | **271 passed** |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Feature module | Complete | `FEATURES`, `build()`, two-pass prior history, `_selfcheck()` |
| 2 | Notebook cells 0–3 (data & target) | Complete | Per-month base rates reproduce the plan's series exactly |
| 3 | Model + metrics helpers | Complete | Deviated — stable sigmoid, see below |
| 4 | Walk-forward evaluation | Complete | Pooled AUC 0.7179, asserted inside 0.68–0.75 |
| 5 | Calibration | Complete | Platt on out-of-fold log-odds; ECE 0.071 → 0.035 |
| 6 | Coefficients + JSON persistence | Complete | Sign asserts pass; artifact reproduces notebook to 1e-9 |
| 7 | Tests | Complete | 5 tests; needed a `conftest.py` for the import path |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Dependencies present | Pass | `numpy, pandas, scipy, matplotlib` → `all present` |
| Feature module self-check | Pass | 9,054 rows / 711 live / 30.9% base / 71.0% `has_prior` |
| Unit tests | Pass | `python -m pytest analysis/tests -q` → 5 passed |
| Backend suite unaffected | Pass | `python -m pytest backend/tests -q` → **271 passed** |
| Notebook execution | Pass | All 10 code cells, all 5 in-notebook asserts — see deviation 3 |
| Static analysis | N/A | No linter or type-checker configured in this repo (no ruff/flake8/mypy, no config file) |
| Edge cases | Pass | Cold start, prior `final_max = 0`, first month skipped, thin folds, null encodings |

### Manual validation

- Reliability diagram sits on the diagonal in the three populated bins (n=169/341/164);
  the two thin bins (n=3, n=13) wander and are annotated with their counts and called out in
  the notebook markdown as noise rather than a finding.
- Per-month table shows the drift plainly: 2025-03 over-predicted **+8.6pp**,
  2026-01 under-predicted **−16.5pp**. This is the evidence for per-cycle recalibration.
- Coefficient signs: `gap_prior_decision` **−0.846**, `delta_abs_vs_current` **−1.662** — both
  negative as measured, and both asserted in the notebook.
- JSON artifact reloads in a cold namespace and reproduces the notebook's five sample scores
  to 1e-9.
- The notebook's first cell states display-only scope and the 0% auto-pass coverage.

## Files Changed

| File | Action | Lines |
|---|---|---|
| `analysis/s21_confidence_features.py` | CREATED | +328 |
| `analysis/s21_confidence_model.ipynb` | CREATED | 20 cells / 207 source lines |
| `analysis/tests/test_s21_features.py` | CREATED | +77 |
| `analysis/tests/conftest.py` | CREATED | +7 |

Outputs written (not source, all under `analysis/output/`):
`s21_confidence_model.json` (1.4 KB), `s21_calibration.csv`, `s21_reliability.png`.

No existing file was modified. `engine_statistical.py` stayed read-only as the plan requires.

## Deviations from Plan

1. **`analysis/tests/conftest.py` added (4th file).** WHY: `analysis/` had no test directory and
   no packaging, so pytest could not resolve `import s21_confidence_features`. Seven lines
   putting `analysis/` on `sys.path`, mirroring how the notebooks reach the same modules.

2. **Gradient uses `scipy.special.expit` instead of `1 / (1 + np.exp(-z))`.** WHY: the plan's
   `logaddexp` guard protects the *loss*, but the literal gradient expression still raised
   `RuntimeWarning: overflow encountered in exp` on the plan's own extreme-magnitude edge case
   (z ≈ 1e3). `expit` is the same function computed stably, from a library already imported.
   The plan's intent — finite loss and gradient at extreme scale — is met more completely.

3. **Notebook validated by executing its code cells, not by `nbconvert`.** WHY: `nbconvert`,
   `nbformat` and `nbclient` are not installed in this venv (only `ipykernel`/`jupyter_client`,
   i.e. VS Code's notebook path), and the plan forbids adding dependencies. Substituted: each
   code cell compiled and executed in order in one namespace under the Agg backend. Every
   assert ran and passed and all three output files were written. **Consequence:** the committed
   `.ipynb` has no stored cell outputs — run it once in VS Code to populate them.

4. **`gap_prior_decision` defined as a relative gap**, `|engine_max − prior_final_max| / max(|prior|, 1)`.
   WHY: the plan named the feature and its cold-start encoding but not its scale. Relative
   matches the relative 10% target rule and reproduces the plan's own measured anchor — 58.3%
   acceptance at zero gap. Absolute did not reproduce it.

5. **Two helpers added to the module beyond the plan's listing**: `standardise()` (so the
   training-fold-only rule has one definition instead of being retyped in the loop) and
   `reliability()` (the diagram's numbers, reused by the plot and the printed table).
   `load_payloads()` also holds the missing-pickle instruction the plan asked cell 3 to carry,
   for the same single-definition reason.

## Issues Encountered

- **Bash heredocs truncate past roughly 160 lines in this environment**, twice producing
  `unexpected EOF while looking for matching '`. Resolved by writing longer files with the Write
  tool and keeping shell commands short. No impact on the delivered code.
- **`frequencymonthswithusage` is 86% null across all rows** but only **4.2% null on live rows**;
  `days_since_last_issue` is 66% null overall and **0% null on live rows**. The plan's null rates
  describe the full population including dormant. The `-1` encoding is retained regardless, as
  specified.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `analysis/tests/test_s21_features.py` | 5 | Target vs `engine_statistical._agreement` on all four scorecard boundaries; prior-cycle leakage (both the positive and the negative assertion); prior `final_max = 0` division guard; PRD 5.1 forbidden-column check; module `_selfcheck` (coefficient signs, `logaddexp` overflow, AUC, ECE) |

## Not Built (deliberate, per plan's NOT Building)

No backend wiring, no frontend change, no gating or ranking, no isotonic calibration, no tree
model, no new dependencies, no SFM or item-level match-rate features.

One plan **Note** is not covered by any task and remains open: the four prior experiments that
established these expectations exist only in session scratch and were to be saved as
`analysis/s21_autopass_feasibility.py`. It is absent from Files to Change and from the task
list, so it was not built. Worth doing separately before the context is lost.

## Next Steps

- [ ] Run the notebook once in VS Code to store cell outputs
- [ ] Code review via `/code-review`
- [ ] Save the four feasibility experiments (`s21_autopass_feasibility.py`) before they are lost
- [ ] Separately: persist `|engine − prior decision|` on `recommendation_result` so
      `gap_prior_decision` comes for free on every future cycle
