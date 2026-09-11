# SBA/TSB test on all combined TCB history

**Decision: keep the current estimator.** None of 27 tested SBA/TSB variants
improved pooled matching. This is an exploratory test using observed snapshots;
the archive does not contain a continuous monthly demand series. Production
code, configuration and database records were not changed by this experiment.

## Matching results

Source: the frozen S27 panel, 26,302 eligible historical reviews across 14 cycles
from July 2024 through August 2026. Primary evaluation: 1,808 active/declining
reviews, with each item-stockroom-cycle weighted equally. A match requires both
Max and ROP within 10% relative tolerance of the engineer; zero requires zero.

| Estimator | Matching reviews | Match rate | Change | Large undersizing |
|---|---:|---:|---:|---:|
| Current cumulative-window engine | 984 / 1,808 | **54.42%** | Reference | 275 |
| SBA, alpha 0.1 | 867 / 1,808 | 47.95% | -6.47 pp | 275 |
| TSB, size/probability alpha 0.1 | 924 / 1,808 | 51.11% | -3.32 pp | 316 |
| Earlier proposal: SBA active / TSB declining | 912 / 1,808 | 50.44% | -3.98 pp | 287 |
| Best tested variant: 50% TSB + 50% current rate | 969 / 1,808 | 53.60% | -0.83 pp | 286 |

Large undersizing means either level below the approved quantity by more than
the larger of one unit or 25% of that quantity. These are review errors, not
observed stockouts. The best variant also lowered 44 critical rows' Max/ROP
relative to the current engine, so its error checks fail.

SBA/TSB was actually applied to 1,368 reviews with at least three observed
snapshots; 440 shorter histories retained the current engine. On those 1,368
attempted rows alone, match rates were 56.94% current, 48.39% SBA, 52.56% TSB and
51.68% combined. Dormant policy outputs were identical across all candidates.
Across all 26,302 reviews, rates were 84.47%, 84.02%, 84.24% and 84.19%, respectively.

The combined proposal gained 33 matches and lost 105. The paired cluster bootstrap
interval for its net change was -5.94 to -2.16 percentage points (4,000 resamples
of 387 item-stockroom pairs). This interval is descriptive and does not adjust
for model selection, calendar gaps or roster selection.

The current engine also matched better within both active and declining groups.
In August 2026, current matching was 64.60%, SBA 56.64%, and TSB/combined 59.29%.
August had already been examined in prior work, so it is not a fresh holdout.

## Test design and limits

- Four SBA smoothing settings and sixteen TSB settings used 0.05, 0.1, 0.2 and
  0.3. One default route combination and six rate-blending/level-guard variants
  brought the total to 27. Best raw SBA reached 49.45%; best raw TSB reached 52.27%.
- Only `_estimate_mu` was temporarily substituted inside the offline Python
  process. Original windows still determined routing and dispersion. Lead times,
  service levels, quantity rounding, dormant rules and anchoring were fixed.
- Prior observations required the same item and stockroom, an earlier cycle,
  and recorded availability before the next cycle began. Current demand windows
  were allowed, as in the baseline; current approved quantities were excluded.
- The demand series used available `last_30_day_cnsmptn_qty` snapshots. Each
  observation was treated as one model step and its forecast divided by 30.
  Missing months were omitted, never zero-filled. This compresses calendar time;
  windows may overlap. It is a sensitivity approximation, not valid monthly SBA/TSB.
- Chronological parameter selectors used only whole earlier cycles with available
  decisions, requiring improved agreement and no worse error proxies. Both retained
  the current engine in every cycle. This remains retrospective analysis.

SBA smooths positive demand sizes and intervals, with the `(1 - alpha/2)` bias
correction. TSB smooths positive sizes and demand occurrence probability. **TSB
updates probability on observed zero-demand periods too**; only its size update
requires a positive observation. These recurrences and initialization follow
[Nixtla's reference implementation](https://github.com/Nixtla/statsforecast/blob/main/python/statsforecast/models.py).
The local implementation avoids adding a StatsForecast dependency. Chronological
evaluation follows the prior-data principle in
[Forecasting: Principles and Practice](https://otexts.com/fpp3/tscv.html).

## What the additional history supports

The median item-stockroom has nine observed snapshots, maximum twelve. The longest
run of consecutive review months is only three. No pair has twelve consecutive
monthly observations. Most gaps are two or three months.

Of 1,368 attempted rows, 688 have no positive demand in any sampled 30-day window;
489 of those nevertheless have positive annual consumption. This demonstrates
that snapshot sampling can miss consumption; it does not explain every matching
loss or prove that SBA/TSB cannot work with suitable data.

A valid next test requires dated issue transactions or complete per-period
consumption by item and stockroom, including observed zeros, then evaluation on
future reviews. Review matching alone cannot establish forecast accuracy,
inventory service or savings. The existing cumulative-window estimator remains
the supported choice on this evidence.

## Evidence and validation

- [Executed notebook](../../analysis/output/s29_sba_tsb/sba_tsb_test.executed.ipynb)
- [Main comparison](../../analysis/output/s29_sba_tsb/main_comparison.csv)
- [All 27 variants](../../analysis/output/s29_sba_tsb/live_ranking.csv)
- [Results by cycle](../../analysis/output/s29_sba_tsb/scores_by_cycle.csv)
- [Observation coverage](../../analysis/output/s29_sba_tsb/observation_coverage.csv)
- [Row predictions](../../analysis/output/s29_sba_tsb/main_predictions.csv)
- [Protocol](../../analysis/output/s29_sba_tsb/protocol.json)
- [Analysis code](../../analysis/s29_sba_tsb_backtest.py)
- [Tests](../../analysis/tests/test_s29_sba_tsb.py)

All 19 relevant tests passed (nine new forecasting/chronology/guard tests and ten
existing S27/S28 tests). Six notebook code cells executed successfully. Independent
scalar checks reconciled match and error counts, key uniqueness, chronological
history, fallback behavior and quantity ordering. Rate substitution with the
original rates reproduced baseline outputs exactly, and baseline metrics matched
the earlier S28 results. Both charts were visually inspected. Frozen source,
configuration and engine hashes remained unchanged.

Reproduce with `.venv-ollama/Scripts/python.exe`:

```text
python analysis/s29_sba_tsb_backtest.py
python analysis/s29_build_notebook.py
```
