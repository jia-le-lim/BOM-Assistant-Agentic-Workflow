"""S21 -- acceptance-confidence features, target, and the model, in one place.

Given a scored BOM cycle, estimate the probability the engineer will accept the
engine's Min/ROP/Max. The number is DISPLAY ONLY: it does not gate, sort or
auto-approve anything. Measured coverage at >=85% precision is 0%, so there is
no auto-pass to be had here -- the deliverable is a CALIBRATED probability, not
a decision rule.

Target is the review scorecard: strict 10% relative tolerance on Max and ROP,
no absolute floor, Min excluded -- identical to engine_statistical.close_enough,
whose tolerance is imported rather than restated (see _matched / _selfcheck).

The model is a 9-coefficient L2 logistic regression fitted with scipy L-BFGS-B
plus Platt scaling, so analysis/ still imports no ML library and the persisted
artifact is ~15 numbers of JSON the backend can score with one dot product.

Everything lives in this module rather than in the notebook because this
codebase has already been bitten by one rule living in three places: the
agreement rule existed in engine_statistical, analysis/common.py and s19
simultaneously and drifted. FEATURES and the target get exactly one definition.

Run: python analysis/s20_diverge_features.py     (pull payloads, needs DB)
     python analysis/s21_confidence_features.py  (offline self-check)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import engine_statistical as E  # noqa: E402

PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"

# Population: live rows only. Dormant is hard-ruled by the engine (0/0 vs 0/0 is
# a free match on any model) and including it inflates every metric.
LIVE_ROUTES = ("active", "dying")

# The feature list, in artifact order. Nothing here is an engine OUTPUT or a
# MEMORY column of THIS cycle -- see common.OUTPUT_COLS / common.MEMORY_COLS
# and test_forbidden_columns_absent.
FEATURES = [
    "gap_prior_decision",       # |engine Max - this item's PREVIOUS final Max| / prior
    "has_prior",                # 0 on an item's first cycle (29% of live rows)
    "delta_abs_vs_current",     # |engine Max - current max_qty|
    "delta_rel_vs_current",     # ...as a fraction of current
    "mu_daily_rate",            # engine's own demand-rate estimate
    "freq_months_usage",        # frequencymonthswithusage, -1 when absent
    "days_since_last_issue",    # -1 when absent
    "is_dying_route",           # engine route == "dying"
    "policy_order_to_max",      # replenishment_policy == "Order To Max"
]

COLD_START_GAP = -1.0


def _n(df: pd.DataFrame, c: str) -> np.ndarray:
    return pd.to_numeric(df.get(c), errors="coerce").values


def _pair(out: pd.DataFrame) -> np.ndarray:
    """Max and ROP only -- Min is a derived floor, not a replenishment lever."""
    return np.c_[out.factory_recommended_new_max,
                 out.factory_recommended_new_rop].astype(float)


def _matched(engine: np.ndarray, bench: np.ndarray) -> np.ndarray:
    """Vectorised engine_statistical.close_enough over Max and ROP.

    The tolerance comes from the engine so this harness cannot drift from it;
    _selfcheck asserts the two agree row by row.
    """
    return (np.abs(engine - bench) <= E.AGREE_TOL * np.abs(bench)).all(axis=1)


def _prior_gap(month: np.ndarray, key: np.ndarray, engine_max: np.ndarray,
               final_max: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Distance from the engine's Max to this item's PREVIOUS accepted Max.

    Two passes per month -- read every row's history first, fold the month in
    second. Reading and writing in one pass leaks a row's own outcome into its
    own feature and lifts walk-forward AUC toward 0.9.

    gap_prior_decision derives from the previous cycle's final_max, which is an
    OUTPUT_COLS field. That is permitted: it is prior-cycle history, known
    before the current review begins -- the same rung
    engine_statistical._benchmark() already grades against in production. It is
    NOT this cycle's output.

    Relative, not absolute, because the target it predicts is a relative 10%
    band. Measured monotone on live rows: gap 0 -> 58.3% accepted, >50% -> 23.3%.
    """
    gap = np.full(len(key), COLD_START_GAP)
    has = np.zeros(len(key))
    hist: dict[str, float] = {}
    for mo in sorted(set(month)):
        rows = np.flatnonzero(month == mo)
        for i in rows:                                  # pass 1: read only
            p = hist.get(key[i])
            if p is not None and not np.isnan(p):
                has[i] = 1.0
                gap[i] = abs(engine_max[i] - p) / max(abs(p), 1.0)
        for i in rows:                                  # pass 2: then write
            hist[key[i]] = final_max[i]
    return gap, has


def build(payloads: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """Features, target, live mask and month for one stack of scored payloads.

    Returns (X, y, live, month) over ALL rows -- callers subset with `live`
    rather than getting a pre-filtered frame, so month alignment stays trivial.
    """
    df = payloads.reset_index(drop=True)
    out = E.run(df)
    route = out.route.values
    engine = _pair(out)
    bench = np.c_[_n(df, "_final_max"), _n(df, "_final_rop")]
    y = _matched(engine, bench).astype(int)

    month = df["_month"].astype(str).values
    # Composite key: one (month, item_id) duplicate exists in the history and
    # the stockroom resolves it.
    key = (df["item_id"].astype(str) + "|" + df["stockroom_id"].astype(str)).values
    gap, has = _prior_gap(month, key, engine[:, 0], _n(df, "_final_max"))

    cur = np.nan_to_num(_n(df, "max_qty"))
    delta = np.abs(engine[:, 0] - cur)
    freq, dsli = _n(df, "frequencymonthswithusage"), _n(df, "days_since_last_issue")
    policy = df.get("replenishment_policy", pd.Series("", index=df.index))

    X = pd.DataFrame({
        "gap_prior_decision": gap,
        "has_prior": has,
        "delta_abs_vs_current": delta,
        "delta_rel_vs_current": delta / np.maximum(np.abs(cur), 1.0),
        # mu is NaN only on the no-data route, which is not live; 0 is the
        # honest reading there anyway (no window carried a rate).
        "mu_daily_rate": pd.to_numeric(out.mu_day, errors="coerce").fillna(0.0).values,
        # -1, never dropped: absence is itself signal, and dropping the row
        # would silently shrink the population.
        "freq_months_usage": np.where(np.isnan(freq), -1.0, freq),
        "days_since_last_issue": np.where(np.isnan(dsli), -1.0, dsli),
        "is_dying_route": (route == "dying").astype(float),
        "policy_order_to_max": policy.astype(str).str.strip().str.lower()
                                     .eq("order to max").astype(float).values,
    })[FEATURES]
    return X, y, np.isin(route, LIVE_ROUTES), month


def load_payloads() -> pd.DataFrame:
    """The S20 payload pickle, with the refresh instruction instead of a bare
    FileNotFoundError -- producing it needs DB access."""
    if not PKL.exists():
        raise FileNotFoundError(
            f"{PKL} is missing. It is produced by a DB pull:\n"
            f"    python analysis/s20_diverge_features.py")
    return pd.read_pickle(PKL).reset_index(drop=True)


# --- model ----------------------------------------------------------------
# No sklearn, no lightgbm. A 9-coefficient logistic regression is ~60 lines of
# numpy/scipy, scipy is already a backend dependency for the engine's NBD
# quantiles, and the artifact stays JSON.

def fit_logistic(A: np.ndarray, y: np.ndarray, lam: float = 1.0) -> np.ndarray:
    """L2 logistic regression via L-BFGS-B. Returns [intercept, *coefs].

    scipy.optimize rather than hand-rolled gradient descent: no learning rate
    to tune, and convergence is checked rather than assumed.
    """
    X = np.c_[np.ones(len(A)), A]
    y = np.asarray(y, dtype=float)

    def obj(w):
        z = X @ w
        # log(1+exp(z)) computed stably: np.log(1+np.exp(z)) overflows to inf
        # above z~700 and L-BFGS-B then fails with an unhelpful message.
        ll = np.sum(np.logaddexp(0, z) - y * z)
        # expit, not 1/(1+exp(-z)): the latter overflows on large |z|.
        grad = X.T @ (expit(z) - y)
        pen, gpen = 0.5 * lam * w[1:] @ w[1:], np.r_[0.0, lam * w[1:]]
        return (ll + pen) / len(X), (grad + gpen) / len(X)

    res = minimize(obj, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B")
    # Deliberate: silent non-convergence produces a plausible-looking wrong model.
    assert res.success, f"logistic fit did not converge: {res.message}"
    return res.x


def predict(w: np.ndarray, A: np.ndarray) -> np.ndarray:
    return expit(np.c_[np.ones(len(A)), A] @ w)


def log_odds(w: np.ndarray, A: np.ndarray) -> np.ndarray:
    """Raw score. Platt scaling is fitted on THIS, never on predict() -- a
    logistic on an already-squashed [0,1] input is numerically poor."""
    return np.c_[np.ones(len(A)), A] @ w


def standardise(A: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean/std of a TRAINING fold. Never call this on the full dataset inside
    a walk-forward -- that leaks the test fold's distribution into training."""
    mu, sd = A.mean(axis=0), A.std(axis=0)
    sd = np.where(sd == 0, 1.0, sd)     # a constant column carries no signal
    return mu, sd


def auc(y: np.ndarray, s: np.ndarray) -> float:
    """Rank-based ROC AUC -- no sklearn needed."""
    y = np.asarray(y)
    r = pd.Series(s).rank().values
    n1, n0 = y.sum(), (1 - y).sum()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def ece(y: np.ndarray, p: np.ndarray, bins: int = 5) -> float:
    """Expected calibration error over equal-width probability bins.

    5 bins, not 10: at n~700 ten bins leaves near-empty buckets whose observed
    rate is noise.
    """
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 5) -> pd.DataFrame:
    """Per-bin predicted vs observed -- the reliability diagram's own numbers."""
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()),
                     "predicted": float(p[m].mean()) if m.any() else np.nan,
                     "observed": float(y[m].mean()) if m.any() else np.nan})
    return pd.DataFrame(rows)


# --- self-check -----------------------------------------------------------

def synthetic_row(month: str, item: str, cons: float, final_max: float,
                  final_rop: float) -> dict:
    """One minimal payload row the engine routes as active. Shared with the
    tests so the fixture and the self-check cannot drift apart."""
    return {"_month": month, "item_id": item, "stockroom_id": "SR1",
            "last_90_day_cnsmptn_qty": cons / 4, "last_365_day_cnsmptn_qty": cons,
            "contractual_lead_time": 30, "max_qty": 5, "rop_qty": 3, "min_qty": 1,
            "sfm_criticality": "m", "replenishment_policy": "Order to Demand",
            "frequencymonthswithusage": 6, "days_since_last_issue": 10,
            "_final_max": final_max, "_final_rop": final_rop}


def _selfcheck() -> None:
    """Pin the tolerance, pin that it still equals the engine's own rule, and
    pin that the prior-cycle feature cannot see the row it describes."""
    b = np.array([[2.0, 2.0], [10.0, 10.0], [0.0, 0.0], [5.0, 5.0]])
    # exact / 10% boundary is inclusive / both zero / exact
    hit = np.array([[2., 2.], [11., 11.], [0., 0.], [5., 5.]])
    # 2 admits only 2 / 12 is 2 off a base of 10 / 0 admits only 0 / ROP alone breaks it
    miss = np.array([[1., 2.], [12., 10.], [1., 0.], [5., 7.]])
    assert list(_matched(hit, b)) == [True] * 4
    assert list(_matched(miss, b)) == [False] * 4
    for cand, want in ((hit, "match"), (miss, "diverge")):
        for e, x in zip(cand, b):
            assert E._agreement((e[0], e[1], 0), (x[0], x[1], 999)) == want, \
                "harness drifted from engine_statistical._agreement"

    # No OUTPUT/MEMORY column of the CURRENT cycle may be a feature name.
    sys.path.insert(0, str(ROOT / "analysis"))
    from common import MEMORY_COLS, OUTPUT_COLS
    assert not set(FEATURES) & (set(OUTPUT_COLS) | set(MEMORY_COLS))

    # Leakage: cycle 1 is cold-start, cycle 2 grades against cycle 1's decision.
    two = pd.DataFrame([synthetic_row("2024-01", "A", 40, 7, 7),
                        synthetic_row("2024-02", "A", 40, 99, 99)])
    X, _, _, _ = build(two)
    assert X.has_prior.tolist() == [0.0, 1.0]
    assert X.gap_prior_decision.iloc[0] == COLD_START_GAP
    eng_max = _pair(E.run(two))[:, 0]
    assert np.isclose(X.gap_prior_decision.iloc[1], abs(eng_max[1] - 7) / 7), \
        "row 2 must be graded against cycle 1's final_max, not its own"

    # Numerics: separable data recovers the right signs, extreme scale stays finite.
    rng = np.random.default_rng(0)
    A = rng.normal(size=(200, 2))
    ys = (A[:, 0] - A[:, 1] > 0).astype(int)
    w = fit_logistic(A, ys, lam=0.01)
    assert w[1] > 0 > w[2], w
    assert auc(ys, predict(w, A)) == 1.0
    assert np.isfinite(fit_logistic(A * 1e3, ys)).all()
    assert ece(ys, ys.astype(float)) < 1e-12


def main() -> int:
    _selfcheck()
    X, y, live, month = build(load_payloads())
    print(f"rows {len(X)}  live {int(live.sum())}  features {len(FEATURES)}")
    print(f"base acceptance rate (live)   {y[live].mean() * 100:5.1f}%")
    print(f"has_prior coverage (live)     {X.has_prior.values[live].mean() * 100:5.1f}%")
    print("\nper-month (live rows):")
    g = pd.DataFrame({"m": month, "y": y, "hp": X.has_prior})[live].groupby("m").agg(
        n=("y", "size"), base=("y", "mean"), has_prior=("hp", "mean"))
    print(g.assign(base=(g.base * 100).round(1),
                   has_prior=(g.has_prior * 100).round(1)).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
