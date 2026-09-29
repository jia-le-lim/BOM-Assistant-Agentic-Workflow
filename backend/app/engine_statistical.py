"""Statistical intermittent-demand sizing engine (PRD v3.2, ported from s13).

Reproducible, distribution-based Min/ROP/Max for spare parts. Treats each row's
trailing-consumption windows (last_5/30/90/180/365/547_day) as multi-horizon
demand-RATE estimators -- never a time series -- then sizes stock from a
lead-time-demand distribution at a criticality-scaled service level.

Same call signature and output schema as analysis/engine/engine.py, so
engine_adapter can swap between the rule engine and this one with no other
change. Adds three review-support signals on top of the sizing:

  1. review band  -- high-volume / policy-driven parts are flagged for a human
                     instead of being trusted to the statistical number.
  2. confidence   -- 0-1 score per row (high for steady low-volume consumers).
  3. reason       -- machine reason_code(s) + a plain-English explanation.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

from . import dormant_rules as DR

# Bump this whenever a DEFAULT changes the proposed Min/ROP/Max. It is stamped
# onto every scored row (engine_adapter.score_batch) and is the only way to tell,
# months later, which engine produced a number -- the config hash cannot help,
# because module constants never enter cfg.
#   stat-v1  original: criticality service level 0.99/0.95/0.90, pure quantile.
#   stat-v2  2026-09-10: SL_BY_CRIT recalibrated to 0.90/0.85/0.80, and live-route
#            anchoring (CONTINUITY_SNAP / PRIOR_ANCHOR_POLICY) on by default.
MODEL_VERSION = "stat-v2"

WINDOWS = [5, 30, 90, 180, 365, 547]
CONS = {w: f"last_{w}_day_cnsmptn_qty" for w in WINDOWS}

# Sizing constants (PRD v3.2 5.x).
T_REVIEW = 30                  # review / protection period, days
DEFAULT_LT = 30                # fallback lead time when the row has none
PHI_ACTIVE = 1.5               # conservative variance-to-mean floor, active parts
PHI_DYING = 3.0                # ...and for parts whose demand has ceased
KEEP_ALIVE = 1                 # insurance stock for critical dormant parts
HIGH_VOLUME_PER_DAY = 0.1      # mu_day above this -> human review
REGULAR_FREQ = 6               # frequencymonthswithusage >= this -> Poisson candidate
REGULAR_CV2 = 0.5              # ...and pseudo_cv2 below this
BIG_CHANGE_FRAC = 0.5          # |new-cur| beyond this share of current -> review
# Service level by criticality. Calibrated against 8 months of TCB engineer
# decisions (analysis/s20_diverge_rootcause.py): their revealed target is ~0.85,
# not the 0.99/0.95/0.90 this table shipped with. Under the old table active-part
# agreement was 39.3% and the proposed book was $3.37M; this one reaches 54.5%
# and $2.53M, and improves in all eight months independently.
#
# This is a stocking-risk decision, not only a fit: a lower service level is a
# genuinely higher stockout probability, and no fill-rate backtest exists yet to
# bound it (ML plan Phase 4). Set deliberately by the owner, 2026-08-28.
SL_BY_CRIT = {"h": 0.90, "m": 0.85, "l": 0.80, "d": 0.80}
SL_DEFAULT = 0.85

# Agreement tolerance -- see close_enough(). Relative only, no absolute floor,
# graded on Max and ROP. s19, s20 and analysis/scorecard.py import AGREE_TOL
# rather than restating it, so the review scorecard cannot drift from the engine.
# analysis/common.agree() deliberately does NOT: it keeps the old max(1, 10%) on
# all three levels because it answers a different question (would a human review
# have added nothing?) -- see the coupling note at analysis/common.py:109.
AGREE_TOL = 0.10
AGREE_FIELDS = ("max", "rop")

# --- Demand-model + policy levers (PRD v3.2 A/B improvements). Each is opt-in
# via cfg and calibrated through analysis/s13 + s6_backtest; every default in
# THIS block is off, so none of them alone changes a bare run(). (The live-route
# anchoring levers below are the exception -- they ship on, which is why
# MODEL_VERSION moved to stat-v2.) ---
DEMAND_ESTIMATOR = "single"         # "single" (first populated window) | "blended"
TREND_ADJUST = False                # lift mu toward the recent rate on a ramp
TREND_BAND = (0.8, 1.2)             # 90d/365d momentum inside this band = stable
LEAD_TIME_SIGMA = False             # widen dispersion for lead-time variability
SERVICE_LEVEL_MODE = "criticality"  # "criticality" | "cost_aware" (newsvendor)
HOLDING_COST_RATE = 0.25            # annual holding cost as a fraction of price
STOCKOUT_COST_BY_CRIT = {"h": 100000.0, "m": 5000.0, "l": 500.0, "d": 250.0}
SL_FLOOR, SL_CEIL = 0.50, 0.999     # clamp on the cost-aware service level
POLICY_MAX_DOI_DAYS = 0.0           # cap Max at this many days of demand (0 = off)
POLICY_EXCESS_NETTING = False       # net shareable excess off the proposed Max

# --- Live-route anchoring (analysis/s26_small_delta_tuning.py). At TCB demand
# rates the lead-time quantile has no resolution left: median c365 is 1 unit/year,
# so mu_LT lands near 0.3 and the quantile rounds to 0 or 1. The engine returned
# Max=1 on 499 of 711 live rows and the engineers wrote 0, 1, 2, 3 and 4 inside
# that one answer -- it was emitting the `rop + moq` floor, not sizing.
#
# ON by default since 2026-09-10 (owner decision). Over eight TCB cycles this
# takes live-row agreement 30.9% -> 44.4%, and 40.1% -> 57.4% on the rows where
# the old engine already landed within one unit of the engineer; proposals fall
# 481 -> 201 while the hit rate on them rises 18.3% -> 19.4%, and proposed stock
# falls $2.53M -> $2.36M. It wins five cycles of eight and loses 2024-10, where
# the engineers held nearly every level and inertia alone was already 85.7%.
# (An unguarded first cut scored 45.0 / 58.1; the refusals below -- incomplete or
# non-monotonic level sets, and critical parts -- cost 0.6-0.7 pp of that.)
#
# Like SL_BY_CRIT this is a stocking decision, not only a fit: holding a level
# the quantile wanted to raise is a real service risk, and no fill-rate backtest
# exists yet to bound it (ML plan Phase 4). Set 0 / "" to restore the old sizing.
CONTINUITY_SNAP = 1          # hold the current level when |Max - max_qty| <= this (0 = off)
PRIOR_ANCHOR_POLICY = "demand"   # replenishment_policy substring whose parts anchor on
                                 # the engineer's own last decision instead ("" = off)

# Auto-clear policy (PRD v3 Phase 5), overridable via cfg. Auto-clear only
# decides whether a HUMAN sees a row -- it never changes Min/ROP/Max, so the
# sizing accuracy is invariant to this policy.
#
# Defaults are OFF: s15 calibration on Jan'26 showed the engine's auto-clear
# only matches the engineers ~67% of the time and expanding it made that worse
# (many parts the engineer proactively moved off current), so on this data the
# levers cannot clear a safe precision bar. They stay config-gated until denser
# data justifies switching them on -- re-run analysis/s15_autoclear_calibration.py.
AUTOCLEAR_NOOP_ABS = 0.0        # |engine - current| within this many units ...
AUTOCLEAR_NOOP_REL = 0.0        # ... or this fraction -> treat as no-op (0 = off)
AUTOCLEAR_IMMATERIAL_USD = 0.0  # exposure below this auto-clears (0 = off)
AUTOCLEAR_HIGH_VALUE_USD = 5000.0   # material change at/above this -> always review
AUTOCLEAR_RELIABLE = False      # reliable-stable lever: opt-in until calibrated

# Benchmark ladder (see _benchmark). A prior review is only a fair yardstick
# while demand has not moved out from under it -- beyond this drift the engine
# would score a "match" for reproducing a decision the data has invalidated.
PRIOR_BENCH_MAX_DRIFT = 0.25    # |c365_now - c365_then| as a fraction of then

# Engineer-owned dormant stocking rules, resolved by engine_adapter and handed
# in through cfg. Empty is today's behaviour exactly: no rule matches, the
# existing dormant branch runs untouched. The engine holds no database handle
# and this is how it stays that way (see dormant_rules).
DORMANT_RULES: list[dict] = []


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _sl(crit) -> float:
    return SL_BY_CRIT.get(str(crit).strip().lower()[:1], SL_DEFAULT)


def _is_critical(crit) -> bool:
    return str(crit).strip().lower()[:1] == "h"


def _mu_day(w: dict[int, float]) -> float:
    for win in (365, 547, 180, 90, 30):
        v = w[win]
        if pd.notna(v):
            return max(v / win, 0.0)
    return float("nan")


def _pseudo_cv2(w: dict[int, float]) -> float:
    edges = [(30, 0, 30), (90, 30, 60), (180, 90, 90), (365, 180, 185), (547, 365, 182)]
    rates = []
    for hi, lo, span in edges:
        a, b = w[hi], (0.0 if lo == 0 else w[lo])
        if pd.notna(a) and pd.notna(b):
            rates.append(max(a - b, 0.0) / span)
    if len(rates) < 2:
        return 0.0
    arr = np.asarray(rates, float)
    m = arr.mean()
    return float(arr.var(ddof=0) / m**2) if m > 0 else 0.0


def _mu_blended(w: dict[int, float]) -> float:
    """Recency-weighted daily rate over every populated window (PRD v3.2 A1).

    Each window is one estimate of the same rate; weighting the shorter, more
    recent windows higher lets a ramp show up sooner than a flat long-run mean.
    Falls back to the single-window pick when only one window is present.
    """
    weights = {5: 0.0, 30: 3.0, 90: 3.0, 180: 2.0, 365: 1.5, 547: 1.0}
    num = den = 0.0
    for win, wt in weights.items():
        v = w.get(win)
        if wt > 0 and pd.notna(v):
            num += wt * max(v / win, 0.0)
            den += wt
    return num / den if den > 0 else _mu_day(w)


def _momentum(w: dict[int, float]) -> float:
    """Recent (90d) rate / long-run (365d) rate: >1 ramping, <1 declining."""
    r90 = w[90] / 90 if pd.notna(w[90]) else float("nan")
    r365 = w[365] / 365 if pd.notna(w[365]) else float("nan")
    if pd.notna(r90) and pd.notna(r365) and r365 > 0:
        return r90 / r365
    return float("nan")


def _estimate_mu(w: dict[int, float], estimator: str, trend_on: bool) -> float:
    mu = _mu_blended(w) if estimator == "blended" else _mu_day(w)
    if trend_on and pd.notna(mu) and mu > 0:
        m = _momentum(w)
        if pd.notna(m) and m > TREND_BAND[1]:
            mu *= min(m, 2.0)          # lift toward the recent rate, capped at 2x
    return mu


def _service_level(crit, price, mode: str, holding_rate: float,
                   stockout_by_crit: dict, ownership: str, L: float) -> float:
    """Criticality table (default) or a newsvendor critical ratio (PRD v3.2 B).

    Cost-aware SL* = Cu / (Cu + Co): Co is the per-unit holding cost over the
    order horizon, Cu the criticality-scaled shortage cost. Cheap parts get a
    near-1 SL (cheap to hold), expensive ones a lower SL (holding dominates).
    """
    if mode != "cost_aware":
        return _sl(crit)
    key = str(crit).strip().lower()[:1]
    cu = float(stockout_by_crit.get(key, stockout_by_crit.get("m", 5000.0)))
    unit = float(price) if pd.notna(price) and price > 0 else 0.0
    co = unit * holding_rate * ((L + T_REVIEW) / 365.0)
    if "consign" in str(ownership).strip().lower():
        co *= 0.1                      # consignment carries little holding cost
    if cu + co <= 0:
        return _sl(crit)
    return float(min(SL_CEIL, max(SL_FLOOR, cu / (cu + co))))


def _lt_dispersion(L: float, lt_alt: float) -> float:
    """phi multiplier (>=1) for lead-time variability (PRD v3.2 A4).

    The gap between the contractual and SFM-mean lead time is a cheap proxy for
    lead-time sigma; a volatile lead time widens the demand distribution.
    """
    if pd.notna(lt_alt) and lt_alt > 0 and L > 0:
        return 1.0 + min(abs(L - lt_alt) / L, 1.0)
    return 1.0


def _quantile(mean: float, sl: float, phi: float, use_poisson: bool) -> int:
    if mean <= 0:
        return 0
    if use_poisson:
        return int(poisson.ppf(sl, mean))
    r = mean / (phi - 1.0)
    p = 1.0 / phi
    return int(nbinom.ppf(sl, r, p))


def _action(new_max: int, cur_max: float) -> str:
    if pd.isna(cur_max) or new_max == cur_max:
        return "Maintain"
    return "Increase" if new_max > cur_max else "Decrease"


def _within(e: float, base: float, tol_abs: float, tol_rel: float) -> bool:
    """Is engine value e effectively equal to base (current level)?"""
    if pd.isna(base):
        return False
    return abs(e - base) <= max(tol_abs, tol_rel * abs(base))


def close_enough(engine: float, bench: float) -> bool:
    """THE agreement tolerance. One definition, imported by every harness.

    A strict relative band with no absolute floor, so small quantities are held
    to a tight tolerance: a benchmark of 2 admits only 2 (1.8-2.2), and a
    benchmark of 0 admits only 0. The previous max(1, 10%) floor hid a 1-unit
    disagreement on every small part -- on the TCB history that was 1,430 rows,
    1,179 of them the engine zeroing a part the engineer chose to keep.

    A missing benchmark is not a disagreement: there is nothing to grade
    against, so it passes and _agreement's 'none' check decides the row.
    """
    return True if pd.isna(bench) else abs(engine - bench) <= AGREE_TOL * abs(bench)


def _agreement(engine_vals, bench_vals) -> str:
    """Engine vs whichever benchmark _benchmark() selected, on Max and ROP.

    A post-hoc scoring signal (PRD v3 6) that drives triage/confidence only --
    the benchmark is never an input to sizing, so there is no target leakage.
    Returns 'match' | 'diverge' | 'none' (no benchmark on the row).

    Min is deliberately excluded: Max and ROP drive replenishment, Min is a
    derived floor (rop - mu_L), so grading it double-counts the same error.
    Callers still pass (max, rop, min) triples; the third is ignored.
    """
    pairs = list(zip(engine_vals, bench_vals))[:len(AGREE_FIELDS)]
    if not any(pd.notna(b) for _, b in pairs):
        return "none"
    return "match" if all(close_enough(e, b) for e, b in pairs) else "diverge"


def _drift_ok(c365_now: float, c365_then: float) -> bool:
    """Has this part's demand held still since the engineer last ruled on it?"""
    if pd.isna(c365_now) or pd.isna(c365_then):
        return False        # strict: this feeds auto-clear
    return abs(c365_now - c365_then) <= PRIOR_BENCH_MAX_DRIFT * max(abs(c365_then), 1.0)


def _benchmark(bench, prior, c365_now, c365_then):
    """Whose number are we grading against?

    The engineer's own for this cycle if the upload carries one; otherwise
    their last decision on this same part, while demand still supports it. A
    brand-new month has no factory_recommended_new_* at all, so without the
    prior-review rung every row scores "none" and every agreement-gated
    signal downstream goes dark.

    Returns (bench_vals, source) with source 'factory' | 'prior_review' | ''.
    """
    # Presence is tested on the GRADED fields only. Testing all three let a row
    # carrying just factory_recommended_new_min select source "factory", after
    # which _agreement saw two NaN benchmarks and returned "none" -- discarding a
    # usable prior-review benchmark the ladder would otherwise have reached.
    n = len(AGREE_FIELDS)
    if any(pd.notna(b) for b in bench[:n]):
        return bench, "factory"
    if any(pd.notna(p) for p in prior[:n]) and _drift_ok(c365_now, c365_then):
        return prior, "prior_review"
    # ponytail: 365d volume only; add a per-window drift check if seasonality bites.
    return (float("nan"),) * 3, ""


def run(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Size Min/ROP/Max for every row; returns the engine-standard schema."""
    cfg = cfg or {}
    rule_version = str(cfg.get("rule_version", "stat"))
    noop_abs = float(cfg.get("autoclear_noop_abs", AUTOCLEAR_NOOP_ABS))
    noop_rel = float(cfg.get("autoclear_noop_rel", AUTOCLEAR_NOOP_REL))
    immaterial_usd = float(cfg.get("autoclear_immaterial_usd", AUTOCLEAR_IMMATERIAL_USD))
    high_value_usd = float(cfg.get("autoclear_high_value_usd", AUTOCLEAR_HIGH_VALUE_USD))
    reliable_on = bool(cfg.get("autoclear_reliable", AUTOCLEAR_RELIABLE))

    # Demand-model + policy levers (each default off; see MODEL_VERSION).
    estimator = str(cfg.get("demand_estimator", DEMAND_ESTIMATOR))
    trend_on = bool(cfg.get("trend_adjust", TREND_ADJUST))
    lt_sigma_on = bool(cfg.get("lead_time_sigma", LEAD_TIME_SIGMA))
    sl_mode = str(cfg.get("service_level_mode", SERVICE_LEVEL_MODE))
    holding_rate = float(cfg.get("holding_cost_rate", HOLDING_COST_RATE))
    stockout_by_crit = cfg.get("stockout_cost_by_crit", STOCKOUT_COST_BY_CRIT)
    max_doi_days = float(cfg.get("policy_max_doi_days", POLICY_MAX_DOI_DAYS))
    excess_netting_on = bool(cfg.get("policy_excess_netting", POLICY_EXCESS_NETTING))
    dormant_rules = cfg.get("dormant_rules", DORMANT_RULES)
    snap_band = float(cfg.get("continuity_snap", CONTINUITY_SNAP))
    anchor_policy = str(cfg.get("prior_anchor_policy", PRIOR_ANCHOR_POLICY)).strip().lower()

    win = {w: _num(df, CONS[w]) for w in WINDOWS}
    lt = _num(df, "contractual_lead_time")
    moq_s = _num(df, "order_qty_multiple")
    price = _num(df, "unitprice")
    cur_max = _num(df, "max_qty")
    cur_rop = _num(df, "rop_qty")
    cur_min = _num(df, "min_qty")
    freq = _num(df, "frequencymonthswithusage")
    # Engineer benchmark -- scoring only, read AFTER sizing (no leakage, PRD 6).
    bench_max = _num(df, "factory_recommended_new_max")
    bench_rop = _num(df, "factory_recommended_new_rop")
    bench_min = _num(df, "factory_recommended_new_min")
    # Fallback benchmark: this part's last engineer decision, attached by
    # engine_adapter._attach_prior_benchmark (absent on a bare run()).
    prior_max = _num(df, "prior_final_max")
    prior_rop = _num(df, "prior_final_rop")
    prior_min = _num(df, "prior_final_min")
    prior_c365 = _num(df, "prior_c365")
    sfm_mean_lt = _num(df, "sfm_mean_lt_cd")
    excess_qty = _num(df, "qry_eoh_excess_qty")
    own = df.get("ownership", pd.Series("", index=df.index)).astype(str)
    crit = df.get("sfm_criticality", pd.Series("", index=df.index)).astype(str)
    item = df.get("item_id", pd.Series("", index=df.index)).astype(str)
    # Attached by engine_adapter from the confirmed part_category lexicon, the
    # same way prior_final_* is attached. Resolved there rather than here
    # because categorising needs the rule table, and the engine has no conn.
    category = df.get("part_category", pd.Series("", index=df.index)).astype(str)
    # Order-To-Demand parts are not held to a Max in SAP, so max_qty is a stale
    # field for them and the engineer's own last decision is the better anchor
    # (212 of 508 live rows with a prior carry current < prior). Read the same
    # way as the other text columns: a bare run() has no such column.
    repl = df.get("replenishment_policy", pd.Series("", index=df.index)).astype(str)

    out = []
    for i in range(len(df)):
        w = {ww: win[ww].iloc[i] for ww in WINDOWS}
        present = [w[ww] for ww in WINDOWS if pd.notna(w[ww])]
        c90, c365 = w[90], w[365]
        mu = _estimate_mu(w, estimator, trend_on)
        cv2 = _pseudo_cv2(w)
        criticality = crit.iloc[i]
        L = lt.iloc[i] if pd.notna(lt.iloc[i]) and lt.iloc[i] > 0 else DEFAULT_LT
        moq = int(moq_s.iloc[i]) if pd.notna(moq_s.iloc[i]) and moq_s.iloc[i] >= 1 else 1
        sl = _service_level(criticality, price.iloc[i], sl_mode,
                            holding_rate, stockout_by_crit, own.iloc[i], L)
        cur = cur_max.iloc[i]

        # --- routing + consumable class ---
        if not present:
            route, consumable = "no-data", "none"
        elif max(present) <= 0:
            route, consumable = "dormant", "none"
        elif pd.notna(c90) and c90 > 0:
            route = "active"
            consumable = "constant" if (pd.notna(c365) and c365 > 0) else "sporadic"
        else:
            route, consumable = "dying", "dying"

        reasons: list[str] = []
        review = "N"
        dist = "none"
        phi = float("nan")
        regular = False

        # --- sizing by route ---
        if route == "no-data":
            new_min = int(cur) if pd.notna(cur) else 0
            new_rop = new_min
            new_max = new_min
            review, dist = "Y", "none"
            conf, risk = 0.2, "Medium"
            reasons.append("NO_CONSUMPTION_DATA")

        elif route == "dormant":
            # The engineer-owned rule wins over the engine here (owner decision,
            # 2026-09-01). These are wear-and-tear parts: a constant quantity is
            # kept regardless of consumption, and the engine's zero is wrong on
            # 1,344 of 8,343 dormant rows across the TCB history.
            _rule = DR.resolve(dormant_rules, item.iloc[i], category.iloc[i])
            _applied = DR.apply(_rule, cur)
            if _applied is not None:
                new_min, new_rop, new_max = _applied
                # review stays "Y": the rule changes the NUMBER, not whether a
                # human looks. "N" here would silently clear 8,343 rows.
                review, dist, conf, risk = "Y", "rule", 0.5, "Low"
                reasons.append("DORMANT_RULE_APPLIED")
            elif _is_critical(criticality):
                new_min = KEEP_ALIVE
                new_rop = KEEP_ALIVE
                new_max = KEEP_ALIVE + moq
                review, dist, conf, risk = "Y", "insurance", 0.6, "Medium"
                reasons.append("DORMANT_CRITICAL_KEEPALIVE")
            else:
                new_min = new_rop = new_max = 0
                review, dist, conf, risk = "Y", "none", 0.4, "Low"
                reasons.append("DORMANT_NONCRITICAL_ZERO")

        else:  # active / dying -> rate-based sizing
            mu_L = mu * L
            mu_LT = mu * (L + T_REVIEW)
            regular = pd.notna(freq.iloc[i]) and freq.iloc[i] >= REGULAR_FREQ and cv2 < REGULAR_CV2
            lt_disp = _lt_dispersion(L, sfm_mean_lt.iloc[i]) if lt_sigma_on else 1.0
            if regular and lt_disp <= 1.0:
                use_poisson, phi = True, 1.0
                dist = "poisson"
            else:
                use_poisson = False
                phi = max(PHI_DYING if route == "dying" else PHI_ACTIVE, 1.0 + cv2) * lt_disp
                dist = "nbinom"
            new_rop = _quantile(mu_L, sl, phi, use_poisson)
            new_max = _quantile(mu_LT, sl, phi, use_poisson)
            new_min = max(math.ceil(new_rop - mu_L), 0)
            new_max = max(new_max, new_rop + moq)
            new_max = int(math.ceil(new_max / moq) * moq)

            # --- live-route anchoring (ON by default, see MODEL_VERSION) ------
            # The quantile above is worth trusting only where it clears the level
            # already in force by more than its own resolution. Inside the band,
            # propose what is in force -- or, for a part SAP does not manage to a
            # Max, what this engineer last decided (analysis/s26_small_delta_tuning).
            # Runs before the route reason codes so BIG_CHANGE cannot fire on a
            # row this has just decided not to change. The DOI cap and excess
            # netting below still apply: they are budget instruments, deliberately
            # allowed to override an anchor, and both default off.
            if snap_band > 0 and pd.notna(cur) and abs(new_max - cur) <= snap_band:
                if (anchor_policy and anchor_policy in repl.iloc[i].strip().lower()
                        and pd.notna(prior_max.iloc[i]) and pd.notna(prior_rop.iloc[i])):
                    anchored, code = ((prior_min.iloc[i], prior_rop.iloc[i],
                                       prior_max.iloc[i]), "PRIOR_ANCHOR")
                else:
                    anchored, code = ((cur_min.iloc[i], cur_rop.iloc[i], cur),
                                      "CONTINUITY_SNAP")
                # Anchor only on a level set that is COMPLETE and INTERNALLY
                # CONSISTENT. All three move together, because the
                # Max >= ROP >= Min clamp below would otherwise re-raise ROP to a
                # quantile Min and drag Max with it. That carry only holds if the
                # anchor is itself monotonic: a row carrying rop_qty > max_qty
                # (max_qty is blank or zero on much of the extract) would be
                # "snapped" and then clamped straight back up, shipping
                # CONTINUITY_SNAP on a row the engine did in fact move. A missing
                # level is refused for the same reason -- defaulting it to 0 would
                # propose never reordering an active part.
                a_min, a_rop, a_max = (float(v) if pd.notna(v) else float("nan")
                                       for v in anchored)
                usable = (all(pd.notna(v) for v in anchored)
                          and 0 <= a_min <= a_rop <= a_max)
                # A critical part never trades statistical protection for
                # agreement. Every other risk-bearing policy in this module carves
                # them out (auto-clear guards on _is_critical, dormant has
                # DORMANT_CRITICAL_KEEPALIVE), and the anchor is the one lever
                # that can LOWER a level -- on an "h" part that means reordering
                # later than the demand distribution says to.
                if usable and _is_critical(criticality) and a_max < new_max:
                    usable = False
                    reasons.append("CRITICAL_NO_ANCHOR")
                if usable:
                    new_min, new_rop, new_max = int(a_min), int(a_rop), int(a_max)
                    reasons.append(code)
                    if moq > 1 and new_max < new_rop + moq:
                        # The quantile guarantees Max >= ROP + MOQ so one order is
                        # placeable without overshooting Max. The level in force
                        # carries no such guarantee. Surface that rather than
                        # rounding the anchor away from the thing it anchors on.
                        reasons.append("MOQ_UNREACHABLE")

            if route == "dying":
                reasons.append("DYING_DEMAND")
                review, conf, risk = "Y", 0.5, "Medium"
            elif consumable == "sporadic":
                reasons.append("SPORADIC_DEMAND")
                review, conf, risk = "Y", 0.4, "Low"
            else:                                   # constant consumer
                reasons.append("CONSTANT_CONSUMER")
                conf, risk = 0.9, "Low"
                if mu > HIGH_VOLUME_PER_DAY:        # high-volume review band
                    reasons.append("HIGH_VOLUME_REVIEW")
                    review, conf, risk = "Y", 0.3, "High"
                elif pd.notna(cur) and abs(new_max - cur) > max(1.0, BIG_CHANGE_FRAC * cur):
                    reasons.append("BIG_CHANGE")
                    review = "Y"

        if _is_critical(criticality) and risk != "High":
            risk = "High"

        # --- policy clamps (structured columns only, default off; the forbidden
        # 5.1 free-text fields are never read) ---
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

        new_min, new_rop, new_max = int(new_min), int(new_rop), int(new_max)
        new_rop = max(new_rop, new_min)
        new_max = max(new_max, new_rop)

        # Engineer-benchmark agreement: raises trust when the engine reproduces
        # the engineer's own number, surfaces disagreements for the review queue.
        bench_vals, agreement_source = _benchmark(
            (bench_max.iloc[i], bench_rop.iloc[i], bench_min.iloc[i]),
            (prior_max.iloc[i], prior_rop.iloc[i], prior_min.iloc[i]),
            w[365], prior_c365.iloc[i])
        agreement = _agreement((new_max, new_rop, new_min), bench_vals)
        if agreement == "none":
            agreement_source = ""
        elif agreement_source == "factory":
            reasons.append("MATCHES_FACTORY" if agreement == "match"
                           else "DIVERGES_FACTORY")
            if agreement == "match":
                conf = min(0.95, conf + 0.1)
        else:
            reasons.append("MATCHES_PRIOR_REVIEW" if agreement == "match"
                           else "DIVERGES_PRIOR_REVIEW")
            if agreement == "match":
                conf = min(0.95, conf + 0.05)

        # --- auto-clear policy (triage only; never touches Min/ROP/Max) ---
        # Expand review=N only where an un-reviewed error is rare or cheap: a
        # no-op vs current, an immaterial part, or (opt-in) a reliable stable
        # estimate. Critical parts, big-money changes and the uncertain routes
        # (dying/dormant/no-data) are never auto-cleared.
        p = price.iloc[i]
        exposure = float(p * new_max) if pd.notna(p) else 0.0
        if review == "Y" and route == "active" and not _is_critical(criticality):
            noop = (_within(new_max, cur_max.iloc[i], noop_abs, noop_rel)
                    and _within(new_rop, cur_rop.iloc[i], noop_abs, noop_rel)
                    and _within(new_min, cur_min.iloc[i], noop_abs, noop_rel))
            if not (exposure >= high_value_usd and not noop):
                if noop:
                    review = "N"; reasons.append("NOOP_VS_CURRENT")
                elif exposure < immaterial_usd:
                    review = "N"; reasons.append("IMMATERIAL_VALUE")
                elif reliable_on and regular:
                    r90 = w[90] / 90 if pd.notna(w[90]) else float("nan")
                    r365 = w[365] / 365 if pd.notna(w[365]) else float("nan")
                    momentum = (r90 / r365 if pd.notna(r90) and pd.notna(r365)
                                and r365 > 0 else float("nan"))
                    if pd.notna(momentum) and 0.8 <= momentum <= 1.2:
                        review = "N"; reasons.append("RELIABLE_STABLE")

        explanation = _explain(route, consumable, mu, L, sl, dist,
                               new_min, new_rop, new_max, reasons)
        out.append({
            "item_id": item.iloc[i],
            "factory_recommended_new_max": new_max,
            "factory_recommended_new_rop": new_rop,
            "factory_recommended_new_min": new_min,
            "review_required": review,
            "factory_recommendation_action": _action(new_max, cur),
            "reason_code": ",".join(reasons),
            "risk_level": risk,
            "confidence_score": float(conf),
            "explanation": explanation,
            "_exposure_usd": exposure,
            "model_version": MODEL_VERSION,
            "rule_version": rule_version,
            # triage signals now persisted (see engine_adapter cols)
            "route": route,
            "consumable": consumable,
            "agreement": agreement,
            "agreement_source": agreement_source,
            # debug-only (ignored by adapter)
            "mu_day": round(mu, 5) if pd.notna(mu) else np.nan,
        })
    return pd.DataFrame(out)


def _explain(route, consumable, mu, L, sl, dist, mn, rp, mx, codes=()) -> str:
    """The sentence the reviewer reads. It has to describe what actually produced
    these numbers, so `codes` is the row's reason list: a rule-set or anchored
    quantity is never credited to the demand model."""
    if route == "no-data":
        return "No consumption data in any window -> kept current levels, flagged for review."
    if route == "dormant":
        if dist == "rule":
            return ("No consumption in any window -> levels set by the engineer-owned "
                    f"dormant rule for this part: Min {mn} / ROP {rp} / Max {mx}.")
        return ("Critical part with no consumption -> keep-alive insurance stock."
                if mx > 0 else
                "No consumption in any window -> proposed zero, flagged for review.")
    if "PRIOR_ANCHOR" in codes:
        return (f"Order-to-demand part, so the stocked Max is not managed in the source "
                f"system: kept this part's last engineer decision, Min {mn} / ROP {rp} / "
                f"Max {mx}. The {dist} model at {int(sl * 100)}% service landed inside the "
                f"continuity band of the current Max.")
    if "CONTINUITY_SNAP" in codes:
        return (f"The {dist} model at {int(sl * 100)}% service landed inside the continuity "
                f"band of the level in force -> kept it unchanged at Min {mn} / ROP {rp} / "
                f"Max {mx}.")
    rate = f"{mu:.3f}/day" if pd.notna(mu) else "n/a"
    tag = {"dying": "demand has ceased (older than a quarter)",
           "sporadic": "recent-only demand",
           "constant": "steady consumer"}.get(consumable, consumable)
    return (f"{tag}: rate {rate} x lead time {L:.0f}d, {dist} at {int(sl*100)}% "
            f"service -> Min {mn} / ROP {rp} / Max {mx}.")
