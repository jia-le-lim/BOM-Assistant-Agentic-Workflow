# TCB chronological calibration — 11 September 2026

**Decision: retain the current production settings.** The guarded wider-anchor
candidate improved development agreement slightly, then failed the independent
August 2026 evaluation. No engine defaults, database rows or configuration were changed.

## Source and chronology

- Read 26,361 historical TCB reviews from the local application's Supabase database.
- Resolved review cycles from the archive's `bom_review_cylce`, or legacy monthly
  filenames when that field was absent. The 14 cycles span July 2024–August 2026.
- The old January 2026 batch carried August 2025 review timestamps. Reconciled
  2,711 records to the cycle-tagged archive only when all three approved quantities
  agreed. Excluded 56 records without a matching archive record, one conflicting
  decision, and two records with invalid consumption after reconciliation.
- The resulting evaluation panel has **26,302 rows**. The repair is local to the
  experiment; the original database timestamps and payloads remain intact.
- Priors use **item + stockroom**, strictly earlier review cycles, and decisions
  available before the prediction cycle begins. Current-cycle labels and reviewer
  text cannot enter the model-input whitelist.

## Experiment

The baseline is `stat-v2`, single-window demand, no trend adjustment,
`continuity_snap=1`, `prior_anchor_policy="demand"`. All methods use the same frozen
current configuration, confirmed rules and service levels. Historical stored
recommendations were not used as predictions because their engine versions differ.

The first grid contained 28 configurations: single/blended demand; trend off/on;
anchors off, current level only, prior/current at one or two units; and prior-age
and demand-drift guards. Development data exposed extra incorrect zero levels in
the wider-anchor leaders. Before opening August, three additional variants added
a veto that restores the baseline's complete level set when a candidate newly
zeros Max/ROP or lowers a critical part's Max/ROP. Both development rounds are saved.

Selection uses six development cycles, June 2025–May 2026, with equal cycle weights
on active/dying rows. A candidate must also avoid increasing large underestimates,
erroneous zero Max/ROP, or critical-part reductions versus baseline, and win at
least half the cycles. The supplementary expanding-origin selector uses earlier
cycles only. Since the guard design used development results, this table is an
internal diagnostic; August is the independent final test.

**Agreement** means both Max and ROP are within 10% of the engineer's values, with
no one-unit tolerance floor. A zero target must match zero. **Large underestimate**
means either level is below the engineer by more than `max(1 unit, 25% of target)`.
These are decision-agreement/error proxies, not measurements of actual stockouts.

## Results

Development selected **single-window, no trend, two-unit prior/current anchoring
with the positive-level and criticality veto**. It won five of six cycles.

| Development metric, active/dying | Current engine | Selected candidate |
|---|---:|---:|
| Mean cycle agreement | 58.97% | 59.81% |
| Pooled agreement, 1,064 rows | 58.18% | 59.02% |
| Large underestimates | 136 | 126 |
| Incorrect zero Max | 70 | 70 |
| Incorrect zero ROP | 129 | 125 |

Blended demand with the current anchor reduced agreement in all six development
cycles. Final selection was frozen before August evaluation.

| August 2026, 226 active/dying rows | Matches | Agreement | Large underestimates | Incorrect zero Max | Incorrect zero ROP |
|---|---:|---:|---:|---:|---:|
| Current engine | 146 | 64.60% | 17 | 1 | 18 |
| Selected guarded wider anchor | 144 | 63.72% | 18 | 1 | 14 |
| Hold current | 174 | 76.99% | 8 | 1 | 12 |
| Last approved | 128 | 56.64% | 16 | 8 | 16 |

The selected candidate's paired gain is **−0.88 percentage points**, with a 95%
paired bootstrap interval of **[−2.21, 0.00]** (4,000 resamples of unique
item–stockroom pairs). It fails both the improvement and large-underestimate checks.

On all 2,865 eligible August rows, agreement is 85.20% for the baseline and 85.13%
for the selected candidate. The 2,639 dormant rows are unchanged by these levers.
Their high agreement must not be substituted for active/dying sizing performance.

Holding current levels is a useful benchmark and a promising subject for a new
policy experiment. It is not selected for deployment: it increases zero-level
errors over development and reduces levels versus the engine on four critical
August parts. Repeating the last approved decision reduces levels on five.

## Limits and next decision

Keep current defaults. Do not implement the selected configuration by simply
setting the snap band to two; the experiment also requires its explicit veto,
and even that complete candidate failed the final test.

For a separate experiment, prioritize explicit maintain-current exceptions and
dormant stocking policies. Use a **new future cycle** for independent validation;
August cannot become unseen again. Actual issue, replenishment and shortage
records are still needed to validate fill rate, stockouts or economic savings.
Results describe a rotating review roster, not every TCB stocking position.

## Reproduction and evidence

- [Experiment implementation](../../analysis/s27_chronological_calibration.py)
- [Notebook source](../../analysis/s27_chronological_calibration.ipynb)
- [Executed notebook](../../analysis/output/s27_chronological/backtest.executed.ipynb)
- [Validation tests](../../analysis/tests/test_s27_chronological.py)
- [Source manifest](../../analysis/output/s27_chronological/source_manifest.json)
- [Frozen selection](../../analysis/output/s27_chronological/selection.json)
- [Final test summary](../../analysis/output/s27_chronological/holdout_summary.json)

Source payloads, SQL, exclusions, both development rounds, row predictions, CSV
scorecards and chart images are in ignored `analysis/output/s27_chronological/`.
The executed notebook recomputes key results with scalar checks and verifies
monotonic quantities, unique keys and prior chronology. Eight focused regression
tests pass; all notebook code cells execute successfully and three charts were
visually inspected.

```powershell
.venv-ollama/Scripts/python.exe analysis/s27_chronological_calibration.py fetch --output analysis/output/s27_reproduction
.venv-ollama/Scripts/python.exe analysis/s27_chronological_calibration.py tune --output analysis/output/s27_reproduction
.venv-ollama/Scripts/python.exe analysis/s27_chronological_calibration.py holdout --output analysis/output/s27_reproduction
.venv-ollama/Scripts/python.exe -m pytest analysis/tests/test_s27_chronological.py -q
```

This uses the current local database when fetching; source/engine/config hashes
identify the exact experiment. Saved notebook results use the frozen snapshot.
Optional authoring dependencies are matplotlib, nbformat, nbclient and ipykernel;
they were installed into the project virtual environment for this run.
