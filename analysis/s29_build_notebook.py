"""Build and execute an inspectable companion to the offline SBA/TSB test."""
import json
import os
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/output/s29_sba_tsb"


def main():
    cells = []
    md = lambda text: cells.append(nbf.v4.new_markdown_cell(text))
    code = lambda text: cells.append(nbf.v4.new_code_cell(text))
    md("""# SBA and TSB: combined TCB history test

## tl;dr

**Keep the current rate estimator.** None of the 27 tested SBA/TSB variants
improved pooled matching. On 1,808 active/declining reviews, current-engine
agreement was **54.42%**, SBA **47.95%**, TSB **51.11%**, and the earlier proposal
(SBA active / TSB declining) **50.44%**. The best exploratory variant, 50% TSB
mixed with 50% current rate, reached **53.60%** and increased large undersizing.

The full combined panel contains 26,302 reviews across 14 observed cycles,
July 2024 through August 2026. These are irregular snapshots. No item-stockroom
pair has 12 consecutive review months; the longest observed run is three.
This is an **observed-snapshot proxy experiment**, not a valid monthly-demand
forecast validation. Production settings and the database were not changed.
""")
    md("""## Context & methods

The question is whether replacing the current cumulative-window demand rate
with SBA or TSB improves agreement with recorded engineer Max and ROP decisions.
We substitute only the daily rate: routing, lead times, service levels,
dispersion, rounding, dormant policy and current/prior anchoring are held fixed.

- **SBA:** smooth positive sizes and intervals between positive observations;
  forecast `(1 - alpha/2) * size / interval`.
- **TSB:** smooth positive sizes and the occurrence indicator; forecast
  `size * probability`. Probability updates on **every observed period,
  including zeros**; demand size updates only on positive demand.
- Primary settings: alpha = 0.1 for SBA; size alpha = occurrence beta = 0.1 for
  TSB. A fixed grid tests 0.05, 0.1, 0.2 and 0.3: four SBA and sixteen TSB settings.
- The earlier combined proposal uses SBA on active parts and TSB on declining
  parts. Six further variants blend each default rate with the current rate,
  or retain the baseline triple when a candidate creates a new zero Max/ROP or
  reduces a critical Max/ROP. Total: **27 variants**.
- Match requires both Max and ROP within **10% relative tolerance**. Zero must
  match zero. Min is checked for level ordering, but is not part of agreement.
- Large undersizing means Max or ROP below the engineer by more than
  `max(1 unit, 25% of that approved level)`. This is an error proxy, not an
  observed stockout. Erroneous zero levels and critical reductions are also checked.

References: [Nixtla's implementation](https://github.com/Nixtla/statsforecast/blob/main/python/statsforecast/models.py)
for the recurrences and first-observation initialization;
[rolling-origin evaluation](https://otexts.com/fpp3/tscv.html) for using prior
observations only. The Python implementation is local and unit-checked;
StatsForecast is not installed or run as a dependency.

### Key assumptions

1. Each available `last_30_day_cnsmptn_qty` is one observed model step. Missing
   review months are omitted, **never filled with zero or reconstructed by
   evenly distributing cumulative demand**. This compresses time and does not
   preserve actual calendar inter-arrival intervals; snapshot windows may overlap.
2. Rates are divided by 30 to get units/day. At least three observed snapshots,
   including the current one, are required. Below that, the current engine is used.
   Three is an exploratory minimum, not evidence of a mature model.
3. Prior demand belongs to the same item AND stockroom, comes from an earlier
   review cycle, and was available before the current cycle starts. Current demand
   windows are allowed because the baseline also uses them. Current approved
   quantities and comments are excluded from the prediction input.
4. Chronological selectors inspect only whole earlier cycles whose decisions are
   available. The model family and August data have been examined in this project;
   this is retrospective diagnostics, **not a fresh independent holdout**.
5. Matching engineer decisions does not establish forecast accuracy, savings,
   achieved fill rate or prevention of stockouts. Bootstrap intervals are descriptive
   and do not account for trying multiple methods or the proxy-series bias.
""")
    code("""from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display

ROOT = next(p for p in (Path.cwd(), *Path.cwd().parents) if (p / 'backend/app/engine_statistical.py').exists())
OUT = ROOT / 'analysis/output/s29_sba_tsb'
summary = json.loads((OUT / 'summary.json').read_text())
protocol = json.loads((OUT / 'protocol.json').read_text())
combined = pd.read_csv(OUT / 'scores_all_months.csv')
monthly = pd.read_csv(OUT / 'scores_by_cycle.csv')
coverage = pd.read_csv(OUT / 'observation_coverage.csv', dtype={'item_id': str, 'stockroom_id': str})
ranking = pd.read_csv(OUT / 'live_ranking.csv')
BASE = 'single_flat_prior1'
labels = {BASE: 'Current engine', 'sba_a0.10': 'SBA', 'tsb_a0.10_b0.10': 'TSB',
          'sba_active_tsb_dying': 'SBA active / TSB declining',
          'tsb_a0.10_b0.10_blend50': '50% TSB + 50% current'}
colors = ['#215fa6', '#c36120', '#7057a3', '#258170', '#72777d']
plt.rcParams.update({'figure.dpi': 125, 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False, 'axes.titlesize': 12})
assert not protocol['calendar_monthly_validation']
assert not summary['production_changed']
for path, expected in summary['unchanged_source_hashes'].items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
assert hashlib.sha256((ROOT / 'analysis/s29_sba_tsb_backtest.py').read_bytes()).hexdigest() == summary['script_sha256']
""")
    md("""## Data

Sources: frozen `analysis/output/s27_chronological/panel.pkl` and `config.json`,
derived from historical TCB reviews and `BOM table/BOM Review Archive 1.csv`.
The S27 source manifest, SQL and timestamp reconciliation are preserved. Its 59
unresolved, conflicting or invalid records remain excluded. No extra source rows
are dropped here; short histories use the baseline fallback.

The experiment actually applies SBA/TSB to **1,368 reviews (75.66% of active/declining)**.
The other 440 active/declining reviews use the current engine. All 24,494 dormant
reviews keep their existing policy output. Metrics below include those explicit
fallbacks; a separate table shows only rows where SBA/TSB was applied.
""")
    code("""data_checks = pd.DataFrame([
    ('Combined historical reviews', summary['review_rows']),
    ('Observed review cycles', summary['cycles']),
    ('Active / declining reviews', summary['active_dying_rows']),
    ('SBA/TSB attempted', summary['attempted_sba_tsb_rows']),
    ('Short-history baseline fallback', summary['fallback_live_rows']),
    ('Dormant policy unchanged', summary['dormant_rows']),
    ('Median observed snapshots per item-stockroom', summary['median_observations_per_pair']),
    ('Longest consecutive review-month run', summary['max_consecutive_review_months']),
    ('Live reviews with 12 consecutive months', summary['live_rows_with_12_consecutive_review_months'])
], columns=['Check', 'Value'])
display(data_checks)
display(coverage.groupby('_cycle').agg(reviews=('route', 'size'),
        candidate_attempts=('candidate_eligible', 'sum')).rename_axis('Review cycle'))
""")
    md("""## Results

Active and declining parts are the primary group because changing the rate cannot
improve the large dormant group under its fixed policy. For all 26,302 reviews,
agreement is 84.47% current, 84.02% SBA, 84.24% TSB and 84.19% combined.
""")
    code("""live = combined[combined.segment.eq('live')].set_index('candidate')
main = live.loc[list(labels)].copy()
main['gain_pp'] = main.match_pct - live.loc[BASE, 'match_pct']
display(main[['n', 'matches', 'match_pct', 'gain_pp', 'severe_under', 'zero_max_errors',
              'zero_rop_errors', 'critical_reductions_vs_baseline']].rename(index=labels).round(2))
fig, axes = plt.subplots(1, 2, figsize=(13, 4), gridspec_kw={'width_ratios': [1.4, 1]})
y = np.arange(len(main))
axes[0].barh(y, main.match_pct, color=colors, height=.65)
axes[0].set_yticks(y, list(labels.values()))
axes[0].invert_yaxis()
axes[0].set(xlim=(0, 100), xlabel='Max + ROP agreement (%)', title='1,808 active / declining reviews')
for i, value in enumerate(main.match_pct):
    axes[0].text(value + 1, i, f'{value:.2f}%', va='center')
axes[1].bar(y, main.severe_under, color=colors, width=.65)
axes[1].set_xticks(y, ['Current', 'SBA', 'TSB', 'Combined', '50% TSB'], rotation=25, ha='right')
axes[1].set(ylabel='Review rows', ylim=(0, 370), title='Large undersizing versus engineer')
for i, value in enumerate(main.severe_under):
    axes[1].text(i, value + 4, str(int(value)), ha='center')
fig.suptitle('Observed-snapshot proxy: no tested variant improves overall matching', fontsize=14)
fig.tight_layout()
fig.savefig(OUT / 'matching_comparison.png')
plt.show()
eligible = pd.read_csv(OUT / 'scores_eligible_only.csv')
display(eligible[eligible.segment.eq('live') & eligible.candidate.isin(list(labels)[:4])][
    ['candidate', 'n', 'matches', 'match_pct', 'severe_under']].round(2))
""")
    md("""On the 1,368 rows actually receiving the alternate estimator, current agreement
is **56.94%**, SBA **48.39%**, TSB **52.56%**, and the combination **51.68%**.
The loss therefore persists when baseline fallback rows are excluded.

The combined proposal gains 33 matches and loses 105 compared with current,
a net loss of 72. Its descriptive item-stockroom cluster bootstrap interval
for the change is **-5.94 to -2.16 percentage points**. Current is also better
within both active and declining groups.
""")
    code("""names = list(labels)[:4]
by_cycle = monthly[monthly.segment.eq('live') & monthly.candidate.isin(names)].pivot(
    index='cycle', columns='candidate', values='match_pct')
dates = pd.to_datetime(by_cycle.index + '-01')
fig, ax = plt.subplots(figsize=(12, 4.2))
for name, color, marker in zip(names, colors, ['o', 's', '^', 'D']):
    ax.plot(dates, by_cycle[name], color=color, marker=marker, label=labels[name], alpha=.9)
ax.set_xticks(dates, by_cycle.index, rotation=45, ha='right')
ax.set(ylim=(0, 100), ylabel='Max + ROP agreement (%)', xlabel='Observed review cycle',
       title='Combined-month result is not driven by a single latest cycle')
ax.legend(loc='upper left', ncol=2, frameon=False)
ax.grid(axis='y', alpha=.18)
fig.tight_layout()
fig.savefig(OUT / 'matching_by_cycle.png')
plt.show()
display(combined[combined.segment.isin(['active', 'dying']) & combined.candidate.isin(names)][
    ['candidate', 'segment', 'n', 'matches', 'match_pct', 'severe_under']].round(2))
""")
    md("""The combined default does not beat current in any cycle. Early equal scores
reflect little eligible history and baseline fallbacks. In the latest cycle,
August 2026, current agreement is **64.60%**, SBA **56.64%**, and TSB/combined
**59.29%**. This August result is descriptive because that cycle was already opened.

The grid and guards also fail to improve matching. The best raw SBA setting
reaches 49.45%; the best raw TSB setting reaches 52.27%. The strongest of all
27 candidates, 50% TSB plus 50% current, reaches 53.60%, while large undersizing
rises from 275 to 286. It also reduces 44 critical rows' levels versus baseline.
Guards improve the undersizing proxies but lose more matches. Both conservative
chronological selectors retain the baseline in every cycle.
""")
    code("""display(ranking[['candidate', 'match_pct', 'gain_pp', 'severe_under', 'zero_max_errors',
                 'zero_rop_errors', 'critical_reductions_vs_baseline', 'passes_error_checks']].head(10).round(2))
choices = pd.read_csv(OUT / 'walk_forward_choices.csv')
assert choices.selected.eq(BASE).all()
display(choices.groupby(['family', 'selected']).size().rename('Review cycles').reset_index())
""")
    md("""## Independent validation

Recompute agreement and error counts directly from saved row values with scalar
comparisons, separately from the vectorized scoring implementation. Check
identity, chronology, fallback behavior, dormant output and level ordering.
""")
    code("""pred = pd.read_csv(OUT / 'main_predictions.csv', dtype={'item_id': str, 'stockroom_id': str})
assert len(pred) == 26302 and not pred.duplicated(['item_id', 'stockroom_id', '_cycle']).any()
assert pred[['item_id', 'stockroom_id', '_cycle']].equals(coverage[['item_id', 'stockroom_id', '_cycle']])
prior = coverage[coverage.latest_prior_cycle.notna()]
assert prior.latest_prior_cycle.lt(prior._cycle).all()
assert prior.latest_prior_available_date.lt(prior._cycle + '-01').all()
records = pred[pred.route.isin(['active', 'dying'])].to_dict('records')
for name in summary['main_names']:
    hits = sum(all(abs(r[name + '_' + level] - r['_final_' + level]) <= .1 * abs(r['_final_' + level])
                   for level in ['max', 'rop']) for r in records)
    severe = sum(any(r['_final_' + level] - r[name + '_' + level] > max(1, .25 * r['_final_' + level])
                     for level in ['max', 'rop']) for r in records)
    assert hits == int(live.loc[name, 'matches'])
    assert severe == int(live.loc[name, 'severe_under'])
    for level in ['max', 'rop']:
        zeros = sum(r[name + '_' + level] == 0 and r['_final_' + level] > 0 for r in records)
        assert zeros == int(live.loc[name, 'zero_' + level + '_errors'])
    assert pred[name + '_max'].ge(pred[name + '_rop']).all()
    assert pred[name + '_rop'].ge(pred[name + '_min']).all()
    unchanged = ~pred.candidate_eligible
    for level in ['max', 'rop', 'min']:
        assert pred.loc[unchanged, name + '_' + level].equals(pred.loc[unchanged, BASE + '_' + level])
assert summary['rate_injection_parity_passed'] and summary['baseline_reconciles_s28']
print('Scalar agreement/error reconciliation, chronology, no duplicates, fallback parity and level ordering: passed.')
""")
    md("""## Takeaways

Keep the current cumulative-window engine. These tests do not support enabling
SBA, TSB, their proposed routing combination, or the tested blend/guard variants.

The added archive improves historical coverage, but **a year covered by reviews
does not provide a continuous year of observed monthly demand**. Among 1,368
eligible reviews, 688 have no positive consumption in any sampled 30-day window;
489 of those nevertheless report positive annual consumption. That illustrates
how snapshot sampling can miss demand; it is not a causal decomposition of the
matching losses.

A valid revisit needs dated issue transactions or complete per-period demand
totals by item and stockroom, including explicitly observed zeros, followed by
a future-cycle comparison. These results establish that the tested snapshot
approximation does not help matching; they do not establish that SBA/TSB could
never help with proper demand data.

Reproduce from the workspace with `.venv-ollama/Scripts/python.exe`:

```text
python analysis/s29_sba_tsb_backtest.py
python analysis/s29_build_notebook.py
```

Supporting outputs in `analysis/output/s29_sba_tsb/`: `protocol.json`,
`summary.json`, `scores_all_months.csv`, `scores_by_cycle.csv`,
`scores_eligible_only.csv`, `live_ranking.csv`, `observation_coverage.csv`,
`main_predictions.csv`, `walk_forward_choices.csv`, and this executed notebook.
The source notebook is saved without row-level outputs for version control.
""")
    notebook = nbf.v4.new_notebook(cells=cells, metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}})
    nbf.validate(notebook)
    nbf.write(notebook, ROOT / "analysis/s29_sba_tsb_backtest.ipynb")
    for variable in ("JUPYTER_RUNTIME_DIR", "JUPYTER_CONFIG_DIR", "IPYTHONDIR", "MPLCONFIGDIR"):
        target = OUT / variable.lower()
        target.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(target)
    executed = NotebookClient(notebook, timeout=120, kernel_name="python3",
                              resources={"metadata": {"path": str(ROOT)}}).execute()
    nbf.validate(executed)
    nbf.write(executed, OUT / "sba_tsb_test.executed.ipynb")
    code_cells = [c for c in executed.cells if c.cell_type == "code"]
    assert all(c.execution_count is not None for c in code_cells)
    assert not any(o.output_type == "error" for c in code_cells for o in c.outputs)
    print(json.dumps({"executed_code_cells": len(code_cells), "validation": "passed"}))


if __name__ == "__main__":
    main()
