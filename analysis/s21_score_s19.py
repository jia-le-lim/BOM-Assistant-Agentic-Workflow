"""S21 -- attach the confidence score to the S19 decision-vs-engine export.

Adds one column, `acceptance_probability`: the calibrated probability that the
engineer accepts the engine's Max/ROP on that row, from the model trained in
s21_confidence_model.ipynb. DISPLAY ONLY -- it does not gate, sort or approve
anything, and there is no threshold on this data at which auto-passing is safe.

Read it against the cycle's own base rate, not in absolute terms: acceptance
runs 15.7% (2025-03) to 44.0% (2026-01), so a 30% means "above average" in one
cycle and "below" in another. The per-cycle averages are printed on every run.

BLANK on dormant rows, which is most of the file. The model is trained on live
rows only (route active/dying, 711 of 9,054); the engine hard-rules dormant to
0/0, and a probability there would be extrapolation dressed as a measurement.

The join key is (source_month, part_code, max_qty, days_since_last_issue), not
part_code alone: one item is reviewed twice in 2024-07 under two stockrooms,
and the S19 export carries no stockroom column to separate them.

It also adds `current_engine_*` / `current_route` / `current_validation`,
because S19's own `engine_*` and `validation` columns are what the DB stored
when each batch was FIRST scored -- for most of this history that was the
pre-2026-08-28 service-level table (0.99/0.95/0.90), while the score predicts
today's engine (0.90/0.85/0.80). They disagree on 297 of 711 live rows, which
flips the match verdict on 93. The stored columns are left untouched: they are
the audit record of what the reviewer actually saw. The `current_*` columns are
the like-for-like comparison the probability is actually about.

Run: python analysis/s21_confidence_features.py  (self-check the model code)
     python analysis/s21_score_s19.py            (offline; needs the artifact)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import s21_confidence_features as F  # noqa: E402  (also puts backend/ on the path)
from app import engine_statistical as E  # noqa: E402

OUTDIR = ROOT / "analysis" / "output"
ARTIFACT = OUTDIR / "s21_confidence_model.json"
SRC = OUTDIR / "s19_decision_vs_engine_tcb.csv"
DST = OUTDIR / "s19_decision_vs_engine_tcb_scored.csv"

SCORE_COL = "acceptance_probability"


def _norm(s: pd.Series) -> pd.Series:
    """Canonical numeric text, so '2' / '2.0' / 2 and 'nan' / None / '' agree.

    The two files are written by different code paths (csv writer vs pickle),
    and without this the same row keys differ on 6,005 of 9,054 rows.
    """
    n = pd.to_numeric(s, errors="coerce")
    return n.map(lambda v: "" if pd.isna(v) else f"{v:g}")


def _key(month, item, max_qty, dsli) -> pd.Series:
    return (month.astype(str) + "|" + item.astype(str) + "|"
            + _norm(max_qty) + "|" + _norm(dsli))


def load_model() -> dict:
    if not ARTIFACT.exists():
        raise FileNotFoundError(
            f"{ARTIFACT} is missing. Train it first:\n"
            f"    run analysis/s21_confidence_model.ipynb")
    m = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    # The artifact was fitted against a specific target. If the engine's
    # agreement rule or version has moved, the model is scoring a different
    # question than it was trained on -- fail loudly rather than silently.
    assert m["features"] == F.FEATURES, "artifact feature order differs from the module"
    assert m["agree_tol"] == E.AGREE_TOL, (
        f"artifact trained at tol {m['agree_tol']}, engine now {E.AGREE_TOL} -- retrain")
    assert m["engine_model_version"] == E.MODEL_VERSION, (
        f"artifact trained on {m['engine_model_version']}, engine now "
        f"{E.MODEL_VERSION} -- retrain")
    return m


def score(m: dict, X: pd.DataFrame) -> np.ndarray:
    """sigmoid(platt_a * (standardised x . coef + intercept) + platt_b)."""
    Xs = (X.values - np.array(m["feature_means"])) / np.array(m["feature_stds"])
    z = F.log_odds(np.r_[m["intercept"], m["coef"]], Xs)
    return F.predict(np.array([m["platt_b"], m["platt_a"]]), z.reshape(-1, 1))


def main() -> int:
    m = load_model()
    payloads = F.load_payloads()
    X, y, live, month = F.build(payloads)

    p = np.full(len(X), np.nan)
    p[live] = score(m, X[live])

    # Today's engine on the same payloads -- what the probability is about.
    cur = E.run(payloads)
    scored = pd.DataFrame({
        "k": _key(payloads["_month"], payloads["item_id"],
                  payloads["max_qty"], payloads["days_since_last_issue"]),
        SCORE_COL: p,
        "current_engine_max": cur.factory_recommended_new_max.values,
        "current_engine_rop": cur.factory_recommended_new_rop.values,
        "current_engine_min": cur.factory_recommended_new_min.values,
        "current_route": cur.route.values,
        "current_validation": np.where(y == 1, "match", "diverge"),
    })
    assert not scored.k.duplicated().any(), "payload key is not unique"

    s19 = pd.read_csv(SRC, dtype=str, encoding="utf-8-sig")
    s19_key = _key(s19["source_month"], s19["part_code"],
                   s19["max_qty"], s19["days_since_last_issue"])
    assert not s19_key.duplicated().any(), "S19 key is not unique"

    matched = s19_key.isin(set(scored.k))
    assert matched.all(), (
        f"{int((~matched).sum())} S19 rows have no payload row -- the two files are "
        f"from different pulls; re-run analysis/s20_diverge_features.py")

    idx = scored.set_index("k")
    for c in scored.columns.drop("k"):
        s19[c] = s19_key.map(idx[c])
    s19[SCORE_COL] = s19[SCORE_COL].round(4)
    # The stored engine_* columns stay as written: they are what the reviewer
    # saw. This flags where today's engine would say something else instead.
    s19["engine_changed"] = np.where(
        (pd.to_numeric(s19.engine_max, errors="coerce") == s19.current_engine_max)
        & (pd.to_numeric(s19.engine_rop, errors="coerce") == s19.current_engine_rop),
        "N", "Y")
    try:
        s19.to_csv(DST, index=False, encoding="utf-8-sig")
    except PermissionError:
        raise SystemExit(f"{DST.name} is open in Excel -- close it and re-run.")

    got = s19[SCORE_COL].notna()
    changed = s19.engine_changed.eq("Y")
    print(f"wrote {DST}")
    print(f"  {len(s19):,} rows, {int(got.sum()):,} scored "
          f"({got.mean() * 100:.1f}%), {int((~got).sum()):,} blank (dormant)")
    print(f"  engine_changed=Y on {int(changed.sum()):,} rows "
          f"({int((changed & got).sum())} of them scored) -- stored engine_* predates "
          f"the 2026-08-28 service-level recalibration")
    print(f"  stored 'validation' vs 'current_validation' differ on "
          f"{int((s19.validation != s19.current_validation).sum()):,} rows")
    print(f"  model: {m['engine_model_version']} tol {m['agree_tol']} "
          f"n_train {m['n_train']} AUC {m.get('walk_forward_auc', float('nan')):.3f}")
    print("\nper cycle -- read a row's score against its own cycle average:")
    g = pd.DataFrame({"m": month, "p": p, "y": y})[live].groupby("m").agg(
        n=("p", "size"), mean_score=("p", "mean"), actual_accepted=("y", "mean"))
    print(g.assign(mean_score=(g.mean_score * 100).round(1),
                   actual_accepted=(g.actual_accepted * 100).round(1)).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
