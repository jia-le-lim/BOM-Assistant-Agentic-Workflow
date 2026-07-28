"""BOM Review recommendation engine -- rule-based MVP.

Implements PRD sections 6.1 (validation), 6.2 (business rules), 6.4 (candidate
comparison) and 6.6 (review Y/N), plus Rules 9 and 10 derived from the Jan'26
analysis (Analysis_Phase0_TCB_Jan26.md section 6.6).

LEAKAGE DISCIPLINE -- the engine reads INPUT and CANDIDATE columns only.
`factory_recommended_new_*` and every PRD section 5.1 memory column
(`justification`, `comments`, `*_adoption`, `review_acknowledge`, ...) are held
out entirely; they are the backtest labels, never engine inputs.

Deterministic: same inputs + same rule_version produce the same output
(PRD section 10).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

CONFIG_PATH = Path(__file__).parent / "rule_config.json"
MODEL_VERSION = "rules-only"

# Columns the engine is forbidden to read.
FORBIDDEN = {
    "factory_recommended_new_max", "factory_recommended_new_rop",
    "factory_recommended_new_min", "justification", "comments",
    "review_acknowledge", "rop_adoption", "max_adoption", "ooq_adoption",
    "modified_user", "modified_date",
}


def load_config(path: Path | None = None) -> dict:
    return json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))


def _num(df: pd.DataFrame, col: str, default=np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _txt(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series("", index=df.index, dtype="object")
    return df[col].astype(str).str.strip().fillna("")


def run(df: pd.DataFrame, cfg: dict | None = None, sfm_source: str = "sfm_brr") -> pd.DataFrame:
    """Score a batch. Returns the PRD section 5.2 output schema, one row per input row.

    sfm_source: which candidate family is treated as "the SFM value" for
    SFM_DISAGREEMENT. Open question 2 -- default `sfm_brr` because the adoption
    labels read "Adopt SFM/BRR". Swap to `sfm` or `atm_recommended` to test.
    """
    cfg = cfg or load_config()
    used = [c for c in df.columns if c in FORBIDDEN]
    df = df.drop(columns=used)  # hard guarantee, not a convention

    n = len(df)
    idx = df.index

    # ---- inputs -----------------------------------------------------------
    price = _num(df, "unitprice")
    clt = _num(df, "contractual_lead_time")
    cur_max, cur_rop, cur_min = _num(df, "max_qty"), _num(df, "rop_qty"), _num(df, "min_qty")
    avail = _num(df, "avail_qty").fillna(0)
    open_po = _num(df, "open_po_qty").fillna(0)
    oqm = _num(df, "order_qty_multiple")
    aging = _txt(df, "aging_status")
    machine = _txt(df, "machine_type")

    cons = {d: _num(df, f"last_{d}_day_cnsmptn_qty").fillna(0)
            for d in (5, 30, 90, 180, 365, 547)}

    # Blank recency/frequency mean "never issued in window" (report section 4.2),
    # so impute rather than treat as missing -- but record that we did.
    recency_raw = _num(df, "days_since_last_issue")
    recency_imputed = recency_raw.isna()
    recency = recency_raw.fillna(9999)
    freq_raw = _num(df, "frequencymonthswithusage")
    freq_imputed = freq_raw.isna()
    freq = freq_raw.fillna(0)

    # Candidate sources (PRD 6.4).
    cand_max = _num(df, f"{sfm_source}_max")
    cand_rop = _num(df, f"{sfm_source}_rop")
    recom_max = _num(df, "recom_max")
    # Quantity anchor. Measured on Jan'26: atm_recommended_max reproduces the
    # approved value on 82.6% of rows (MAE 0.40) -- the strongest single
    # predictor available, so it anchors Layer 3 rather than a demand formula
    # that returns 0 for the 74% of rows with no usage history.
    anchor_max = _num(df, cfg.get("quantity_anchor", "atm_recommended") + "_max")
    anchor_rop = _num(df, cfg.get("quantity_anchor", "atm_recommended") + "_rop")

    # Criticality: engineer-owned config keyed on machine_type. Empty by default,
    # so R3 correctly does nothing until engineers populate it.
    crit_map = cfg.get("machine_criticality", {})
    criticality = pd.Series(cfg.get("default_criticality", "Medium"), index=idx, dtype="object")
    for pattern, level in crit_map.items():
        criticality = criticality.mask(machine.str.contains(pattern, case=False, regex=False), level)
    is_critical = criticality.eq("High")

    LOW, HIGH = cfg["low_cost_threshold"], cfg["high_cost_threshold"]
    LONG_LT, RISK_LT = cfg["long_lead_time_threshold"], cfg["zero_stock_risk_clt"]
    RECENT, MINM = cfg["recent_usage_days"], cfg["min_usage_months"]

    # ---- Layer 1: validation (PRD 6.1) ------------------------------------
    v_bad_level = (cur_max < cur_rop) | (cur_rop < cur_min)
    v_neg_cons = pd.concat([cons[d] < 0 for d in cons], axis=1).any(axis=1)
    v_ladder = pd.Series(False, index=idx)
    ordered = [5, 30, 90, 180, 365, 547]
    for a, b in zip(ordered, ordered[1:]):
        v_ladder |= cons[a] > cons[b]
    v_price = price.isna() | (price <= 0)
    v_clt = clt.isna() | (clt <= 0) | (clt > 365)
    v_neg_po = open_po < 0
    invalid = v_bad_level | v_neg_cons | v_ladder | v_price | v_clt | v_neg_po

    # ---- Layer 2 + new rules ----------------------------------------------
    any_usage = cons[547] > 0
    recent_usage = recency <= RECENT
    proposed_action_up = cand_max > cur_max  # candidate wants an increase

    R1 = (price <= LOW) & (freq >= MINM) & recent_usage
    R2 = (price >= HIGH) & proposed_action_up
    R3 = is_critical & (cand_max < cur_max)
    R4 = (clt >= LONG_LT) & any_usage
    R5 = (cons[365] == 0) & (recency > 365) & ~is_critical
    R6 = aging.eq("Dead") & ~any_usage & (clt < RISK_LT) & (avail == 0)
    R7 = _txt(df, "partfreq").eq("High")
    R8 = aging.eq("New Part") | (aging.eq("") & ~any_usage & cur_max.isna())

    # Rule 9 -- the recom=0 trap. INPUT-ONLY risk factors; the approved value is
    # never consulted. This is the prediction being tested.
    #
    # Predicate selected empirically on Jan'26 (see s6 backtest). Within the
    # recom=0 population the base rate of "engineer stocked it anyway" is 24.0%;
    # this predicate lifts precision to 82.4% at 61.5% recall, covering 77.1% of
    # the value at stake. The dominant signals are that some *other* source still
    # wants stock (atm/brr), or the part is stocked today -- i.e. recom is alone
    # in proposing zero.
    recom_is_zero = recom_max.fillna(-1) == 0
    rf_other_source = (anchor_max.fillna(0) > 0) | (cand_max.fillna(0) > 0)
    rf_stocked_today = cur_max.fillna(0) > 0
    rf_order_to_max = _txt(df, "replenishment_policy").eq("Order To Max")
    rf_usage = any_usage
    rf_alive = ~aging.eq("Dead")

    mode = cfg.get("rule9_mode", "balanced")
    r9_trigger = rf_other_source | rf_stocked_today | is_critical
    if mode in ("balanced", "wide", "widest"):
        r9_trigger |= rf_order_to_max | rf_usage | rf_alive
    if mode in ("wide", "widest"):
        r9_trigger |= clt >= RISK_LT
    if mode == "widest":
        r9_trigger |= avail > 0
    R9 = recom_is_zero & r9_trigger

    # Rule 10 -- dormant, unstocked, slow to recover. Lower precision by design:
    # a watch/carve-out flag so the auto-clear tail cannot silently absorb the
    # parts with the longest recovery time.
    R10 = (~any_usage) & (cur_max.fillna(0) == 0) & (clt >= RISK_LT) & ~R9

    disagree = (cand_max.notna() & cur_max.notna() & (cand_max != cur_max))
    big_change = (cand_max > (1 + cfg["max_change_pct_review"]) * cur_max) & (cur_max > 0)

    # ---- Layer 3: quantity proposal ---------------------------------------
    # Anchor on the best-measured candidate source, then adjust. A pure demand
    # formula is not usable here: 74% of rows have no usage history, so it
    # returns 0 for exactly the parts the trap targets.
    base = anchor_max.where(anchor_max.notna(), cand_max).fillna(cur_max).fillna(0)

    # Demand formula applies only where real usage exists; take the more
    # protective of the two.
    daily = cons[365] / 365.0
    z = pd.Series(criticality.map(cfg["service_level_z"]).fillna(1.28).astype(float), index=idx)
    variability = np.sqrt(np.maximum(cons[365], 0))
    lt_days = clt.fillna(clt.median()).clip(lower=1)
    rop_calc = pd.Series(np.ceil(daily * lt_days + z * variability * np.sqrt(lt_days / 30.0)), index=idx)
    max_calc = pd.Series(np.ceil(rop_calc + np.maximum(daily * 30, 1)), index=idx)

    has_usage = cons[365] > 0
    prop_max = base.where(~has_usage, np.maximum(base, max_calc.fillna(0)))

    # Protective floor: the actual intervention against the trap. Applies only
    # where the anchor itself is zero -- otherwise the anchor already stocks it.
    protect = R9 | R10 | (is_critical & (clt >= LONG_LT))
    floor = float(cfg.get("min_protective_stock", 1))
    prop_max = prop_max.mask(protect & (prop_max <= 0), floor)

    # Never propose below current stock for a critical item (PRD 6.2 R3 intent).
    prop_max = pd.Series(np.where(is_critical, np.maximum(prop_max, cur_max.fillna(0)), prop_max), index=idx)

    prop_rop = anchor_rop.where(anchor_rop.notna(), cand_rop).fillna(0)
    prop_rop = prop_rop.where(~has_usage, np.maximum(prop_rop, rop_calc.fillna(0)))
    prop_rop = prop_rop.mask(protect & (prop_rop <= 0) & (prop_max > 0), floor)

    # MOQ / pack rounding (PRD 6.3).
    m = oqm.fillna(0) > 0
    prop_max = pd.Series(np.where(m, np.ceil(prop_max / oqm.where(m, 1)) * oqm.where(m, 1), prop_max), index=idx)

    prop_rop = np.minimum(prop_rop, prop_max)
    # Min is 0 on 98.7% of approved rows -- anchor on the candidate, do not invent one.
    anchor_min = _num(df, cfg.get("quantity_anchor", "atm_recommended") + "_min")
    prop_min = anchor_min.where(anchor_min.notna(), _num(df, f"{sfm_source}_min")).fillna(0)
    prop_min = np.minimum(prop_min, prop_rop)
    prop_max, prop_rop, prop_min = (pd.Series(prop_max, index=idx).fillna(0),
                                    pd.Series(prop_rop, index=idx).fillna(0),
                                    pd.Series(prop_min, index=idx).fillna(0))

    # On validation failure, do not move anything.
    prop_max = prop_max.where(~invalid, cur_max.fillna(0))
    prop_rop = prop_rop.where(~invalid, cur_rop.fillna(0))
    prop_min = prop_min.where(~invalid, cur_min.fillna(0))

    action = pd.Series(np.select([prop_max > cur_max.fillna(0), prop_max < cur_max.fillna(0)],
                                 ["Increase", "Decrease"], "Maintain"), index=idx)

    # ---- Layer 6: review Y/N (PRD 6.6) ------------------------------------
    # R5 (NO_RECENT_USAGE) and R6 (ABANDONED_TOOL) are informational only: they
    # fire on 92% / 50% of the roster and would swamp the queue. They are emitted
    # as reason codes but do not, alone, force a review.
    exposure = prop_max * price.fillna(0)
    review = (invalid | R2 | R3 | R4 | R8 | R9 | R10 | big_change
              | (disagree & (exposure >= cfg["value_gate_usd"])))

    # Auto-clear guards: a row must not be silently cleared just because no rule
    # fired, if getting it wrong is expensive or slow to recover from.
    if cfg.get("autoclear_guard", True):
        review |= (price >= HIGH) | (clt >= RISK_LT) | (exposure >= cfg["value_gate_usd"])

    # ---- risk / confidence -------------------------------------------------
    risk = pd.Series("Low", index=idx, dtype="object")
    risk = risk.mask(R4 | R9 | R10 | (price >= HIGH) | disagree, "Medium")
    risk = risk.mask(invalid | R3 | (is_critical & R9) | (exposure >= cfg["value_gate_usd"] * 5), "High")

    conf = pd.Series(0.9, index=idx, dtype="float64")
    conf -= 0.25 * invalid.astype(float)
    conf -= 0.15 * (recency_imputed | freq_imputed).astype(float)
    conf -= 0.15 * R8.astype(float)
    conf -= 0.10 * disagree.astype(float)
    conf -= 0.10 * (~any_usage).astype(float)
    conf = conf.clip(0.05, 0.99).round(2)

    # ---- reason codes + explanation ---------------------------------------
    codes = {
        "DATA_QUALITY_REVIEW_REQUIRED": invalid & ~v_bad_level,
        "INVALID_CURRENT_STOCKING_LEVEL": v_bad_level,
        "LOW_COST_RECURRING_USAGE": R1,
        "HIGH_COST_INCREASE_REVIEW": R2,
        "CRITICAL_MACHINE_PROTECTION": R3,
        "LONG_LEAD_TIME_RISK": R4,
        "NO_RECENT_USAGE": R5,
        "ABANDONED_TOOL": R6,
        "HIGH_USAGE_TOOL": R7,
        "INSUFFICIENT_DATA": R8,
        "ZERO_RECOMMENDATION_OVERRIDE": R9,
        "AD_HOC_CONSUMPTION_RISK": R9 & ~any_usage & (clt >= LONG_LT),
        "LONG_LEAD_TIME_ZERO_STOCK": R10,
        "SFM_DISAGREEMENT": disagree,
        "MAX_CHANGE_EXCEEDS_THRESHOLD": big_change,
        "IMPUTED_USAGE_FEATURES": recency_imputed | freq_imputed,
    }
    code_df = pd.DataFrame({k: v.fillna(False) for k, v in codes.items()}, index=idx)
    reason_code = code_df.apply(lambda r: ",".join(code_df.columns[r.values]) or "NO_ACTION", axis=1)

    def explain(i):
        cs = list(code_df.columns[code_df.loc[i].values])
        if not cs:
            return "No rule triggered; current stocking parameters retained."
        bits = []
        if "ZERO_RECOMMENDATION_OVERRIDE" in cs:
            bits.append(f"Source algorithm recommends 0 but the part carries risk "
                        f"(lead time {clt.get(i, float('nan')):.0f}d, on-hand {avail.get(i, 0):.0f}); "
                        f"protective stock proposed instead of zero")
        if "LONG_LEAD_TIME_ZERO_STOCK" in cs:
            bits.append(f"dormant with a {clt.get(i, float('nan')):.0f}-day lead time — excluded from auto-clear")
        if "SFM_DISAGREEMENT" in cs:
            bits.append(f"candidate source proposes {cand_max.get(i)} vs current {cur_max.get(i)}")
        if "INVALID_CURRENT_STOCKING_LEVEL" in cs or "DATA_QUALITY_REVIEW_REQUIRED" in cs:
            bits.append("input data failed validation; values held unchanged pending correction")
        if not bits:
            bits.append("; ".join(c.replace("_", " ").lower() for c in cs[:3]))
        return (". ".join(bits) + ".").capitalize()

    out = pd.DataFrame({
        "item_id": df["item_id"] if "item_id" in df.columns else idx,
        "factory_recommended_new_max": prop_max.astype("int64"),
        "factory_recommended_new_rop": prop_rop.astype("int64"),
        "factory_recommended_new_min": prop_min.astype("int64"),
        "review_required": np.where(review.fillna(False), "Y", "N"),
        "factory_recommendation_action": action,
        "reason_code": reason_code,
        "risk_level": risk,
        "confidence_score": conf,
        "explanation": [explain(i) for i in idx],
        "model_version": MODEL_VERSION,
        "rule_version": cfg["rule_version"],
        "_exposure_usd": exposure.round(2),
    }, index=idx)
    return pd.concat([out, code_df.add_prefix("rc_")], axis=1)
