"""Build and execute the S27 companion from frozen, local experiment outputs."""
import os
from pathlib import Path

import nbformat as nbf
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis/output/s27_chronological"


def main():
    cells = []
    md = lambda source: cells.append(nbf.v4.new_markdown_cell(source))
    code = lambda source: cells.append(nbf.v4.new_code_cell(source))
    md("""# TCB chronological sizing backtest

The wider-anchor candidate did **not** pass the final August 2026 test. Keep the
current engine settings. On 226 active/dying rows, the current engine matched
146 engineer decisions (64.60%); the candidate matched 144 (63.72%). Large
underestimates increased from 17 to 18. Production was not changed.

This notebook is an executed companion to `analysis/s27_chronological_calibration.py`.
It reads frozen local evidence; it never connects to or writes to the database.
""")
    md("""## Context and methods

We tested cumulative-window demand estimation, trend adjustment, and current/prior
decision anchoring. The target is the engineer's recorded **Max and ROP**, both
within a strict 10% relative tolerance. Zero must match zero. This measures
decision agreement, not achieved fill rate or stockout prevention.

Development covers June 2025 through May 2026 (six observed cycles). Earlier
cycles supply history and walk-forward warmup. August 2026 was reserved for
the final evaluation; the candidate was selected and hashed before opening it.
There were 28 initial configurations, followed by three guards designed using
development errors only. Both rounds' protocols and selections are retained.

### Assumptions and limits

- Historical identity is **item + stockroom + review cycle**, not item alone.
- Priors must come from an earlier cycle and be available before the next cycle starts.
- January 2026 records dated August 2025 are reconciled to the cycle-tagged archive
  only when all three engineer-approved quantities agree. Unresolved/conflicting
  records and invalid consumption rows are excluded from both scoring and priors.
- Current-cycle engineer answers, comments, adoption and modification fields are
  absent from the model input whitelist.
- All variants use the same frozen current engine, confirmed rules and service
  levels. This is a retrospective evaluation, not a recreation of historical software.
- Active/dying rows form the primary evaluation group. Dormant results are reported
  separately because many correct zero/zero matches can conceal sizing errors.
- Hold-current and last-approved comparators fall back to a valid current level,
  then the engine, when the required level set is unavailable or inconsistent.
- The positive-level guard preserves the **baseline's full level set** if the wider
  anchor would newly zero Max/ROP or reduce a critical part's Max/ROP. Setting only
  `continuity_snap=2` does not implement this guard.
- Sampling uncertainty is conditional on this review roster; only one final cycle
  is held out. These records do not establish causal inventory savings or stockouts.
""")
    code("""from pathlib import Path
import json, sys, hashlib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display

ROOT = next(p for p in (Path.cwd(), *Path.cwd().parents) if (p / 'backend/app/engine_statistical.py').exists())
OUT = ROOT / 'analysis/output/s27_chronological'
sys.path.insert(0, str(ROOT / 'analysis'))
from s27_chronological_calibration import BASELINE
manifest = json.loads((OUT / 'source_manifest.json').read_text())
selection = json.loads((OUT / 'selection.json').read_text())
summary = json.loads((OUT / 'holdout_summary.json').read_text())
development = pd.read_csv(OUT / 'development_scores.csv')
ranking = pd.read_csv(OUT / 'development_ranking.csv')
holdout = pd.read_csv(OUT / 'holdout_scores.csv')
walk = pd.read_csv(OUT / 'walk_forward.csv')
panel = pd.read_pickle(OUT / 'panel.pkl')
assert hashlib.sha256((OUT / 'panel.pkl').read_bytes()).hexdigest() == manifest['panel_sha256']
assert hashlib.sha256((OUT / 'selection.json').read_bytes()).hexdigest() == summary['selection_sha256']
assert not selection['holdout_used_for_selection']
assert not summary['production_changed']
plt.rcParams.update({'figure.dpi': 125, 'font.size': 10, 'axes.spines.top': False,
                     'axes.spines.right': False, 'axes.titlesize': 13})
BLUE, ORANGE, GRAY, PURPLE = '#2364aa', '#c35b16', '#66717e', '#7d54a6'
""")
    md("""## Data and timestamp reconciliation

Source: the configured local Supabase application's historical TCB reviews,
joined to their frozen BOM payloads, plus `BOM table/BOM Review Archive 1.csv` for
January reconciliation. The SQL, source hashes, configuration and exclusion lists
are saved alongside this notebook. Dates on the chart are observed review cycles;
gaps are not zero-demand months.
""")
    code("""quality = pd.DataFrame([
    ('Historical reviews read', manifest['source_rows']),
    ('Reconciled to archive', manifest['timestamp_resolution']['archive_reconciled']),
    ('Unresolved timestamps excluded', manifest['timestamp_resolution']['unresolved_timestamp']),
    ('Conflicting engineer decisions excluded', manifest['timestamp_resolution']['conflicting_archive_decision']),
    ('Invalid consumption after reconciliation excluded', manifest['quarantine_after_reconciliation']),
    ('Eligible reviews', manifest['eligible_rows'])], columns=['Check', 'Rows'])
display(quality)
counts = panel.groupby('_cycle').size()
dates = pd.to_datetime(counts.index + '-01')
fig, ax = plt.subplots(figsize=(11.8, 3.8))
ax.bar(dates, counts, width=20, color=[ORANGE if c == '2026-08' else BLUE for c in counts.index])
ax.set_xticks(dates, counts.index, rotation=45, ha='right')
ax.set(ylabel='Eligible historical reviews', xlabel='Observed review cycle',
       title='14 irregular review cycles; August 2026 is the final test')
ax.set_ylim(0, counts.max() * 1.15)
ax.grid(axis='y', alpha=.18)
fig.tight_layout()
fig.savefig(OUT / 'coverage.png')
plt.show()
""")
    md("""## Development results

The primary selection statistic gives each development cycle equal weight.
Eligibility also requires no increase in large underestimates or erroneous zero
levels, no reductions of critical-part Max/ROP relative to baseline, and wins in
at least half the cycles. These conservative error proxies are experimental
selection rules, not validated service-level guarantees.

Demand blending with the current anchor reduced agreement in every development
cycle. The selected guarded wider anchor gained 0.84 percentage points in mean
cycle agreement. The initial unguarded winners failed the zero-level checks.
""")
    code("""chosen = selection['selected']['name']
names = {BASELINE: 'Current engine', chosen: 'Guarded wider anchor',
         'blended_flat_prior1': 'Blended demand', 'hold_current': 'Hold current'}
subset = development[(development.segment == 'live') &
                     development.cycle.isin(selection['development_cycles'])]
comparison = subset[subset.candidate.isin(names)].pivot(index='cycle', columns='candidate', values='match_pct')
display(ranking[ranking.candidate.isin(names)][['candidate', 'macro_match_pct', 'pooled_match_pct',
       'wins', 'severe_under', 'zero_max_errors', 'zero_rop_errors', 'eligible']].round(2))
fig, ax = plt.subplots(figsize=(10.5, 4.1))
for (key, label), color in zip(names.items(), [BLUE, ORANGE, PURPLE, GRAY]):
    ax.plot(pd.to_datetime(comparison.index + '-01'), comparison[key], marker='o', label=label, color=color)
ax.set_xticks(pd.to_datetime(comparison.index + '-01'), comparison.index)
ax.set(ylim=(0, 100), ylabel='Max + ROP agreement (%)', xlabel='Development review cycle',
       title='Small development gain from wider anchoring; blending underperforms')
ax.legend(loc='lower left', ncol=2, frameon=False)
ax.grid(axis='y', alpha=.18)
fig.tight_layout()
fig.savefig(OUT / 'development.png')
plt.show()
display(walk.round(2))
""")
    md("""The expanding-origin selector chose configurations using only earlier cycles.
The guard family itself was designed after reviewing development results, so this
walk-forward table is an internal development diagnostic. **August is the only
test that remained untouched throughout method design.**
""")
    md("""## Final August 2026 evaluation

Both the paired agreement result and the large-underestimate check fail to support
promotion. A large underestimate means Max or ROP is below the engineer by more
than the larger of one unit or 25% of that approved quantity.

The hold-current comparator matches more engineer decisions in this cycle, but
it reduced four critical-part level sets relative to the engine and had more
zero-level errors in development. Its result does not justify automatic approval.
""")
    code("""labels = {BASELINE: 'Current engine', 'selected': 'Guarded wider anchor',
          'hold_current': 'Hold current', 'last_approved': 'Last approved'}
live = holdout[holdout.segment.eq('live')].set_index('candidate').loc[list(labels)].copy()
display(live[['n', 'matches', 'match_pct', 'max_mae', 'rop_mae', 'severe_under',
              'zero_max_errors', 'zero_rop_errors', 'critical_reductions_vs_baseline']].round(2))
fig, axes = plt.subplots(1, 2, figsize=(12, 4.1), gridspec_kw={'width_ratios': [1.3, 1]})
colors = [BLUE, ORANGE, GRAY, PURPLE]
y = np.arange(len(live))
axes[0].barh(y, live.match_pct, color=colors, height=.6)
axes[0].set_yticks(y, labels.values())
axes[0].invert_yaxis()
axes[0].set(xlim=(0, 100), xlabel='Max + ROP agreement (%)', title='August: 226 active/dying reviews')
for i, (_, r) in enumerate(live.iterrows()):
    axes[0].text(r.match_pct + 1, i, f'{r.match_pct:.1f}%', va='center', fontsize=9)
axes[1].bar(y, live.severe_under, color=colors, width=.6)
axes[1].set_xticks(y, ['Current', 'Candidate', 'Hold', 'Previous'])
axes[1].set(ylabel='Rows', title='Large underestimates versus engineer', ylim=(0, max(live.severe_under) + 5))
for i, value in enumerate(live.severe_under):
    axes[1].text(i, value + .4, str(int(value)), ha='center')
fig.tight_layout()
fig.savefig(OUT / 'holdout.png')
plt.show()
print(f"Paired candidate gain: {summary['gain_pp']:.2f} percentage points; "
      f"95% interval {summary['paired_gain_95pct_interval_pp']}.")
display(holdout[holdout.candidate.isin([BASELINE, 'selected'])][
    ['candidate', 'segment', 'n', 'matches', 'match_pct', 'severe_under']].round(2))
""")
    md("""## Independent checks

Recompute agreement directly from saved row predictions using scalar comparisons,
separately from the vectorized scorecard. Verify zero-level and large-error counts,
level ordering, unique historical keys, and strictly earlier/available priors.
""")
    code("""pred = pd.read_csv(OUT / 'holdout_predictions.csv', dtype={'item_id': str, 'stockroom_id': str})
for name, group in pred.groupby('candidate'):
    live_group = group[group.route.isin(['active', 'dying'])]
    # to_dict preserves underscore-prefixed column names, unlike itertuples.
    hits = sum(all(abs(r['predicted_' + level] - r['_final_' + level]) <= .10 * abs(r['_final_' + level])
                   for level in ['max', 'rop']) for r in live_group.to_dict('records'))
    assert hits == int(live.loc[name, 'matches'])
    severe = sum(any(r['_final_' + l] - r['predicted_' + l] > max(1, .25 * r['_final_' + l])
                     for l in ['max', 'rop']) for r in live_group.to_dict('records'))
    assert severe == int(live.loc[name, 'severe_under'])
    for level in ['max', 'rop']:
        zeros = sum(r['predicted_' + level] == 0 and r['_final_' + level] > 0
                    for r in live_group.to_dict('records'))
        assert zeros == int(live.loc[name, 'zero_' + level + '_errors'])
    assert (group.predicted_max >= group.predicted_rop).all()
    assert (group.predicted_rop >= group.predicted_min).all()
assert not panel.duplicated(['item_id', 'stockroom_id', '_cycle']).any()
with_prior = panel[panel['_prior_cycle'].ne('')]
assert with_prior['_prior_cycle'].lt(with_prior['_cycle']).all()
assert with_prior['_prior_available_date'].lt(with_prior['_cycle'] + '-01').all()
assert manifest['source_rows'] - manifest['eligible_rows'] == 59
assert not summary['promotion_supported']
print('Independent agreement/error checks, monotonicity, uniqueness and prior chronology: passed.')
""")
    md("""## Takeaways and reproduction

1. Keep the existing single-window estimator and one-unit anchoring band.
2. Do not enable blending, trend adjustment or the tested wider anchor on these results.
3. Historical continuity is useful, but the strongest remaining signal is often
   maintaining the current stocking policy. Study explicit exceptions and dormant
   policy rules in a **new** experiment, with a new future holdout.
4. The January timestamp/source repair belongs to this evaluation panel. Original
   production timestamps and quantities were not changed.

Reproduce from repository root with the project Python environment:

```powershell
.venv-ollama/Scripts/python.exe analysis/s27_chronological_calibration.py fetch --output analysis/output/s27_reproduction
.venv-ollama/Scripts/python.exe analysis/s27_chronological_calibration.py tune --output analysis/output/s27_reproduction
.venv-ollama/Scripts/python.exe analysis/s27_chronological_calibration.py holdout --output analysis/output/s27_reproduction
.venv-ollama/Scripts/python.exe -m pytest analysis/tests/test_s27_chronological.py -q
```

A reproduction does not make August unseen again. Future tuning needs a later
test cycle. The notebook itself can be rerun safely from its frozen local files.

Evidence in `analysis/output/s27_chronological/`: `source_query.sql`,
`source_manifest.json`, `config.json`, `timestamp_resolution.csv`,
`quarantine_exclusions.csv`, both protocol/selection rounds, development and
walk-forward scorecards, held-out row predictions and `holdout_summary.json`.
The source dataset and row-level exports are local and ignored by Git.
""")
    notebook = nbf.v4.new_notebook(cells=cells, metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}})
    nbf.validate(notebook)
    source = ROOT / "analysis/s27_chronological_calibration.ipynb"
    nbf.write(notebook, source)
    for variable, folder in (("JUPYTER_RUNTIME_DIR", "jupyter-runtime"),
                             ("JUPYTER_CONFIG_DIR", "jupyter-config"),
                             ("IPYTHONDIR", "ipython"), ("MPLCONFIGDIR", "matplotlib")):
        path = OUT / folder
        path.mkdir(exist_ok=True)
        os.environ[variable] = str(path)
    client = NotebookClient(notebook, timeout=120, kernel_name="python3", resources={"metadata": {"path": str(ROOT)}})
    client.execute()
    nbf.validate(notebook)
    executed = OUT / "backtest.executed.ipynb"
    nbf.write(notebook, executed)
    assert all(c.execution_count is not None for c in notebook.cells if c.cell_type == "code")
    assert not any(o.output_type == "error" for c in notebook.cells if c.cell_type == "code" for o in c.outputs)
    print(f"Executed notebook: {executed}")


if __name__ == "__main__":
    main()
