# Plan: Snap-1 + prior anchor sizing for live routes

## Summary

Add two config-gated levers to `engine_statistical.run()` that change the proposed
Min/ROP/Max on **live rows only** (`route` in `active` / `dying`). The first holds the
current stocking level when the quantile lands within N units of it (`continuity_snap`).
The second, inside that band, prefers the part's own previous engineer decision over the
current level when `replenishment_policy` says the part is not managed to a Max
(`prior_anchor_policy`). Both default OFF, so a bare `run()` is byte-identical to today.

Measured on eight cycles of TCB history (`analysis/s26_small_delta_tuning.py`):
live-row agreement 30.9% → 44.9%, low-delta band 40.1% → 57.9%, and the hit rate on the
rows where the engine actually asks for a change goes 18.3% → 19.6% while the proposal
count drops 481 → 194.

## User Story

As a **TCB stocking engineer reviewing a monthly BOM batch**,
I want **the engine to stop proposing one-unit changes it cannot justify, and to anchor
order-to-demand parts on my own last decision rather than a stale SAP Max**,
So that **the numbers I am asked to act on are ones I would plausibly have written myself.**

## Problem → Solution

At TCB demand rates (median `c365` = 1 unit/year) the lead-time-demand quantile has no
resolution left: `_quantile(mu_LT ≈ 0.3, 0.85)` rounds to 0 or 1, and
`new_max = max(new_max, new_rop + moq)` supplies the 1. The engine returns **Max = 1 on
499 of 711 live rows (70%)**, inside which the engineers wrote 0, 1, 2, 3 and 4 — so 39%
of all live rows sit in three cells the engine cannot separate (`1→2`: 149 rows, `1→0`:
109, `1→3`: 21).

→ Stop proposing a change the quantile cannot support. Inside a one-unit band, propose the
level that is already in force — or, for parts SAP does not manage to a Max, the level the
engineer last chose.

## Metadata

- **Complexity**: Small
- **Source PRD**: N/A (free-form; evidence in `analysis/s26_small_delta_tuning.py`)
- **PRD Phase**: N/A
- **Estimated Files**: 3 (1 engine, 1 test, 1 analysis harness)

---

## UX Design

### Before

```
Item 500699374  O2ANALYZER,MODULE,ASSEMBLY      route: dying
  current   Max 1 / ROP 0
  engine    Max 2 / ROP 1        action: Increase     review: Y
  reason    DYING_DEMAND
                                  ^ a one-unit move the demand data cannot justify;
                                    the engineer wrote 1
```

### After (`continuity_snap = 1`)

```
Item 500699374  O2ANALYZER,MODULE,ASSEMBLY      route: dying
  current   Max 1 / ROP 0
  engine    Max 1 / ROP 0        action: Maintain     review: Y
  reason    DYING_DEMAND,CONTINUITY_SNAP
                                  ^ engine says "the level in force is inside my
                                    resolution" instead of inventing a move
```

### After (`prior_anchor_policy = "demand"`, part is Order To Demand)

```
Item 500184739  ASIO3                            route: active
  current   Max 1 / ROP 0        (SAP; never updated after last cycle)
  prior     Max 2 / ROP 1        (this engineer's own decision, previous cycle)
  engine    Max 2 / ROP 1        action: Increase     review: Y
  reason    CONSTANT_CONSUMER,PRIOR_ANCHOR
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Item detail `reason_code` chips | `DYING_DEMAND` | `DYING_DEMAND,CONTINUITY_SNAP` | `ReasonCodes` renders raw codes (`frontend/src/components/ui.tsx:120`) — **no frontend change needed** |
| Batch table `action` column | many `Increase` / `Decrease` | more `Maintain` | `_action()` derives this from `new_max` vs `cur` — falls out for free |
| Batch summary `top_reason_codes` | — | two new codes appear | `engine_adapter.score_batch` counts them automatically (`engine_adapter.py:211`) |
| Review queue volume | 481 proposals over 8 cycles | 194 | Fewer rows where engine ≠ current; `review_required` logic itself is untouched |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/app/engine_statistical.py` | 61-99 | Lever declaration block — the exact shape a new lever must take |
| P0 | `backend/app/engine_statistical.py` | 286-348 | `run()` cfg reads and column reads; where the two new cfg keys and two new columns go |
| P0 | `backend/app/engine_statistical.py` | 399-431 | The active/dying sizing branch the snap is inserted into |
| P0 | `backend/app/engine_statistical.py` | 449-451 | The monotonic clamp — **the gotcha**; see Task 3 |
| P1 | `backend/app/engine_statistical.py` | 315-324 | How `prior_final_*` is already read (as a benchmark); the new code reuses those Series |
| P1 | `backend/tests/test_statistical_engine.py` | 19-43 | `_row` / `_run` / `_run_cfg` helpers every new test uses |
| P1 | `backend/tests/test_statistical_engine.py` | 340-366 | Lever test shape + `test_levers_default_off_match_baseline`, which must be extended |
| P2 | `backend/app/engine_adapter.py` | 103-137 | `_attach_prior_benchmark` — proof `prior_final_*` already reaches the engine; **no change needed here** |
| P2 | `analysis/s26_small_delta_tuning.py` | 113-152 | `snap_prior()` (135-142) — the harness rule the engine must reproduce exactly; `cands` at 144 |
| P2 | `analysis/s23_live_route_autopsy.py` | 83-127 | `ladder()` — the module-level-function pattern the harness uses |

## External Documentation

No external research needed — feature uses established internal patterns
(`scipy.stats` sizing already in place; no new dependency).

---

## Patterns to Mirror

### LEVER_DECLARATION
```python
# SOURCE: backend/app/engine_statistical.py:61-73
# --- Demand-model + policy levers (PRD v3.2 A/B improvements). Every default
# reproduces the current stat-v1 sizing, so a bare run() is unchanged; each is
# opt-in via cfg and calibrated through analysis/s13 + s6_backtest. ---
DEMAND_ESTIMATOR = "single"         # "single" (first populated window) | "blended"
TREND_ADJUST = False                # lift mu toward the recent rate on a ramp
POLICY_MAX_DOI_DAYS = 0.0           # cap Max at this many days of demand (0 = off)
POLICY_EXCESS_NETTING = False       # net shareable excess off the proposed Max
```

### CFG_READ
```python
# SOURCE: backend/app/engine_statistical.py:297-304
    estimator = str(cfg.get("demand_estimator", DEMAND_ESTIMATOR))
    trend_on = bool(cfg.get("trend_adjust", TREND_ADJUST))
    max_doi_days = float(cfg.get("policy_max_doi_days", POLICY_MAX_DOI_DAYS))
    excess_netting_on = bool(cfg.get("policy_excess_netting", POLICY_EXCESS_NETTING))
```

### NUMERIC_COLUMN_READ
```python
# SOURCE: backend/app/engine_statistical.py:308-324
    lt = _num(df, "contractual_lead_time")
    cur_max = _num(df, "max_qty")
    cur_rop = _num(df, "rop_qty")
    cur_min = _num(df, "min_qty")
    # Fallback benchmark: this part's last engineer decision, attached by
    # engine_adapter._attach_prior_benchmark (absent on a bare run()).
    prior_max = _num(df, "prior_final_max")
    prior_rop = _num(df, "prior_final_rop")
    prior_min = _num(df, "prior_final_min")
```

### TEXT_COLUMN_READ
```python
# SOURCE: backend/app/engine_statistical.py:327-333
    own = df.get("ownership", pd.Series("", index=df.index)).astype(str)
    crit = df.get("sfm_criticality", pd.Series("", index=df.index)).astype(str)
    item = df.get("item_id", pd.Series("", index=df.index)).astype(str)
    category = df.get("part_category", pd.Series("", index=df.index)).astype(str)
```

### POLICY_CLAMP_WITH_REASON_CODE
```python
# SOURCE: backend/app/engine_statistical.py:438-447
        if max_doi_days > 0 and pd.notna(mu) and mu > 0:
            cap = max(math.ceil(max_doi_days * mu), int(new_rop))
            if new_max > cap:
                new_max = cap
                reasons.append("DOI_CAP")
        if excess_netting_on and pd.notna(excess_qty.iloc[i]) and excess_qty.iloc[i] > 0:
            e = int(excess_qty.iloc[i])
            if new_max - e >= new_rop:
                new_max -= e
                reasons.append("EXCESS_NETTED")
```

### RULE_WINS_OVER_ENGINE (dormant precedent for "an anchor replaces the sizing")
```python
# SOURCE: backend/app/engine_statistical.py:375-387
        elif route == "dormant":
            # The engineer-owned rule wins over the engine here (owner decision,
            # 2026-09-01). ...
            _rule = DR.resolve(dormant_rules, item.iloc[i], category.iloc[i])
            _applied = DR.apply(_rule, cur)
            if _applied is not None:
                new_min, new_rop, new_max = _applied
                # review stays "Y": the rule changes the NUMBER, not whether a
                # human looks. "N" here would silently clear 8,343 rows.
                review, dist, conf, risk = "Y", "rule", 0.5, "Low"
                reasons.append("DORMANT_RULE_APPLIED")
```

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_statistical_engine.py:19-43
def _row(**kw) -> dict:
    base = {
        "item_id": "A", "unitprice": "10", "max_qty": "2", "rop_qty": "1",
        "min_qty": "0", "contractual_lead_time": "30", "order_qty_multiple": "1",
        "sfm_criticality": "M", "frequencymonthswithusage": "8",
        **{f"last_{w}_day_cnsmptn_qty": "0" for w in WINS},
    }
    base.update({k: str(v) for k, v in kw.items()})
    return base


def _run_cfg(rows, cfg):
    from app.engine_statistical import run
    return run(pd.DataFrame(rows), cfg)

_CONST = dict(last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
              last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
              last_547_day_cnsmptn_qty=18)
```

### LEVER_TEST
```python
# SOURCE: backend/tests/test_statistical_engine.py:340-352
def test_doi_cap_limits_max():
    base = _row(**_HI)
    default = _run([base]).iloc[0]
    capped = _run_cfg([base], {"policy_max_doi_days": 20}).iloc[0]
    assert capped["factory_recommended_new_max"] <= default["factory_recommended_new_max"]
    assert "DOI_CAP" in capped["reason_code"]
```

### DEFAULTS_OFF_GUARD
```python
# SOURCE: backend/tests/test_statistical_engine.py:356-366
def test_levers_default_off_match_baseline():
    """Every new lever defaults to the current sizing (no silent regression)."""
    rows = [_row(**_CONST)]
    base = _run(rows).iloc[0]
    same = _run_cfg(rows, {"demand_estimator": "single", "trend_adjust": False,
                           "lead_time_sigma": False,
                           "service_level_mode": "criticality"}).iloc[0]
    for c in ("factory_recommended_new_max", "factory_recommended_new_rop",
              "factory_recommended_new_min"):
        assert base[c] == same[c]
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/app/engine_statistical.py` | UPDATE | Two lever constants, two cfg reads, one column read, the snap block inside the live branch |
| `backend/tests/test_statistical_engine.py` | UPDATE | Seven new lever tests + extend the defaults-off guard |
| `analysis/s26_small_delta_tuning.py` | UPDATE | Add an "engine cfg" arm and assert it reproduces the harness rule row-for-row (drift guard, same reason `analysis/scorecard.py` exists) |

**No change needed** in `engine_adapter.py` (`prior_final_*` already attached at
`engine_adapter.py:132-133`; `replenishment_policy` already rides in `bom_rows.payload`),
in the frontend (`ReasonCodes` renders raw codes), or in the DB schema.

## NOT Building

- **No default-on.** Both levers ship OFF. Turning them on is a separate owner decision,
  the same way `SL_BY_CRIT` and the dormant rules were.
- **No stocking ladder / spare floor.** Measured and rejected: on the contested rows the
  engineer sides with the current level in every split.
- **No change to `route` classification.** The `dying` label is mis-assigned on 326 of 454
  rows (they consumed within the year) — real, parked, tracked separately.
- **No change to dormant or no-data rows.** That is `dormant_rules`' question (s22).
- **No change to `review_required`, confidence, or the auto-clear policy.** A snapped row
  is a no-op vs current, which `AUTOCLEAR_NOOP_ABS/REL` already covers and which is
  itself default-off.
- **No re-scoring of historical batches.** `score_batch` deliberately refuses to overwrite
  a reviewed row (`engine_adapter.py:150-190`).
- **No new dependency.**

---

## Step-by-Step Tasks

### Task 1: Declare the two levers

- **ACTION**: Add two constants to the lever block in `backend/app/engine_statistical.py`.
- **IMPLEMENT**: After `POLICY_EXCESS_NETTING` (line 73), append:
  ```python
  # --- Live-route anchoring (analysis/s26_small_delta_tuning.py). At TCB demand
  # rates the lead-time quantile has no resolution left: it returns Max=1 on 70% of
  # live rows, and the engineers wrote 0/1/2/3/4 inside that one answer. These two
  # levers stop the engine proposing a move it cannot justify. Both default OFF.
  CONTINUITY_SNAP = 0          # hold the current level when |Max - max_qty| <= this (0 = off)
  PRIOR_ANCHOR_POLICY = ""     # replenishment_policy substring whose parts anchor on
                               # the engineer's own last decision instead ("" = off)
  ```
- **MIRROR**: `LEVER_DECLARATION`.
- **IMPORTS**: none.
- **GOTCHA**: `CONTINUITY_SNAP = 0` must mean *off*, not "snap only on an exact tie" —
  the guard in Task 3 is `snap > 0 and ...`, so a band of 0 never fires.
- **VALIDATE**: `python -c "from app.engine_statistical import CONTINUITY_SNAP, PRIOR_ANCHOR_POLICY; print(CONTINUITY_SNAP, repr(PRIOR_ANCHOR_POLICY))"` from `backend/` prints `0 ''`.

### Task 2: Read the cfg keys and the policy column

- **ACTION**: Add two cfg reads and one text-column read in `run()`.
- **IMPLEMENT**: After `dormant_rules = cfg.get(...)` (line 305):
  ```python
  snap_band = float(cfg.get("continuity_snap", CONTINUITY_SNAP))
  anchor_policy = str(cfg.get("prior_anchor_policy", PRIOR_ANCHOR_POLICY)).strip().lower()
  ```
  and beside the other text columns (after line 328):
  ```python
  # Order-To-Demand parts are not held to a Max in SAP, so max_qty is a stale
  # field for them and the engineer's own last decision is the better anchor.
  repl = df.get("replenishment_policy", pd.Series("", index=df.index)).astype(str)
  ```
- **MIRROR**: `CFG_READ`, `TEXT_COLUMN_READ`.
- **IMPORTS**: none.
- **GOTCHA**: use `df.get(..., pd.Series("", index=df.index))` — a bare `run()` in the
  tests has no `replenishment_policy` column and `df["replenishment_policy"]` would raise.
- **VALIDATE**: `make test` still green (no behaviour change yet).

### Task 3: Apply the snap inside the live branch

- **ACTION**: Insert the anchoring block in the `else:  # active / dying` branch, directly
  after `new_max = int(math.ceil(new_max / moq) * moq)` (line 415) and **before** the route
  reason codes at line 417.
- **IMPLEMENT**:
  ```python
            # --- live-route anchoring (both levers default off) -------------
            # The quantile above is worth trusting only when it clears the level
            # already in force by more than its own resolution. Inside the band,
            # propose what is in force -- or, for a part SAP does not manage to a
            # Max, what this engineer last decided (analysis/s26).
            if snap_band > 0 and pd.notna(cur) and abs(new_max - cur) <= snap_band:
                anchored = None
                if (anchor_policy and anchor_policy in repl.iloc[i].strip().lower()
                        and pd.notna(prior_max.iloc[i]) and pd.notna(prior_rop.iloc[i])):
                    anchored = (prior_min.iloc[i], prior_rop.iloc[i], prior_max.iloc[i])
                    reasons.append("PRIOR_ANCHOR")
                else:
                    anchored = (cur_min.iloc[i], cur_rop.iloc[i], cur)
                    reasons.append("CONTINUITY_SNAP")
                # All THREE levels move together: line 450 re-raises ROP to Min,
                # so leaving a quantile Min behind would silently undo the snap.
                a_min, a_rop, a_max = anchored
                new_min = 0 if pd.isna(a_min) else max(int(a_min), 0)
                new_rop = new_min if pd.isna(a_rop) else max(int(a_rop), 0)
                new_max = new_rop if pd.isna(a_max) else max(int(a_max), 0)
  ```
- **MIRROR**: `POLICY_CLAMP_WITH_REASON_CODE` for the reason-code style;
  `RULE_WINS_OVER_ENGINE` for "an anchor replaces the computed number".
- **IMPORTS**: none (`pd`, `math` already imported).
- **GOTCHA (the important one)**: lines 449-451 run
  `new_rop = max(new_rop, new_min)` then `new_max = max(new_max, new_rop)`. If you snap
  only Max and ROP and leave the quantile's `new_min`, that clamp pushes ROP — and then
  Max — back up, and the snap silently does nothing on exactly the rows it was written
  for. Set all three from the anchor.
- **GOTCHA**: place the block *before* line 417 so `BIG_CHANGE` (line 429) correctly does
  not fire — after the snap there is no change to call big.
- **GOTCHA**: `cur` is `cur_max.iloc[i]`, already bound at line 347. Do not re-read it.
- **GOTCHA**: the DOI cap and excess netting (lines 438-447) still run afterwards and may
  clamp a snapped number. That is deliberate — they are budget instruments and both
  default off — but say so in the code comment.
- **VALIDATE**: `make test` green; then
  `python -c "import pandas as pd,sys; sys.path.insert(0,'backend'); from app.engine_statistical import run; print(run(pd.DataFrame([{'item_id':'A','max_qty':'1','rop_qty':'0','min_qty':'0','unitprice':'10','contractual_lead_time':'30','order_qty_multiple':'1','sfm_criticality':'M','frequencymonthswithusage':'8','last_90_day_cnsmptn_qty':'3','last_365_day_cnsmptn_qty':'12'}]),{'continuity_snap':1}).iloc[0][['factory_recommended_new_max','reason_code']])"`
  shows `CONTINUITY_SNAP` in the reason codes.

### Task 4: Tests for the continuity snap

- **ACTION**: Add to `backend/tests/test_statistical_engine.py`, after
  `test_excess_netting_reduces_max` (line 354).
- **IMPLEMENT**:
  ```python
  def test_continuity_snap_holds_the_current_level():
      base = _row(max_qty=1, rop_qty=0, min_qty=0, **_CONST)
      default = _run([base]).iloc[0]
      snapped = _run_cfg([base], {"continuity_snap": 1}).iloc[0]
      assert abs(default["factory_recommended_new_max"] - 1) <= 1, "fixture must sit in the band"
      assert snapped["factory_recommended_new_max"] == 1
      assert snapped["factory_recommended_new_rop"] == 0
      assert snapped["factory_recommended_new_min"] == 0
      assert "CONTINUITY_SNAP" in snapped["reason_code"]
      assert snapped["factory_recommendation_action"] == "Maintain"


  def test_continuity_snap_leaves_a_far_engine_number_alone():
      base = _row(max_qty=99, rop_qty=50, min_qty=10, **_HI)
      default = _run([base]).iloc[0]
      snapped = _run_cfg([base], {"continuity_snap": 1}).iloc[0]
      assert snapped["factory_recommended_new_max"] == default["factory_recommended_new_max"]
      assert "CONTINUITY_SNAP" not in snapped["reason_code"]


  def test_snap_carries_min_so_the_monotonic_clamp_cannot_undo_it():
      """Regression: snapping Max+ROP but not Min lets line 450 re-raise ROP."""
      base = _row(max_qty=1, rop_qty=0, min_qty=0, **_CONST)
      r = _run_cfg([base], {"continuity_snap": 1}).iloc[0]
      assert (r["factory_recommended_new_max"], r["factory_recommended_new_rop"],
              r["factory_recommended_new_min"]) == (1, 0, 0)


  def test_snap_does_not_touch_dormant_rows():
      dormant = _row(item_id="D", max_qty=4, rop_qty=2, min_qty=1)
      default = _run([dormant]).iloc[0]
      snapped = _run_cfg([dormant], {"continuity_snap": 1}).iloc[0]
      assert default["route"] == "dormant"
      assert snapped["factory_recommended_new_max"] == default["factory_recommended_new_max"]
      assert "CONTINUITY_SNAP" not in snapped["reason_code"]
  ```
- **MIRROR**: `LEVER_TEST`, `TEST_STRUCTURE`.
- **IMPORTS**: none beyond the module's existing ones. `_HI` is already defined in the
  file (used by `test_doi_cap_limits_max`); reuse it, do not redefine.
- **GOTCHA**: `_row` stringifies every value, and `max_qty` defaults to `"2"` — pass
  `max_qty=1` explicitly in the snap fixtures or the band assertion becomes ambiguous.
- **VALIDATE**: `make test` — four new tests pass.

### Task 5: Tests for the prior anchor

- **ACTION**: Append three more tests in the same place.
- **IMPLEMENT**:
  ```python
  _OTD = dict(replenishment_policy="Order To Demand",
              prior_final_max=2, prior_final_rop=1, prior_final_min=0)


  def test_prior_anchor_beats_the_current_level_for_order_to_demand():
      base = _row(max_qty=1, rop_qty=0, min_qty=0, **_CONST, **_OTD)
      r = _run_cfg([base], {"continuity_snap": 1, "prior_anchor_policy": "demand"}).iloc[0]
      assert r["factory_recommended_new_max"] == 2
      assert r["factory_recommended_new_rop"] == 1
      assert "PRIOR_ANCHOR" in r["reason_code"]
      assert "CONTINUITY_SNAP" not in r["reason_code"]


  def test_prior_anchor_ignores_order_to_max_parts():
      base = _row(max_qty=1, rop_qty=0, min_qty=0, **_CONST, **_OTD)
      base["replenishment_policy"] = "Order To Max"
      r = _run_cfg([base], {"continuity_snap": 1, "prior_anchor_policy": "demand"}).iloc[0]
      assert r["factory_recommended_new_max"] == 1
      assert "CONTINUITY_SNAP" in r["reason_code"]


  def test_prior_anchor_falls_back_to_current_without_a_prior():
      base = _row(max_qty=1, rop_qty=0, min_qty=0, **_CONST,
                  replenishment_policy="Order To Demand")
      r = _run_cfg([base], {"continuity_snap": 1, "prior_anchor_policy": "demand"}).iloc[0]
      assert r["factory_recommended_new_max"] == 1
      assert "CONTINUITY_SNAP" in r["reason_code"]
  ```
- **MIRROR**: `LEVER_TEST`; the `_prior(...)` fixtures at
  `backend/tests/test_statistical_engine.py:150-178` show how `prior_final_*` is passed
  through `_row`.
- **IMPORTS**: none.
- **GOTCHA**: the anchor must fire **only inside the snap band** — a prior decision far
  from both the engine and the current level is not evidence, it is an old number. The
  `snap_band` guard in Task 3 already enforces this; the second test pins it.
- **VALIDATE**: `make test` — three new tests pass.

### Task 6: Extend the defaults-off guard

- **ACTION**: Add the two new keys to `test_levers_default_off_match_baseline`
  (`backend/tests/test_statistical_engine.py:356`).
- **IMPLEMENT**: extend the cfg dict:
  ```python
      same = _run_cfg(rows, {"demand_estimator": "single", "trend_adjust": False,
                             "lead_time_sigma": False,
                             "service_level_mode": "criticality",
                             "continuity_snap": 0, "prior_anchor_policy": ""}).iloc[0]
  ```
  and add a second assertion that no new reason code leaked in:
  ```python
      assert "CONTINUITY_SNAP" not in base["reason_code"]
      assert "PRIOR_ANCHOR" not in base["reason_code"]
  ```
- **MIRROR**: `DEFAULTS_OFF_GUARD`.
- **IMPORTS**: none.
- **GOTCHA**: this is the test that stops a future default flip from shipping silently.
  Do not weaken it to `>=`.
- **VALIDATE**: `make test`.

### Task 7: Parity guard between the engine and the analysis harness

- **ACTION**: In `analysis/s26_small_delta_tuning.py`, add an arm that calls the real
  engine with the cfg and assert it equals the harness's `snap_prior()` row-for-row.
- **IMPLEMENT**: after the `cands` dict is built:
  ```python
      # The engine must reproduce the harness rule exactly, or the number quoted in
      # this file is not the number the product would ship. Same guarantee
      # analysis/scorecard.py gives for the tolerance.
      eng = E.run(df, {"continuity_snap": 1, "prior_anchor_policy": "demand"})
      eng_pair = _pair(eng)
      want_mx, want_rp = cands["snap-1 + prior anchor"]
      assert np.array_equal(eng_pair[live], np.c_[want_mx, want_rp][live]), \
          "engine_statistical drifted from the s26 rule"
      cands["engine cfg (parity)"] = (eng_pair[:, 0], eng_pair[:, 1])
  ```
- **MIRROR**: the self-check pattern at `analysis/s26_small_delta_tuning.py:62-65` (`_selfcheck`) and
  `analysis/s20_diverge_rootcause.py:_selfcheck`.
- **IMPORTS**: none new.
- **GOTCHA**: the harness computes `prior` from `_month` ordering over the pickle, while
  the engine reads `prior_final_*` **columns**. The pickle has no such columns, so before
  the `E.run` call the script must write them onto `df`:
  ```python
      df["prior_final_max"], df["prior_final_rop"], df["prior_final_min"] = pmax, prop, pmin
  ```
  (`pmin` needs adding to the existing prior loop alongside `pmax`/`prop`.)
  Without this the engine's anchor never fires and the assertion fails for the wrong reason.
- **GOTCHA**: `prior_final_*` written as columns will ALSO feed
  `_benchmark()` / `_agreement()` — that is already true of the real product path and does
  not change sizing (`test_prior_benchmark_never_changes_sizing`), so it is safe here.
- **VALIDATE**: `.venv/Scripts/python.exe analysis/s26_small_delta_tuning.py` runs to
  completion and prints the `engine cfg (parity)` column with the same figures as
  `snap-1 + prior anchor` (44.9 / 53.4 / 57.9).

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_continuity_snap_holds_the_current_level` | `max_qty=1`, constant consumer, `continuity_snap=1` | Max 1 / ROP 0 / Min 0, `CONTINUITY_SNAP`, action `Maintain` | no |
| `test_continuity_snap_leaves_a_far_engine_number_alone` | `max_qty=99`, high-volume row | number unchanged, no snap code | no |
| `test_snap_carries_min_so_the_monotonic_clamp_cannot_undo_it` | same as #1 | exactly `(1, 0, 0)` | **yes** — the clamp interaction |
| `test_snap_does_not_touch_dormant_rows` | all windows zero, `max_qty=4` | identical to default, no snap code | **yes** — route isolation |
| `test_prior_anchor_beats_the_current_level_for_order_to_demand` | `Order To Demand` + prior 2/1/0 | Max 2 / ROP 1, `PRIOR_ANCHOR` only | no |
| `test_prior_anchor_ignores_order_to_max_parts` | `Order To Max` + prior 2/1/0 | Max 1, `CONTINUITY_SNAP` | **yes** — policy gate |
| `test_prior_anchor_falls_back_to_current_without_a_prior` | `Order To Demand`, no prior columns | Max 1, `CONTINUITY_SNAP` | **yes** — missing-column path |
| `test_levers_default_off_match_baseline` (extended) | both keys at their defaults | byte-identical to baseline, neither code present | **yes** — no silent regression |

### Edge Cases Checklist

- [x] Empty input — `run()` on an empty frame already returns an empty frame (unchanged path)
- [x] Missing `replenishment_policy` column — `df.get(...)` default (Task 2 gotcha)
- [x] Missing `prior_final_*` columns — `_num` yields all-NaN, `pd.notna` guard falls back
- [x] `NaN` current level (`max_qty` blank) — `pd.notna(cur)` guard skips the snap entirely
- [x] Snap would produce ROP > Max — impossible: all three come from one anchor and are
      re-clamped at lines 450-451 as a no-op
- [x] `continuity_snap` passed as a string from a config row — `float(cfg.get(...))` coerces
- [ ] Concurrent access — N/A, `run()` is pure over a DataFrame
- [ ] Network failure — N/A, engine holds no connection by design

---

## Validation Commands

### Static Analysis
```bash
.venv/Scripts/python.exe -m py_compile backend/app/engine_statistical.py analysis/s26_small_delta_tuning.py
```
EXPECT: no output (the repo has no type checker configured for the backend)

### Unit Tests — affected area
```bash
.venv/Scripts/python.exe -m pytest backend/tests/test_statistical_engine.py -q
```
EXPECT: all pass, including the 7 new tests

### Full Test Suite
```bash
make test
```
EXPECT: no regressions — 271 tests green before the change, 278 after

### Analysis Parity (the real acceptance test)
```bash
.venv/Scripts/python.exe analysis/s26_small_delta_tuning.py
```
EXPECT: the parity assertion in Task 7 holds, and the `engine cfg (parity)` arm reports
`all live 44.9 · delta<=1 57.9 · delta<=2 53.4`

### Regression against the shipped configuration
```bash
.venv/Scripts/python.exe analysis/s20_diverge_rootcause.py
```
EXPECT: unchanged from today — baseline live 30.9%, all% 79.7%, stock $2,533,613.
Both levers are off by default, so **every number in this script must be identical**.

### Manual Validation
- [ ] `make backend`, upload a TCB batch, score it — batch summary `top_reason_codes`
      shows neither new code (levers off)
- [ ] Re-score with `continuity_snap: 1` in the active config row — item detail shows
      `CONTINUITY_SNAP` chips and the action column flips to `Maintain` on those rows
- [ ] Confirm `analysis/output/s25_ladder_jan26_changed_rows.csv` parts that snap are ones
      a reviewer agrees should not have moved

---

## Acceptance Criteria

- [ ] All 7 tasks completed
- [ ] All validation commands pass
- [ ] 7 new tests written and passing; defaults-off guard extended
- [ ] `s20_diverge_rootcause.py` output byte-identical to today (levers default off)
- [ ] `s26_small_delta_tuning.py` parity assertion passes
- [ ] No lint errors (line length ≤ 100 to match the file)
- [ ] Reason codes render in the UI without a frontend change

## Completion Checklist

- [ ] Code follows the lever-declaration / cfg-read / reason-code patterns above
- [ ] Comments explain *why* (the Max=1 collapse), with the analysis script named
- [ ] Tests follow `_row` / `_run_cfg` structure
- [ ] No hardcoded values — both thresholds are module constants overridable via cfg
- [ ] `docs/CURRENT_CODEBASE_END_TO_END.md` engine section updated with the two levers
- [ ] No unnecessary scope additions (see NOT Building)
- [ ] Self-contained — no codebase searching needed during implementation

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The monotonic clamp silently undoes the snap | **High** if Min is not carried | Feature does nothing on its target rows | Task 3 gotcha + dedicated regression test |
| `prior_final_*` absent on a bare `run()` → anchor never fires in tests | High | False confidence | `test_prior_anchor_falls_back_to_current_without_a_prior` pins the fallback explicitly |
| Harness and engine drift apart | Medium | Quoted figures stop describing the product | Task 7 parity assertion fails the run |
| Gain is carried by two inertia-heavy cycles (Oct/Dec '24) | **Certain** — measured | Live performance may undershoot 57.9% | Levers ship OFF; enable per batch and measure; per-cycle table is in the plan's evidence |
| `replenishment_policy` wording changes upstream | Low | Anchor stops firing (fails safe to the snap) | Substring match, not equality; value is config, not code |
| An engineer reads `Maintain` as "the engine has no opinion" | Medium | Under-review of genuinely wrong levels | `review_required` is untouched — snapped rows still carry their route's review flag |

## Notes

- **Evidence**: `analysis/s26_small_delta_tuning.py` (8 cycles, 9,054 decisions,
  711 live rows). Low-delta band `δ≤1` = 549 rows covering 67% of live divergence,
  frozen at the shipped engine so no candidate can win by changing its own denominator.
- **Rejected alternatives**: the stocking ladder (spare floor of Max 2 / ROP 1 on moving
  parts) — steadier per cycle (7/8 vs 5/8) but caps at 48.6% on the band; anchoring on
  `sfm_brr_*` — 55.4% on live rows but rejected by the owner on 2026-09-10 because the
  column's provenance is unknown; `prior > current & c365 > 0 → propose 2` — 58.1%
  in-sample but 51.5% leave-one-month-out, a fishing artifact.
- **Why `Order To Demand` and not a learned rule**: the cell probe in
  `s23_live_route_autopsy.py` §3 shows no partition of the engine's own features beats a
  single global policy out of sample. This one lever is the only split that survived
  leave-one-month-out, and it has a mechanism: SAP does not hold those parts to a Max, so
  `max_qty` is stale for them (212 of 508 live rows with a prior have `current < prior`).
- **Ceiling**: 57.9% is the measured wall for this family. 84% of what remains is the
  engineer moving up 1-2 units from current, and nothing available predicts it — the best
  discriminator found lifts the move rate from a 42% base to 51%. Past this needs the
  lifecycle inputs (constraint tool / EOL / sustaining / ramp) that diverge at 90-100%.
