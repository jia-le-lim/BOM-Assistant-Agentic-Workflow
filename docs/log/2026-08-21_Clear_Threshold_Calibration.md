# Clear-Threshold Calibration (S17) — 2026-08-21

Harness: `analysis/s17_clear_threshold_calibration.py`
Outputs: `analysis/output/s17_clear_threshold.csv`, `analysis/output/s17_confidence_ranking.csv`

Grades two evidence rungs the engine already produces but had never been measured:
the `prior_review` benchmark (`engine_statistical._benchmark`, added `af9b750`) and
`ANALOGUE_CONCUR` (`similarity.py:81`).

## Protocol

Peer pool built from the months preceding the held-out one, through the production
path (`ingest` → `score_batch` → `backfill_history.synthesise_reviews`). The
held-out month is ingested with `factory_recommended_new_max/rop/min` **stripped**,
so the engine falls through to the `prior_review` rung and similarity retrieves
peers that never saw it. The withheld numbers are the label.

Stripping is not optional. `agreement` is computed against
`factory_recommended_new_*` (`engine_statistical:205`) and
`review_history.final_*` **is** that same column (`backfill_history:115`), so
grading an agreement-gated rung on an unstripped month measures nothing. For the
record, the circular version of this number is 82.9% (5730-row `agreement='match'`
+ Low-risk lane, 4750 exact Max matches). It is not used anywhere below.

| Month | Role | Rows | Reviews synthesised |
|---|---|---|---|
| Jul'24 | history | 244 | 135 |
| Aug'24 | history | 154 | 9 |
| Sept'24 | history | 2,500 | 2,487 |
| Oct'24 | history | 730 | 727 |
| May'25 | history | 155 | 153 |
| Jan'26 | held out | 2,780 (2,768 scored) | — |

3,511 reviews collapse to a peer pool of **2,947** unique parts — `_load_pool` keeps
the latest decision per `(item_id, stockroom_id)`.

An earlier run had Aug'24 locked by Excel and skipped it; re-running with the file
readable produced **byte-identical** results, because its 9 reviewed parts were all
already present in later months. Empirical confirmation that the skip cost nothing,
rather than an assumption.

Dec_24 and March_2025 remain un-backfilled (~350MB each, `--include-large`).

`prior_review` fired on **2,570** of 2,768 held-out rows, confirming the rung works
against real data for the first time.

Provenance: `_PRIOR_SQL` was subsequently fixed to admit only decisions dated at or
before the batch's own data vintage, and to resolve "latest" by `reviewed_at` rather
than `review_id` (it previously accepted every other batch and ordered by insert
sequence). Re-running afterwards produced identical numbers — every history month
here predates Jan'26 — so the results below are unaffected either way.

## Result 1 — the route gate is the binding constraint, not rung quality

Only **61 of 2,768** rows (2.2%) are eligible under the engine's own auto-clear
exclusions, because Jan'26 is almost entirely dormant:

| route | rows |
|---|---|
| dormant | 2,575 |
| dying | 125 |
| active | 68 |

`engine_statistical:426` auto-clears only `route == 'active'`. No rung can beat
that ceiling, so every gated config below clears single digits — that is the gate
speaking, not the evidence.

| config | cleared % | cleared n | precision % | n labelled | escaped USD |
|---|---|---|---|---|---|
| baseline_engine | 1.0 | 29 | 79.3 | 29 | **70,419** |
| prior_review_match | 0.1 | 4 | 50.0 | 4 | 2,871 |
| analogue_concur | 0.5 | 14 | 78.6 | 14 | 1,235 |
| prior_or_analogue | 0.6 | 16 | 68.8 | 16 | 4,107 |
| prior_and_analogue | 0.1 | 2 | 100.0 | 2 | 0 |
| prior+conf>=0.8 | 0.1 | 3 | 66.7 | 3 | 39 |
| analogue+conf>=0.8 | 0.3 | 9 | 66.7 | 9 | 1,235 |
| analogue+conf>=0.9 | 0.2 | 6 | 50.0 | 6 | 1,235 |
| **prior_review_match \| no_route_gate** | **91.5** | **2,532** | **96.6** | 2,532 | 2,943 |
| **analogue_concur \| no_route_gate** | **90.9** | **2,516** | **96.9** | 2,516 | 6,200 |

Full grid in the CSV. No config clears the 98% bar
(`triage_clear_precision_bar`), so the harness recommends **rank only, do not
auto-clear** — and exits 0, because that is a valid experimental result.

Worth noting on its own: today's `baseline_engine` clears 29 rows at 79.3%
precision while leaking **$70,419** of exposure — 24× the escaped value of
`prior_review_match|no_route_gate`, which clears 87× more rows at higher precision.
The current auto-clear rungs are the worst option on the card.

## Result 2 — the 96.6% is mostly "both said zero", and must not be read as judgment

The `no_route_gate` rows look strong until you decompose them by route. On Jan'26:

| route | n | engine says 0 | engineer says 0 | both 0 | agree within tolerance |
|---|---|---|---|---|---|
| dormant | 2,575 | **2,575 (100%)** | 2,061 | 2,061 | 2,482 (96.4%) |
| dying | 125 | 0 | 18 | 0 | 101 (80.8%) |
| active | 68 | 0 | 10 | 0 | 34 (50.0%) |

The engine zeroes **every** dormant row. It is not discriminating between them, so
"agreement" there is not evidence of judgment. Of the 2,482 dormant agreements,
2,061 are both-zero and the remaining 421 are the engineer writing 1 against the
engine's 0, forgiven by the `tol_abs = 1.0` floor. Beyond those two cases the
dormant agreement count is zero.

So the honest reading is the inverse of the headline: precision falls as the engine
actually has to decide something — **96.4% dormant → 80.8% dying → 50.0% active**.
The 93 dormant rows where the engineer set a real number above 1 against the
engine's 0 are the ones that matter, and auto-clearing the dormant lane would skip
exactly those.

This vindicates the existing `route == 'active'` gate rather than arguing against
it. Removing it would buy 91% coverage backed by a number that means nothing.

## Result 3 — confidence ranks, even where it cannot clear

Precision by `similarity_result.confidence` decile over uncleared labelled rows:

| decile | n | precision % | confidence range |
|---|---|---|---|
| 0 | 277 | 77.3 | 0.000–0.929 |
| 1 | 277 | 90.6 | 0.929–0.976 |
| 2 | 277 | 96.8 | 0.976 |
| 3 | 276 | 97.5 | 0.976 |
| 4 | 277 | 95.3 | 0.976 |
| 5 | 277 | 95.3 | 0.976–1.000 |
| 6 | 276 | 100.0 | 1.000 |
| 7 | 277 | 100.0 | 1.000 |
| 8 | 277 | 94.9 | 1.000 |
| 9 | 277 | 94.9 | 1.000 |

The bottom decile is 77.3% against ~95% for the rest, so low confidence does
identify rows where review is more likely to change something. The signal is
concentrated at the bottom rather than monotone across the range — confidence
saturates at 0.976/1.000 for 80% of rows, which limits it to "flag the worst
decile" rather than a full ordering. Ranking is usable; the score needs more
dynamic range before it can do more.

## Limitations

- **`risk_level != 'High'` stands in for the engine's criticality exclusion.**
  `sfm_criticality` is not on `recommendation_result`. Critical parts score High, so
  the proxy is close but not identical to `engine_statistical:426`.
- **`analogue_concur` standalone rests on few rows.** Only 57 Jan'26 rows lack a
  prior decision, so the gated variant's 14 cleared rows carry wide error bars. The
  `n_labelled_cleared >= 30` floor in the winner selection exists for this.
- **One held-out month.** Jan'26 is the newest 100%-labelled month. Sept'24 and
  Oct'24 could serve as secondary held-outs with less history behind them.

## Next

1. Re-run with `--include-large` for Dec_24/March_2025 — more peers, and a check on
   whether these numbers are stable.
2. **`prior_review` is already wired into auto-clear and should not be**, contrary
   to the assumption this harness was built under. `agent/specialists.py:151`
   accepts `agreement_source in ("factory", "prior_review")` as a `safe_clear`
   condition, so an uncalibrated rung already gates `clear_candidate` and
   `recommend.bulk_acceptable`. This measurement is its first calibration, and it
   does not clear the bar. Either gate it behind a config lever like the
   `AUTOCLEAR_*` levers (all default-off) or drop `prior_review` from that tuple
   until a held-out run supports it.
3. The reachable win is the dormant lane itself: the engine zeroes all 2,575 rows
   without discriminating. A rung that separates "dormant and genuinely dead" from
   "dormant but the engineer still stocks it" is worth more than any threshold on
   the current signal.
4. Ship confidence as a **ranking** input for the review queue — bottom decile
   first — which needs no precision bar and no auto-clear.
