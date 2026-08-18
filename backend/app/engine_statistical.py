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

MODEL_VERSION = "stat-v1"

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
SL_BY_CRIT = {"h": 0.99, "m": 0.95, "l": 0.90, "d": 0.90}
SL_DEFAULT = 0.95


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


def run(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Size Min/ROP/Max for every row; returns the engine-standard schema."""
    cfg = cfg or {}
    rule_version = str(cfg.get("rule_version", "stat"))

    win = {w: _num(df, CONS[w]) for w in WINDOWS}
    lt = _num(df, "contractual_lead_time")
    moq_s = _num(df, "order_qty_multiple")
    price = _num(df, "unitprice")
    cur_max = _num(df, "max_qty")
    freq = _num(df, "frequencymonthswithusage")
    crit = df.get("sfm_criticality", pd.Series("", index=df.index)).astype(str)
    item = df.get("item_id", pd.Series("", index=df.index)).astype(str)

    out = []
    for i in range(len(df)):
        w = {ww: win[ww].iloc[i] for ww in WINDOWS}
        present = [w[ww] for ww in WINDOWS if pd.notna(w[ww])]
        c90, c365 = w[90], w[365]
        mu = _mu_day(w)
        cv2 = _pseudo_cv2(w)
        criticality = crit.iloc[i]
        L = lt.iloc[i] if pd.notna(lt.iloc[i]) and lt.iloc[i] > 0 else DEFAULT_LT
        moq = int(moq_s.iloc[i]) if pd.notna(moq_s.iloc[i]) and moq_s.iloc[i] >= 1 else 1
        sl = _sl(criticality)
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

        # --- sizing by route ---
        if route == "no-data":
            new_min = int(cur) if pd.notna(cur) else 0
            new_rop = new_min
            new_max = new_min
            review, dist = "Y", "none"
            conf, risk = 0.2, "Medium"
            reasons.append("NO_CONSUMPTION_DATA")

        elif route == "dormant":
            if _is_critical(criticality):
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
            if regular:
                use_poisson, phi = True, 1.0
                dist = "poisson"
            else:
                use_poisson = False
                phi = max(PHI_DYING if route == "dying" else PHI_ACTIVE, 1.0 + cv2)
                dist = "nbinom"
            new_rop = _quantile(mu_L, sl, phi, use_poisson)
            new_max = _quantile(mu_LT, sl, phi, use_poisson)
            new_min = max(math.ceil(new_rop - mu_L), 0)
            new_max = max(new_max, new_rop + moq)
            new_max = int(math.ceil(new_max / moq) * moq)

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

        new_min, new_rop, new_max = int(new_min), int(new_rop), int(new_max)
        new_rop = max(new_rop, new_min)
        new_max = max(new_max, new_rop)
        p = price.iloc[i]
        explanation = _explain(route, consumable, mu, L, sl, dist, new_min, new_rop, new_max)
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
            "_exposure_usd": float(p * new_max) if pd.notna(p) else 0.0,
            "model_version": MODEL_VERSION,
            "rule_version": rule_version,
            # extra context (ignored by adapter, handy for debugging/tests)
            "route": route,
            "consumable": consumable,
            "mu_day": round(mu, 5) if pd.notna(mu) else np.nan,
        })
    return pd.DataFrame(out)


def _explain(route, consumable, mu, L, sl, dist, mn, rp, mx) -> str:
    if route == "no-data":
        return "No consumption data in any window -> kept current levels, flagged for review."
    if route == "dormant":
        return ("Critical part with no consumption -> keep-alive insurance stock."
                if mx > 0 else
                "No consumption in any window -> proposed zero, flagged for review.")
    rate = f"{mu:.3f}/day" if pd.notna(mu) else "n/a"
    tag = {"dying": "demand has ceased (older than a quarter)",
           "sporadic": "recent-only demand",
           "constant": "steady consumer"}.get(consumable, consumable)
    return (f"{tag}: rate {rate} x lead time {L:.0f}d, {dist} at {int(sl*100)}% "
            f"service -> Min {mn} / ROP {rp} / Max {mx}.")
