"""S24 -- should the active / dying / dormant label come from clustering?

Asked directly (owner, 2026-09-10). The routing in engine_statistical.run() is
three hand-written thresholds; this measures whether unsupervised clustering on
the demand shape would label the population better.

Answered against three bars, because "the clusters look sensible" is not one:

  1. Reach       -- how many rows can clustering even touch? `dormant` is not a
                    cluster, it is a definition: every consumption window reads
                    zero. That is 92% of the population, leaving 711 rows.
  2. Value       -- does a cluster-conditioned policy predict the engineer better
                    than one global policy? Fitted leave-one-month-out, so a
                    cluster that only fits its own month cannot score.
  3. Stability   -- does a part keep its label between reviews? A label that
                    flips month to month is worse than a crude one in a tool an
                    engineer is meant to trust.

k-means is scipy.cluster.vq (already a dependency for scipy.stats in the
engine); no new package, and the silhouette is computed on a subsample because
the full pairwise matrix is not worth 9k x 9k for a diagnostic.

Run: python analysis/s24_route_clustering.py   (offline, needs s20 payloads)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.vq import kmeans2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import engine_statistical as E  # noqa: E402
from scorecard import num as _n, pair as _pair  # noqa: E402

PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"
OUT = ROOT / "analysis" / "output" / "s24_route_clustering.csv"
RNG = np.random.default_rng(0)


def main() -> int:
    df = pd.read_pickle(PKL).reset_index(drop=True)
    base = E.run(df)
    route = base.route.values
    month, item = df["_month"].values, df["item_id"].astype(str).values
    fm = np.nan_to_num(_n(df, "_final_max").values)
    e0 = _pair(base)

    def col(c, fill=0.0):
        return np.nan_to_num(_n(df, c).values, nan=fill)

    W = {w: col(f"last_{w}_day_cnsmptn_qty") for w in (5, 30, 90, 180, 365, 547)}
    freq, dsli, cur = col("frequencymonthswithusage"), col("days_since_last_issue", 9999), col("max_qty")
    mu = np.nan_to_num(base.mu_day.values)
    cv2 = np.array([E._pseudo_cv2({w: W[w][i] for w in W}) for i in range(len(df))])
    nwin = sum((W[w] > 0).astype(float) for w in (30, 90, 180, 365, 547))
    mom = np.where(W[365] > 0, (W[90] / 90) / np.maximum(W[365] / 365, 1e-9), 0.0)
    FEAT = np.c_[np.log1p(W[90]), np.log1p(W[365]), np.log1p(W[547]), np.log1p(freq),
                 np.log1p(np.minimum(dsli, 1500)), np.log1p(mu * 365), cv2, nwin, np.clip(mom, 0, 3)]
    NAMES = ["log_c90", "log_c365", "log_c547", "log_freq", "log_dsli",
             "log_annual_rate", "cv2", "n_windows", "momentum"]
    live = sum((W[w] > 0).astype(int) for w in W) > 0

    TOL = E.AGREE_TOL

    def hit(v, y):
        return np.abs(np.asarray(v, float) - y) <= TOL * np.abs(y)

    print("=== 1. REACH ===")
    print(f"  rows {len(df)}; consumption in at least one window: {int(live.sum())} "
          f"({live.mean() * 100:.1f}%)")
    print("  'dormant' is a definition (all six windows zero), not a cluster -- clustering "
          f"can only ever relabel the other {int(live.sum())} rows.")

    def fit_assign(train_mask, k):
        """Fit on train rows only, assign every live row to the nearest centroid."""
        Xtr = FEAT[train_mask]
        m, s = Xtr.mean(0), Xtr.std(0)
        s[s == 0] = 1
        cen, _ = kmeans2((Xtr - m) / s, k, minit="++", seed=0, iter=60)
        Z = (FEAT - m) / s
        return np.where(live, ((Z[:, None, :] - cen[None, :, :]) ** 2).sum(-1).argmin(1), -1)

    def silhouette(X, lab, n=1200):
        idx = RNG.choice(len(X), min(n, len(X)), replace=False)
        Xs, ls = X[idx], lab[idx]
        if len(set(ls)) < 2:
            return float("nan")
        D = np.sqrt(((Xs[:, None, :] - Xs[None, :, :]) ** 2).sum(-1))
        out = []
        for i in range(len(Xs)):
            same = ls == ls[i]
            same[i] = False
            if not same.any():
                continue
            a = D[i, same].mean()
            b = min(D[i, ls == c].mean() for c in set(ls) if c != ls[i])
            out.append((b - a) / max(a, b))
        return float(np.mean(out)) if out else float("nan")

    Xl = FEAT[live]
    Xl = (Xl - Xl.mean(0)) / np.where(Xl.std(0) == 0, 1, Xl.std(0))
    print("\n=== 2. SHAPE: k sweep on the live rows ===")
    for k in range(2, 8):
        _, lab = kmeans2(Xl, k, minit="++", seed=0, iter=60)
        print(f"  k={k}  silhouette {silhouette(Xl, lab):5.3f}  sizes {np.bincount(lab, minlength=k).tolist()}")

    lab4 = fit_assign(live, 4)
    print("\n=== k=4 cluster profiles (medians) ===")
    prof = pd.DataFrame(FEAT[live], columns=NAMES)
    prof["k"] = lab4[live]
    print(prof.groupby("k").median().round(2).to_string())
    print(pd.crosstab(pd.Series(lab4[live]), pd.Series(route[live]),
                      rownames=["cluster"], colnames=["rule route"]).to_string())

    print("\n=== 3. VALUE: best policy per label, leave-one-month-out (Max only) ===")
    POL = {"current": cur, "one": np.ones(len(df)), "two": np.full(len(df), 2.0),
           "zero": np.zeros(len(df)), "stat": e0[:, 0],
           "max(stat,2)": np.maximum(e0[:, 0], 2), "max(cur,stat)": np.maximum(cur, e0[:, 0]),
           "max(cur,2)": np.maximum(cur, 2), "ceil(c365)": np.ceil(W[365])}
    PN = list(POL)
    PM = np.c_[[POL[k] for k in PN]].T

    def lomo(labeller, name):
        pooled = np.zeros(len(df), bool)
        for m in sorted(set(month)):
            tr, te = live & (month != m), live & (month == m)
            if tr.sum() < 50:
                continue
            lab = labeller(tr)
            table = {}
            for c in np.unique(lab[tr]):
                s = tr & (lab == c)
                if s.sum() >= 10:
                    table[c] = PN[int(np.argmax(hit(PM[s].T, fm[s]).sum(1)))]
            fb = PN[int(np.argmax(hit(PM[tr].T, fm[tr]).sum(1)))]
            idx = np.where(te)[0]
            pooled[idx] = hit([POL[table.get(lab[i], fb)][i] for i in idx], fm[idx])
        print(f"  {name:36s} {pooled[live].mean() * 100:5.1f}%")

    print(f"  {'engine quantile (shipped)':36s} {hit(e0[live, 0], fm[live]).mean() * 100:5.1f}%")
    print(f"  {'one global policy = change nothing':36s} {hit(cur[live], fm[live]).mean() * 100:5.1f}%")
    lomo(lambda tr: np.where(live, (route == "dying").astype(int), -1), "conditioned on the rule route")
    for k in (2, 3, 4, 5):
        lomo(lambda tr, k=k: fit_assign(tr, k), f"conditioned on k-means k={k}")
    print("  -> no labelling beats one global policy. Same verdict as s23's cell probe:")
    print("     711 live rows carry inertia and not a per-segment policy.")

    print("\n=== 4. STABILITY: does a part keep its label between reviews? ===")

    def stability(v):
        prev, same, tot = {}, 0, 0
        for i in np.argsort(month):
            if not live[i]:
                continue
            if item[i] in prev:
                tot += 1
                same += int(prev[item[i]] == v[i])
            prev[item[i]] = v[i]
        return same, tot

    rows = {}
    cands = {"rule route (today)": route,
             "k-means k=2": fit_assign(live, 2), "k-means k=3": fit_assign(live, 3),
             "k-means k=4": lab4, "k-means k=5": fit_assign(live, 5),
             "threshold relabel (quiet split)":
                 np.where(~live, "dormant", np.where(W[90] > 0, "active",
                          np.where((W[365] > 0) | (dsli <= 365), "intermittent", "quiet")))}
    for nm, v in cands.items():
        s, t = stability(v)
        rows[nm] = {"stable_pairs": s, "pairs": t, "stable%": round(s / t * 100, 1)}
    print(pd.DataFrame(rows).T.to_string())

    print("\n=== 5. WHAT THE CLUSTERS DO SHOW: 'dying' is two populations ===")
    rl = cands["threshold relabel (quiet split)"]
    out = []
    for c in ("active", "intermittent", "quiet"):
        s = live & (rl == c)
        out.append({"label": c, "n": int(s.sum()),
                    "engine_agree%": round(hit(e0[s, 0], fm[s]).mean() * 100, 1),
                    "change_nothing_agree%": round(hit(cur[s], fm[s]).mean() * 100, 1),
                    "median_c365": float(np.median(W[365][s])),
                    "median_days_since_issue": float(np.median(dsli[s])),
                    "engineer_held_level%": round(float((fm[s] == cur[s]).mean() * 100), 1)})
    t = pd.DataFrame(out)
    print(t.to_string(index=False))
    t.to_csv(OUT, index=False)
    print("  Today both 'intermittent' and 'quiet' are routed 'dying' and told 'demand has")
    print("  ceased'. The engine is right on 'quiet' and wrong on 'intermittent', and the")
    print("  two are 3x apart on agreement -- that is the split worth having, and it needs")
    print("  a threshold, not a clusterer.")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
