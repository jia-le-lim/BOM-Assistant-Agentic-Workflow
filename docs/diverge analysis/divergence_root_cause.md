# Divergence Autopsy — engineer vs statistical engine

TCB BOM review · 8 months (2024-07 → 2026-01) · 9,054 engineer decisions · 3,014 parts
Engine under test: `backend/app/engine_statistical.py`, `MODEL_VERSION = "stat-v1"`
Regenerated 2026-08-28 against the **strict scorecard rule** and the **re-calibrated service level**.

## What changed in this revision

Two deliberate changes, both owner decisions, both applied:

1. **Scorecard rule.** Match now requires `|engine − engineer| ≤ 10% × engineer` on **Max and ROP
   only**. No absolute floor, so an engineer's 2 admits only 2 (1.8–2.2) and an engineer's 0 admits
   only 0. Min is excluded — it is a derived floor, not a replenishment lever. The previous rule was
   `max(1, 10%)` across all three fields.
2. **Service level.** `SL_BY_CRIT` moved from `0.99 / 0.95 / 0.90` to **`0.90 / 0.85 / 0.80`**
   (`SL_DEFAULT` 0.95 → 0.85), calibrated on the eight months of engineer decisions below.
3. **One rule, one place.** `engine_statistical._agreement` now applies the same strict rule as the
   scorecard, via the shared `close_enough` / `AGREE_TOL` / `AGREE_FIELDS`, which `s19` and `s20`
   import instead of restating. This aligns the engine's own reason codes (`MATCHES_FACTORY` /
   `DIVERGES_FACTORY`) and confidence bump with what the scorecard reports.

Both are reflected in every number in this document. Where a figure moved because of the rule
rather than the engine, that is called out.

---

## Summary

| Metric | Value | Note |
| --- | --- | --- |
| Headline match | 79.7% | 7,219 of 9,054. Was 95.3% under the old rule — the rule changed, not the engine |
| **Match on live rows** | **30.9%** | 711 rows where the sizing math runs; 491 of 1,835 diverges sit here |
| Diverges | 1,835 | 1,344 dormant · 279 dying · 212 active |
| Proposed stock | **$2.53M** | down from $3.37M at the legacy service level (−24.9%) |
| Live-row gain from the SL change | +2.3 pp | 28.6% → 30.9%; **improves 5 months, flat 2, regresses 2** |

The headline is not the number to manage. 77.3% of the population is a dormant part where both
sides write zero, and that fraction fixes the headline in a narrow band no matter what the engine
does — every configuration tested lands between 78.3% and 79.8%. The **live-row rate does
discriminate** (12.2% to 31.2% across the service-level sweep), so it is the signal to steer on.

---

## Finding 01 — the headline is measuring the part mix

| Segment | Rows | Share | What it is |
| --- | ---: | ---: | --- |
| Free match | 6,999 | 77.3% | Dormant part, engineer wrote 0/0, engine wrote 0/0. Any engine returning zero for a dead part scores these. They prove nothing. |
| Dormant disagreement | 1,344 | 14.8% | Engine says zero, engineer **keeps stock**. Under the old ±1 floor, 1,179 of these were scored as *matches*. The strict rule now counts every one as a divergence. |
| Live demand | 711 | 7.9% | Active (257) + dying (454) — the only rows where the demand distribution, service level and lead time produce a number. Match rate: 30.9%. |

### Match rate by demand route

| Route | Rows | Match | Diverge | What the engine does here |
| --- | ---: | ---: | ---: | --- |
| `active` | 257 | **17.5%** | 212 | Negative-binomial quantile of lead-time demand at the criticality service level |
| `dying` | 454 | **38.5%** | 279 | Same math, but the demand rate comes from the 365-day window while the last 90 days are zero |
| `dormant` | 8,343 | 83.9% | 1,344 | Hard-coded 0/0/0 (non-critical) or 1/1/1+MOQ (critical) — no statistics involved |

### The dormant disagreement, now visible

| | Rows |
| --- | ---: |
| engineer 0, engine 0 — true agreement | 6,999 |
| engineer 1, engine 0 | 1,179 |
| engineer ≥ 2, engine 0 | 165 |

The engine zeroes **1,344 parts the engineer chose to keep**. Under the old rule only 165 of those
surfaced; the other 1,179 were absorbed by the ±1 floor. This single policy question is now
**73.2% of the residual divergence** and the largest item on the board.

> 92% of the review population never touches the statistical engine — it is routed to a constant.
> Any improvement to the demand model can only move 7.9% of the rows. Report **match on live rows**
> as the primary number and dormant coverage separately.

---

## Finding 02 — root-cause decomposition: the engineers already told us why

Every BOM row carries a `justification` code the engineer picked. It partitions the 1,835 diverges
exactly, with no overlap — a clean decomposition of the gap, where each bucket names a specific
assumption inside the engine that fails.

| Bucket | Diverges | Share | Diverge rate | Verdict |
| --- | ---: | ---: | ---: | --- |
| (no justification recorded) | 571 | 31.1% | 21.3% | Ordinary sizing disagreement |
| ad-hoc consumption / bulk withdraw | 488 | 26.6% | **90.5%** | Needs a lumpy-demand route |
| budget / DOI control | 247 | 13.5% | 5.6% | Lever exists, currently off |
| follow sfm | 188 | 10.2% | 30.7% | Benchmark question, not a model error |
| constraint tool | 181 | 9.9% | **98.9%** | Needs a new structured input |
| flat future consumption | 140 | 7.6% | 22.3% | Needs a forward-looking input |
| required for sustaining | 9 | 0.5% | 90.0% | Needs a new structured input |
| volume/tool increase | 6 | 0.3% | 85.7% | Needs a new structured input |
| eol/obsoleted | 4 | 0.2% | **100%** | Needs a new structured input |
| volume/tool decrease | 1 | 0.1% | 50.0% | Needs a new structured input |
| **Total** | **1,835** | **100%** | 20.3% | |

### Ad-hoc consumption / bulk withdraw — 488 rows, 90.5% diverge

**Engine assumes:** consumption in a window is a demand *rate* that continues —
`μ = c365 / 365`, then stock covers `μ × (lead time + 30)`.

These parts were pulled once, in bulk, for a job. There is no rate to annualise. The engine spreads
a single withdrawal across a year; the engineer sizes to the next expected job. Under the strict
rule this bucket now fails **nine times out of ten** — the clearest evidence in the dataset that the
model *class* is wrong here, not its parameters.

Worst case: `500790606 BACAVI,TV,700GR` — 700 units consumed in the last 90 days, MOQ 100, current
Max 1, engineer 1,500, engine 300.

### Constraint tool — 181 rows, 98.9% diverge

**Engine assumes:** a part's operational status is visible in the structured columns it may read.

It is not. This bucket was 21 diverges under the old rule and is 181 now — the ±1 floor was covering
a large number of small, systematic misses on constraint-tool parts. Together with `eol/obsoleted`
(100%), `required for sustaining` (90%) and `volume/tool increase` (86%), these are facts about a
tool or a product ramp that exist only in the engineer's head.

### Follow SFM — 188 rows, 30.7% diverge

**Engine assumes:** the engineer is independently estimating demand.

They are copying the SFM number. On live rows, `sfm_brr_*` alone predicts the engineer at **55.4%**
against the engine's 30.9%. Divergence here is engine-versus-SFM, not engine-versus-expert.

### Flat future consumption — 140 rows, 22.3% diverge

**Engine assumes:** trailing consumption is the best estimate of next period's demand.

The engineer is applying forward-looking information the engine has no input for, and the action is
usually *hold the level still*.

### Budget / DOI control — 247 rows, 5.6% diverge

**Engine assumes:** sizing is driven by a service-level target, not a days-of-inventory ceiling.

Largest label in the population (4,391 rows) and the lowest diverge rate, because it lands mostly on
dormant parts both sides zero.

---

## Finding 03 — the service-level calibration, and its honest caveat

**The distribution is fine. The target was wrong.** Dispersion barely matters at these demand rates:
under the shipped configuration, halving `PHI_DYING` (3.0 → 1.5) changes **1 row**, and
`PHI_ACTIVE` (1.5 → 1.2) changes **0**. The quantile is set almost entirely by the service level.

### Service-level sweep

| Service level | All | **Live** | Active | Dying | Proposed stock |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.99 | 78.3% | 12.2% | 3.1% | 17.4% | $9.28M |
| 0.95 | 79.5% | 28.3% | 14.4% | 36.1% | $4.19M |
| 0.90 | 79.6% | 29.0% | 15.2% | 36.8% | $3.01M |
| 0.85 | 79.8% | **31.2%** | 18.3% | 38.5% | $2.59M |
| 0.80 | 79.7% | 30.4% | 16.7% | 38.1% | $2.41M |
| 0.75 | 79.7% | 30.1% | 16.3% | 37.9% | $2.25M |
| 0.70 | 79.7% | 30.0% | 15.6% | 38.1% | $2.14M |
| legacy 0.99/0.95/0.90 | 79.5% | 28.6% | 15.2% | 36.1% | $3.37M |
| **shipped 0.90/0.85/0.80** | **79.7%** | **30.9%** | **17.5%** | **38.5%** | **$2.53M** |

The sweep peaks at 0.85 and flattens below it — the match rate stops improving while stock keeps
falling, the signature of over-cutting rather than better fit. The criticality-graded table lands
within 0.3 pp of flat 0.85 on live rows while keeping high-criticality parts at 0.90.

### Month by month — read this before quoting the gain

| Review month | Live rows | Legacy SL | Shipped SL | Δ |
| --- | ---: | ---: | ---: | ---: |
| 2024-07 | 21 | 28.6% | 28.6% | 0.0 |
| 2024-08 | 9 | 22.2% | 22.2% | 0.0 |
| 2024-09 | 100 | 28.0% | 35.0% | +7.0 |
| 2024-10 | 106 | 19.8% | 25.5% | +5.7 |
| 2024-12 | 164 | 26.8% | 24.4% | **−2.4** |
| 2025-03 | 51 | 13.7% | 15.7% | +2.0 |
| 2025-05 | 67 | 31.3% | 25.4% | **−5.9** |
| 2026-01 | 193 | 38.3% | 44.0% | +5.7 |
| **Aggregate** | **711** | **28.6%** | **30.9%** | **+2.3** |

Under the previous `max(1, 10%)`-on-three-fields rule this change won **every** month by 8.9 to
17.7 pp. Under the strict rule it wins five, ties two and **loses two**. The reason is mechanical:
with no absolute floor a near-miss stops counting in either direction, so moving the quantile can
push a row out of tolerance as easily as into it. The aggregate improvement is real but modest, and
`s20_diverge_rootcause.py` now guards the aggregate and prints the regressing months rather than
asserting a per-month win it cannot support.

> **Risk, stated plainly.** A lower service level is a genuinely higher stockout probability. This
> analysis establishes what the engineers' decisions *imply*; it does not establish what is safe.
> No fill-rate or stockout simulation exists in the repo yet (ML plan Phase 4). Until it does, the
> 0.90/0.85/0.80 table rests on revealed preference, not on a service guarantee.

---

## Finding 04 — the ceiling

Residual under the shipped configuration: **1,835 diverges** — dormant 1,344, dying 279, active 212.

| Characteristic of the residual row | Rows | Share | Why tuning cannot reach it |
| --- | ---: | ---: | --- |
| Dormant — zero consumption in all six windows | 1,344 | 73.2% | No demand signal exists; the answer is a stocking policy |
| Engineer stated a reason in `justification` | 1,264 | 68.9% | The reason lies outside the engine's input space |
| Engineer held the current level unchanged | 721 | 39.3% | Continuity preference, not a computed target |
| Engineer's Max is exactly the SFM number | 445 | 24.3% | The benchmark is another system's output |

These markers overlap and are diagnostic, not a partition; the clean partition is Finding 02.

### Who predicts the engineer best — live rows, same rule

| Candidate | Coverage | Match, live rows | Reading |
| --- | ---: | ---: | --- |
| `atm_recommended_*` | 9,053 | 81.2% | **Target leakage.** Equals the engineer's final on 86% / 98% of rows for Max / ROP — the review's output column written back into the sheet, not an input. Never a feature, never a benchmark. |
| `sfm_brr_*` | 9,054 | 55.4% | Another planning system beats the engine by 24 pp |
| `sfm_max/rop` | 2,217 | 54.9% | Same signal, thinner coverage |
| `max_qty` (do nothing) | 9,054 | 46.0% | Leaving every level untouched beats the engine by 15 pp |
| engine (shipped SL) | 9,054 | **30.9%** | Last place among the available baselines |

> **Read the "do nothing" row carefully.** 46.0% is *not* proof the engine is useless. Engineers
> keep most levels flat, so inertia scores well against their own decisions while delivering no
> inventory improvement at all. It is proof that **agreement with the engineer is the wrong sole
> objective** — the engine proposes 24.9% less stock than the legacy configuration, which inertia
> by definition cannot do. But the gap to SFM BRR is large enough to demand an answer: on live
> rows, another system already predicts these engineers far better than we do.

---

## Finding 05 — recommendations, ranked by evidence

### 1. Do **not** re-score the historical batches — resolved, no action

An earlier revision of this document recommended re-scoring the eight batches so the database would
reflect the new service level. That recommendation was wrong, and is withdrawn.

`engine_adapter.score_batch` (lines 150-190) deliberately refuses to overwrite a row an engineer has
already decided on: `payload = [p for p in payload if (p[1], p[2]) not in reviewed]`, with the
rationale *"a reviewed item keeps the recommendation its reviewer actually saw."* Verified against
the database: all 9,054 analysed rows are reviewed, so a re-score would replace only the **325**
un-reviewed rows in those batches and change nothing in this analysis. `review_history.engine_max`
equals `recommendation_result.new_max` on **all 9,054** rows.

So `recommendation_result` for a historical batch is not stale data — it is the audit record of what
the reviewer was shown. Overwriting it would falsify that record to no analytical benefit. The
correct way to see what today's engine would say is the offline harness, which is what this document
already uses: `engine_statistical.run()` on the stored payloads, at the legacy service level,
reproduces the persisted Min/ROP/Max on **100.0%** of all 9,054 rows.

### 2. Make live-row match the headline

77.3% of the denominator is a row where both sides write zero, which pins the headline between 78%
and 80% for every configuration tested. It cannot detect a regression in the part of the engine
that does the work. `route` is already persisted on `recommendation_result`, so the split costs a
`GROUP BY`. Report: live-row match, dormant coverage, proposed stock.

### 3. Decide the dormant stocking policy explicitly, and write it down

**73.2% of all residual divergence** is now this one question: does a dead non-critical part with
stock on hand keep one unit or zero? The engine's rule is `DORMANT_NONCRITICAL_ZERO` → propose 0.
The engineers keep stock on 1,344 of them. Resolve first the data contradiction flagged in the
previous revision: 60 of the 165 engineer-≥2 cases carry a `days_since_last_issue` inside 547 days
while every consumption window reads zero.

### 4. Add a lumpy / bulk-withdraw route

26.6% of diverges at a **90.5% failure rate**. A rate model applied to a one-off withdrawal is the
wrong model class. Detect it from shape already in the data — consumption concentrated in one
window, low `frequencymonthswithusage`, MOQ large relative to the annual quantity — and size to one
expected job rather than an annualised rate.

### 5. Build the fill-rate backtest

Required to justify the service level now in production, and it is the ML plan's own Phase 4 gate.
Replay each part's next-snapshot consumption against the proposed Min/ROP/Max across the seven
forward month-pairs; report achieved fill rate per criticality tier at SL ∈ {0.95, 0.90, 0.85,
0.80}. Without it the calibration rests on revealed preference alone.

### 6. Capture the lifecycle facts as structured inputs

`eol/obsoleted` 100%, `required for sustaining` 90%, `volume/tool increase` 86%, `constraint tool`
98.9% across 181 rows. The strict rule has made this bucket far more visible than it was. A small
dropdown on the review form turns guaranteed misses into inputs.

### 7. `_agreement` now follows the scorecard — done, with one loose end

The rule lives in exactly one place: `engine_statistical.close_enough`, parameterised by
`AGREE_TOL = 0.10` and `AGREE_FIELDS = ("max", "rop")`. `_agreement` calls it, and both `s19` and
`s20` import it rather than restating it. Each harness self-check now asserts, case by case, that
its verdict equals `engine_statistical._agreement` on the same numbers, so the scorecard cannot
drift from the engine without a run failing. All 271 backend tests pass.

**Loose end.** `analysis/common.agree()` (used by s15 and s17) still applies the old
`max(1, 10%)` on all three levels, and that is deliberate — it answers a different question
(*would a human review have added nothing?*), where a 1-unit difference on a 2-unit part genuinely
is immaterial. But s17 asks the *engine* whether `agreement == 'match'` and then computes precision
with its own predicate, so those are now two rules inside one calculation.
**`s17_clear_threshold.csv` should not be quoted until that is re-derived** — either grade both ends
with the engine's rule, or stop reading the engine's verdict and grade entirely in the harness. The
coupling note at `analysis/common.py:109` records this.

### 8. `policy_max_doi_days` — cost lever only, demoted

Under the old rule this was slightly positive on agreement (+0.2 pp). Under the strict rule it costs
**−1.1 pp** on live rows for a −7.0% stock reduction. It is no longer a free win; treat it as a
budget instrument to switch on when inventory value is the binding constraint.

---

## Finding 06 — changes that look like improvements

### Do not use `atm_recommended_*`

81.2% on live rows because it *is* the engineer — equal to the final decision on 86–98% of rows
depending on the field. Scoring against it, or feeding it in, manufactures a number that predicts
nothing.

### The tolerance-gaming exploit is now closed

Under the old rule, giving every dormant part a keep-alive of 1 raised the score to 96.3% purely by
exploiting the ±1 floor — an engine value of 1 sat "within tolerance" of an engineer's 0, 1 and 2 at
once. Under the strict rule the same trick scores **2.5%** (dormant 0.1%) and takes proposed stock
from $2.53M to $17.8M. This is the clearest evidence that the rule change was the right call: the
metric can no longer be gamed by splitting the difference.

### Do not switch on the cost-aware service level

The newsvendor critical ratio is the most sophisticated lever in the codebase and the worst
performer: **−9.5 pp** on live rows and **+35.6%** stock. It drives cheap parts toward a ~0.999
service level — exactly the over-service the calibration removed.

### Also tested, not recommended

Against the shipped baseline: blended estimator −2.3 pp live / +5.2% stock; trend adjust −1.1 pp /
+9.2%; lead-time sigma −0.9 pp / −1.3%; excess netting −1.5 pp / −11.0%. Every built-in demand-model
lever is now negative on agreement. The two that reduce stock (excess netting, DOI cap) do so at a
measurable accuracy cost and should be chosen as budget decisions, not accuracy ones.

---

## Method

- **Population.** All 9,054 `review_history` rows with `decision = 'historical'` across the eight
  ingested TCB review months (2024-07 → 2026-01), joined to `bom_rows.payload` and
  `recommendation_result`. Same rows and batch-to-month map as `s19_decision_vs_engine_tcb.csv`.

- **Engineer's decision.** `review_history.final_max/rop/min`, which for historical ingestion is the
  engineer's own `factory_recommended_new_*` from the review sheet.

- **Scoring rule.** `|engine − engineer| ≤ 0.10 × engineer` on Max and ROP; Min excluded; no
  absolute floor. Implemented once in `s19_export_decision_vs_engine._validate` and mirrored in
  `s20_diverge_rootcause._matched`, both with self-checks pinning the boundary cases (engineer 2
  admits only 2; engineer 0 admits only 0; the 10% boundary is inclusive; Min ignored however far
  off; ROP alone can break a row).

- **One definition, enforced.** The tolerance is `engine_statistical.close_enough`
  (`AGREE_TOL`, `AGREE_FIELDS`); `_agreement` calls it and both harnesses import it. Each harness
  self-check asserts its verdict equals `_agreement` on the same numbers, so a drift fails the run.
  `s19` still emits `engine_agreement` beside `validation`, but note what it now means: for the
  eight historical batches it is the verdict recorded **at scoring time under the old rule**, since
  those rows are an audit record and are not re-scored (recommendation 1). Future batches will carry
  the same rule in both columns.

- **Counterfactual harness.** `engine_statistical.run()` re-run offline on the stored payloads **at
  the legacy service level reproduces the persisted Min/ROP/Max on 100.0% of all 9,054 rows**, on
  each of the three fields. That is the proof the harness is the production engine and not a
  re-implementation. At the shipped service level it deliberately differs — see recommendation 1.

- **Overfitting guard.** The script asserts the aggregate live-row match does not regress against
  the legacy table, and prints every month where the shipped table is worse. It does not assert a
  per-month win, because under this rule there is not one.

- **Cost figure.** "Proposed stock" is `Σ unitprice × proposed Max` over all 9,054 rows — a
  comparison metric between configurations, not a budget number.

### Scripts

| Script | Does | Writes |
| --- | --- | --- |
| `analysis/s19_export_decision_vs_engine.py` | Exports engineer vs engine per month, applies the scorecard rule | `analysis/output/s19_decision_vs_engine_tcb.csv` |
| `analysis/s20_diverge_features.py` | Pulls rows, engine inputs and full payloads from Supabase | `analysis/output/s20_diverge_features.csv`, `analysis/output/s20_payloads.pkl` |
| `analysis/s20_diverge_rootcause.py` | Runs the decomposition and every ablation offline | `analysis/output/s20_ablation.csv` |

None of these write to the database.

Engine under test: `backend/app/engine_statistical.py`, `MODEL_VERSION = "stat-v1"`,
`SL_BY_CRIT = {"h": 0.90, "m": 0.85, "l": 0.80, "d": 0.80}`.
