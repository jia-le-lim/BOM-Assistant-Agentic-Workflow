# Work Log — Multi-Month TCB Extraction & Consumption-Trend Build

| | |
|---|---|
| Document type | Engineering session log |
| Author | LIM |
| Date | 13 August 2026 |
| Session goal | Extract `module = TCB` across all monthly BOM files; design a cross-month consumption-trend dataset |
| Deliverable | [analysis/s10_multi_month_extract.py](../../analysis/s10_multi_month_extract.py) + 3 CSVs in [analysis/output/](../../analysis/output/) |
| Status | **Complete** (open follow-ups in §8) |

---

## 1. Objective

Consolidate every prior-month BOM review table into one training-ready dataset, restricted to the **TCB** module, keying on consumption rate / lead time / machine type, and — the core ask — a way to capture the **consumption trend when the same item recurs across months**, plus a **cumulative consumable number** for recurring items.

## 2. Source inventory

Nine dated files under `BOM table/` (the `DEMO_TCB_showcase.csv` synthetic file is excluded). All share the same lowercased column schema as the Jan'26 CSV. The full-dump data sheet was selected per workbook (most TCB rows), not the curated `49510` subset.

| Snapshot | File | Data sheet | TCB rows |
|---|---|---|---|
| 2024-07 | AE_JULY'24 … Factory Cost Rep Review_.xlsx | `Upload List` | 244 |
| 2024-08 | AUGUST'24 … Factory Cost Rep Review .xlsx | `Upload File` | 154 |
| 2024-09 | Sept_24 BOM REVIEW.xlsx | `sheet1` | 2,500 |
| 2024-10 | Oct_24 BOM REVIEW.xlsx | `Raw` | 730 |
| 2024-12 | Dec_24 BOM REVIEW.xlsx | `49510_DEC'24` | 2,748 |
| 2025-03 | March_2025_BOM REVIEW_UN94X4_…xlsx | `sheet1` | 140 |
| 2025-04 | Apr_2025_BOM REVIEW_ww17.xlsx | `Ignore this` | 140 — **dropped (dup of 2025-03)** |
| 2025-05 | May'2025_BOM REVIEW.xlsx | `49510` | 155 |
| 2026-01 | BOM REVIEW_Jan'26 .csv | (csv) | 2,780 |

## 3. Key decisions

1. **Scope** — full-dump `module == TCB` per file (consistent across all months; matches existing `analysis/common.py`).
2. **Duplicate snapshots** — March'25 and April'25 are byte-identical (same items *and* same `last_365` values). Auto-detected via a TCB-subset fingerprint; the later month (April) is dropped so it cannot double-count in the cumulative. Toggle `DROP_IDENTICAL_SNAPSHOTS = False` to keep it.
3. **Heterogeneous scope** — monthly TCB counts swing 140 → 2,780 because some files are full site dumps and others are small curated review lists. Therefore an item **absent** in a month = *not in that month's review scope*, **not** zero consumption. Absent months are left as `NaN` and never zero-filled; a genuine in-file 0 is preserved.
4. **Recurring items** (confirmed with owner) — an item re-appearing in consecutive reviews is the **same** item being re-reviewed, not a new one. Keyed on `item_id`; consumption accumulated across reviews.
5. **Calendar-aware trend** — `month_index = year*12 + (month-1)` drives every slope so the gaps (missing Nov'24, Jan–Feb'25, the jump to Jan'26) are handled instead of treating snapshots as evenly spaced.

## 4. Column design

**Long panel** — `s10_tcb_panel_long.csv` (one row per item × month): identity + key features (`unitprice`, `contractual_lead_time`, `machine_type`, `replenishment_policy`, `shared_parts`, `factory_recommended_new_max/rop/min`, `max/rop/min_qty`) + the six trailing-window consumption columns + per-snapshot rates (`cons_rate_5/30/90/180/365/547d`, `cons_momentum_30_over_365`) + cumulative (`gap_months`, `period_cons_est`, `cum_cons_est`).

**Trend table** — `s10_tcb_consumption_trend.csv` (one row per item):
- Pivoted monthly consumption `cons90_YYYYMM` and `cons30_YYYYMM` (sortable, `NaN` where absent).
- Trend aggregates on the `last_90` series: `cons90_slope_per_month`, `cons90_slope_norm`, `cons90_pct_change`, `cons90_cv`, `first/last/max`, `annual365_slope_per_month`, `is_accelerating_latest`.
- Coverage: `n_months_present`, `coverage_ratio`, `first/last_month`, `span_months`, `is_new_recent`.
- Cumulative: `reviews_count`, `cum_cons_est_total`, `avg_cons_per_review`, and the clean source rolling totals `latest_last_365_cons`, `latest_last_547_cons`.
- `trend_label` ∈ {INCREASING, DECREASING, STABLE, INTERMITTENT, DORMANT, SINGLE_SNAPSHOT}.

### Cumulative method
The trailing windows overlap, so a plain sum would double-count. For each review, `period_cons_est` takes the trailing window **closest to the gap since the previous review** (first review uses the 30-day window), then `cum_cons_est` sums those non-overlapping pieces. Use `latest_last_547_cons` (clean 18-month rolling actual) as the trustworthy cumulative; `cum_cons_est_total` is the estimate spanning the full review history (may differ from the actual due to source restatement/aging).

## 5. Findings

- **3,060** distinct TCB items across **8** usable monthly snapshots; long panel = **9,424** item-month rows.
- Trend labels: DORMANT 2,587 · SINGLE_SNAPSHOT 260 · INTERMITTENT 174 · STABLE 29 · DECREASING 6 · INCREASING 4.
- **~92% of TCB items never consume in any 90-day window** (slow/non-moving spares) → labelled `DORMANT`, not `STABLE`. Only ~213 items show real movement.
- March'25 == April'25 confirmed duplicate; auto-dropped.

## 6. Outputs

| File | Grain | Notes |
|---|---|---|
| [analysis/output/s10_tcb_panel_long.csv](../../analysis/output/s10_tcb_panel_long.csv) | item × month | tidy training base |
| [analysis/output/s10_tcb_consumption_trend.csv](../../analysis/output/s10_tcb_consumption_trend.csv) | item | pivot + trend + cumulative |
| [analysis/output/s10_extract_manifest.csv](../../analysis/output/s10_extract_manifest.csv) | file | provenance + dup status |

## 7. How to reproduce

```powershell
python analysis\s10_multi_month_extract.py
```

Interpreter: `C:\Program Files\Python314\python.exe` (pandas 3.0.5, openpyxl 3.1.5). Reading the large Excel files takes a few minutes. The script writes a `*.new.csv` fallback if an output is locked (open in Excel) instead of failing. To rebuild only the trend table without re-reading Excel, load `s10_tcb_panel_long.csv` and call `build_trend`.

## 8. Open follow-ups (next session)

1. Re-run with `DROP_IDENTICAL_SNAPSHOTS = False` if April'25 should count as a real re-review.
2. Join the trend table onto the engine output (`analysis/output/s6_engine_output.csv`) for scoring.
3. Optional curated **AE / 49510-scope** variant instead of the full-dump scope.
4. Tune the INCREASING/DECREASING threshold (`slope_norm` ±0.10) if more/fewer movers should be flagged.
