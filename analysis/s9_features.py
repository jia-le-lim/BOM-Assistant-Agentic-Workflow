"""S9 -- feature-influence ranking for the factory_recommended_new_* targets.

Answers: which columns actually drive the engineer's Max / ROP / Min decision,
ranked, so Phase-4 ML has a defensible feature set.

Method (no sklearn in this environment -- everything is numpy/pandas):
  * permutation-adjusted mutual information, so a 175-level column is judged
    against its own chance level rather than rewarded for cardinality;
  * 5-fold out-of-fold target encoding -> log loss / AUC, as an honest check
    that the MI ranking is not in-sample overfitting;
  * greedy forward selection with an L2 logistic model, for marginal (not
    univariate) value -- collinear columns drop out on their own.

Reproduce: python s9_features.py
"""

import numpy as np
import pandas as pd

from common import OUT, load_typed

pd.set_option("display.width", 240)
rng = np.random.default_rng(0)

df = load_typed("TCB")
N = len(df)

TMAX = "factory_recommended_new_max"
TROP = "factory_recommended_new_rop"
TMIN = "factory_recommended_new_min"
for c in (TMAX, TROP, TMIN):
    df[c] = df[c].fillna(0)

cur_max = df["max_qty"].fillna(0)
price = df["unitprice"].fillna(0)

y = (df[TMAX] > 0).astype(int).to_numpy()          # stage 1: stock at all?
df["mag"] = np.log1p(df[TMAX])                      # stage 2: how much?
atm = df["atm_recommended_max"].fillna(0)
df["ovr_dir"] = np.select([df[TMAX] > atm, df[TMAX] < atm], [2, 0], 1)
df["zero_trap"] = ((df["recom_max"].fillna(-1) == 0) & (df[TMAX] > 0)).astype(int)

# PRD 5.1 groupings, plus the undocumented columns placed by what they measure.
GROUPS = {
    "A. Algorithm candidate recommendation": [
        "atm_recommended_max", "atm_recommended_rop", "atm_recommended_min",
        "sfm_brr_max", "sfm_brr_rop", "sfm_brr_min",
        "recom_max", "recom_rop", "recom_min",
        "sfm_max", "sfm_rop", "sfm_min", "sfm_recommendation",
        "one_msia_max", "one_msia_rop", "one_msia_min", "rop_1dlt_sfm"],
    "B. Current stocking parameters": ["max_qty", "rop_qty", "min_qty"],
    "C. Consumption / demand history": [
        "last_547_day_cnsmptn_qty", "last_365_day_cnsmptn_qty",
        "last_180_day_cnsmptn_qty", "last_90_day_cnsmptn_qty",
        "last_30_day_cnsmptn_qty", "last_5_day_cnsmptn_qty",
        "days_since_last_issue", "frequencymonthswithusage", "partfreq",
        "previous_part_freq", "aging_status", "aging_new_flag", "sfm_criticality"],
    "D. Lead time / supply risk": [
        "contractual_lead_time", "sfm_mean_lt_cd", "alert_1dlt",
        "replenishment_policy", "order_qty_multiple", "repair_type",
        "new_clt_change_type"],
    "E. Cost / value": ["unitprice", "spending_impact", "gl_group",
                        "item_gl_account", "max_delta", "rop_delta"],
    "F. Inventory position": [
        "avail_qty", "vf_avail_qty", "open_po_qty", "qry_eoh_excess_qty",
        "consignment_qty", "avail_doi_maxd", "avail_doi_maxd_group",
        "uzb_doi_group2", "excess_status_tf", "onhand_pallet_qty",
        "qry_msbidataopen_simi_shp_qty"],
    "G. Sharing / cross-stockroom": [
        "shareable_indicator", "shared_parts", "other_stkrm_cons",
        "other_stkrm_30d_cons_qty", "other_stkrm_90d_cons_qty",
        "other_stkrm_180d_cons_qty", "other_stkrm_365d_cons_qty",
        "alternative_part"],
    "H. Machine / part identity": [
        "machine_type", "new_modulle", "functional_group", "category_type",
        "supplier_name", "item_desc"],
    "I. Org / ownership": [
        "purchasing_group_name", "inventory_owner", "area_owner", "ownership",
        "stockroom_id", "stockroom_name", "bunker_type"],
    "J. Age of master data": [
        "qry_msbidatadays_since_effective", "qry_msbidatadays_since_activation",
        "latest_atk_valid_to"],
    "Z. LEAKAGE (engineer's own decision record)": [
        "justification", "comments", "max_adoption", "rop_adoption",
        "modified_user", "review_acknowledge"],
}
COL2GRP = {c: g for g, cs in GROUPS.items() for c in cs}
FEATS = [c for cs in GROUPS.values() for c in cs if c in df.columns]
NBINS = 8


def discretise(s: pd.Series, nbin: int = NBINS) -> np.ndarray:
    """-> integer codes. Missing is its own level; exact zero kept separate from
    the positive bins, because 'zero' is a decision here, not a small number."""
    if pd.api.types.is_numeric_dtype(s):
        v = s.astype("float64")
        out = np.full(len(v), -1, dtype=np.int64)
        nz = v.notna().to_numpy()
        z = nz & (v.fillna(1).to_numpy() == 0)
        out[z] = 0
        pos = nz & ~z
        if pos.sum():
            vals = v.to_numpy()[pos]
            codes = (pd.factorize(vals, sort=True)[0] if len(np.unique(vals)) <= nbin
                     else np.asarray(pd.qcut(vals, nbin, labels=False, duplicates="drop")))
            out[pos] = codes + 1
        return out
    t = s.astype("object").where(s.notna(), "")
    t = t.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    return pd.factorize(t.to_numpy(), sort=True)[0]


def entropy(v: np.ndarray) -> float:
    _, cnt = np.unique(v, return_counts=True)
    p = cnt / cnt.sum()
    return float(-(p * np.log2(p)).sum())


def mi(x: np.ndarray, t: np.ndarray) -> float:
    xc, xi = np.unique(x, return_inverse=True)
    tc, ti = np.unique(t, return_inverse=True)
    if len(xc) < 2 or len(tc) < 2:
        return 0.0
    j = np.zeros((len(xc), len(tc)))
    np.add.at(j, (xi, ti), 1.0)
    j /= len(x)
    px, pt = j.sum(1, keepdims=True), j.sum(0, keepdims=True)
    m = j > 0
    return float((j[m] * np.log2(j[m] / (px @ pt)[m])).sum())


def adj_mi(x: np.ndarray, t: np.ndarray, nperm: int = 25) -> tuple[float, float]:
    """MI minus the MI this column would score against a shuffled target.
    Without this, cardinality alone puts category_type (428 levels) near the top."""
    raw = mi(x, t)
    null = float(np.mean([mi(x, rng.permutation(t)) for _ in range(nperm)]))
    return raw - null, null


def rank_against(t: np.ndarray, mask, label: str, feats=FEATS) -> pd.DataFrame:
    tt = t if mask is None else t[mask]
    H = entropy(tt)
    rows = []
    for c in feats:
        x = discretise(df[c])
        xx = x if mask is None else x[mask]
        if len(np.unique(xx)) < 2:
            continue
        a, null = adj_mi(xx, tt)
        rows.append({"target": label, "feature": c, "group": COL2GRP[c],
                     "adj_mi_bits": round(a, 4),
                     "pct_entropy": round(100 * a / H, 1) if H else 0.0,
                     "null_bias": round(null, 4),
                     "levels": int(len(np.unique(xx))),
                     "fill_pct": round(100 * df[c].notna().mean(), 1)})
    out = pd.DataFrame(rows).sort_values("adj_mi_bits", ascending=False)
    print(f"\n{'=' * 100}\n### {label}  (n={len(tt)}, target entropy={H:.3f} bits)\n{'=' * 100}")
    print(out.head(25).to_string(index=False))
    g = (out.groupby("group")
            .agg(best_feature=("feature", "first"), best_pct=("pct_entropy", "max"),
                 top3_bits=("adj_mi_bits", lambda s: round(s.head(3).sum(), 3)))
            .sort_values("best_pct", ascending=False))
    print(f"\n--- group roll-up: {label} ---")
    print(g.to_string())
    return out


# ------------------------------------------------------------ 0. target shape
print(f"TCB rows={N}\n")
print("=== TRAINABILITY OF EACH TARGET ===")
for c in (TMAX, TROP, TMIN):
    pos = df[c] > 0
    print(f"  {c:32s} positives={int(pos.sum()):4d} ({100*pos.mean():4.1f}%)  "
          f"distinct nonzero={df.loc[pos, c].nunique()}")
print(f"\n  rows where max/rop/min all unchanged: "
      f"{int(((df[TMAX] == cur_max) & (df[TROP] == df.rop_qty.fillna(0)) & (df[TMIN] == df.min_qty.fillna(0))).sum())}")

print("\n=== IS ROP/MIN A DETERMINISTIC FUNCTION OF MAX? ===")
stk = df[df[TMAX] > 0]
print(f"  among {len(stk)} stocked rows: new_rop==0 on {int((stk[TROP] == 0).sum())} "
      f"({100*(stk[TROP] == 0).mean():.1f}%), new_min==0 on {int((stk[TMIN] == 0).sum())} "
      f"({100*(stk[TMIN] == 0).mean():.1f}%)")
r = stk[stk[TROP] > 0]
ratio = (r[TROP] / r[TMAX]).round(2)
print(f"  rop/max ratio where rop>0 (n={len(r)}): p50={ratio.median():.2f}")
print(ratio.value_counts().head(5).to_string())
bm = pd.cut(df[TMAX], [-.01, 0, 1, 2, 5, 1e9], labels=["0", "1", "2", "3-5", "6+"])
br = pd.cut(df[TROP], [-.01, 0, 1, 2, 5, 1e9], labels=["0", "1", "2", "3-5", "6+"])
print("\n  new_max bucket x new_rop bucket:")
print(pd.crosstab(bm, br, margins=True).to_string())

# --------------------------------------------------- 1. MI ranking per target
parts = [
    rank_against(discretise(df[TMAX]), None, "new_MAX (binned)"),
    rank_against(discretise(df[TROP]), None, "new_ROP (binned)"),
    rank_against(discretise(df[TMIN]), None, "new_MIN (binned)"),
    rank_against(y, None, "STAGE 1: stock at all? (new_max > 0)"),
    rank_against(discretise(df["mag"]), (df[TMAX] > 0).to_numpy(),
                 "STAGE 2: how much? (log1p new_max, stocked rows)"),
]
nonalgo = [c for c in FEATS if COL2GRP[c] != "A. Algorithm candidate recommendation"]
parts.append(rank_against(df["ovr_dir"].to_numpy(), None,
                          "OVERRIDE direction vs atm_recommended_max (algo cols excluded)",
                          feats=nonalgo))
parts.append(rank_against(df["zero_trap"].to_numpy(), None,
                          "ZERO-TRAP firing (algo cols excluded)", feats=nonalgo))
pd.concat(parts).to_csv(OUT / "s9_feature_ranking.csv", index=False)

# ------------------------------------------- 2. out-of-fold univariate check
print(f"\n{'=' * 100}\n### OUT-OF-FOLD UNIVARIATE POWER (5-fold, stock/no-stock)\n{'=' * 100}")
base = y.mean()
base_ll = float(-(y * np.log(base) + (1 - y) * np.log(1 - base)).mean())


def auc_of(score: np.ndarray, t: np.ndarray) -> float:
    r = pd.Series(score).rank().to_numpy()
    n1, n0 = t.sum(), (1 - t).sum()
    return float((r[t == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else np.nan


def oof_encode(col: str, k: int = 5, prior: float = 20.0) -> tuple[float, float]:
    codes = discretise(df[col], nbin=8)
    fold = rng.permutation(N) % k
    pred = np.full(N, base)
    for f in range(k):
        tr, te = fold != f, fold == f
        g = pd.DataFrame({"c": codes[tr], "y": y[tr]}).groupby("c")["y"].agg(["sum", "count"])
        gm = y[tr].mean()
        enc = (g["sum"] + prior * gm) / (g["count"] + prior)
        pred[te] = pd.Series(codes[te]).map(enc).fillna(gm).to_numpy()
    pred = np.clip(pred, 1e-6, 1 - 1e-6)
    ll = float(-(y * np.log(pred) + (1 - y) * np.log(1 - pred)).mean())
    return ll, auc_of(pred, y)


print(f"baseline log loss (always predict {base:.3f}) = {base_ll:.4f}\n")
rows = []
for c in FEATS:
    ll, auc = oof_encode(c)
    rows.append({"feature": c, "group": COL2GRP[c],
                 "levels": int(len(np.unique(discretise(df[c])))),
                 "oof_logloss": round(ll, 4),
                 "loss_reduction_pct": round(100 * (base_ll - ll) / base_ll, 1),
                 "oof_auc": round(auc, 3)})
oof = pd.DataFrame(rows).sort_values("oof_auc", ascending=False)
print(oof.head(30).to_string(index=False))
oof.to_csv(OUT / "s9_oof_univariate.csv", index=False)

# ------------------------------------------------- 3. leakage / provenance
print(f"\n{'=' * 100}\n### max_delta PROVENANCE -- pre-decision input or post-decision leakage?\n{'=' * 100}")
md = df["max_delta"].fillna(0)
nzd = md != 0
for name, v in {
    "(approved_new - current) * price": (df[TMAX] - cur_max) * price,
    "(recom_max     - current) * price": (df["recom_max"].fillna(0) - cur_max) * price,
    "(atm_max       - current) * price": (atm - cur_max) * price,
    "(sfm_brr_max   - current) * price": (df["sfm_brr_max"].fillna(0) - cur_max) * price,
}.items():
    print(f"  {name:36s} all={100*(np.abs(md - v) < .01).mean():5.1f}%  "
          f"among {int(nzd.sum())} nonzero max_delta={100*(np.abs(md[nzd] - v[nzd]) < .01).mean():5.1f}%")
print(f"  spending_impact nonzero rows: {int((df['spending_impact'].fillna(0) != 0).sum())} "
      f"-- effectively a dead column")

print(f"\n{'=' * 100}\n### ALGORITHM AGREEMENT -- is 82.6%% real, or just matching zeros?\n{'=' * 100}")
rows = []
for cand, tgt in [("atm_recommended_max", TMAX), ("sfm_brr_max", TMAX), ("recom_max", TMAX),
                  ("max_qty", TMAX), ("atm_recommended_rop", TROP), ("sfm_brr_rop", TROP),
                  ("recom_rop", TROP), ("rop_qty", TROP), ("atm_recommended_min", TMIN),
                  ("sfm_brr_min", TMIN), ("recom_min", TMIN), ("min_qty", TMIN)]:
    v, t = df[cand].fillna(0), df[tgt]
    nontriv = (v > 0) | (t > 0)
    rows.append({"candidate": cand, "target": tgt.replace("factory_recommended_new_", ""),
                 "match_all_rows_pct": round(100 * (v == t).mean(), 1),
                 "both_zero": int(((v == 0) & (t == 0)).sum()),
                 "nontrivial_rows": int(nontriv.sum()),
                 "match_nontrivial_pct": round(100 * (v[nontriv] == t[nontriv]).mean(), 1),
                 "algo_0_eng_gt0": int(((v == 0) & (t > 0)).sum()),
                 "algo_gt0_eng_0": int(((v > 0) & (t == 0)).sum())})
print(pd.DataFrame(rows).to_string(index=False))

# ------------------------------------------------ 4. missingness structure
print(f"\n{'=' * 100}\n### MISSINGNESS CLUSTERS -- are the sparse columns one latent flag?\n{'=' * 100}")


def blank_mask(c: str) -> np.ndarray:
    s = df[c]
    if pd.api.types.is_numeric_dtype(s):
        return s.isna().to_numpy()
    return (s.isna() | (s.astype(str).str.strip() == "")).to_numpy()


SPARSE = ["area_owner", "inventory_owner", "new_modulle", "functional_group",
          "sfm_criticality", "days_since_last_issue", "frequencymonthswithusage",
          "partfreq", "new_clt_change_type"]
pats = {c: blank_mask(c) for c in SPARSE}
J = pd.DataFrame(index=SPARSE, columns=SPARSE, dtype=float)
for a in SPARSE:
    for b in SPARSE:
        u = (pats[a] | pats[b]).sum()
        J.loc[a, b] = round((pats[a] & pats[b]).sum() / u, 2) if u else np.nan
print("Jaccard overlap of BLANK patterns (1.00 = identical missingness):")
print(J.to_string())

print("\nstock rate by presence:")
for c in SPARSE:
    m = pats[c]
    if m.sum() in (0, N):
        continue
    print(f"  {c:28s} blank={int(m.sum()):5d} ({100*m.mean():4.1f}%)  "
          f"P(stock|blank)={y[m].mean():.3f}  P(stock|present)={y[~m].mean():.3f}")

# Cluster B runs the *opposite* way -- blank owner predicts stocking. Characterise it,
# so it is read as a population marker rather than a data-quality defect.
print("\nwhat IS the blank-owner population (cluster B)?")
print(pd.DataFrame({
    "g": np.where(pats["area_owner"], "area_owner BLANK", "area_owner filled"),
    "y": y,
    "policy": df["replenishment_policy"].to_numpy(),
    "aging": df["aging_status"].to_numpy(),
    "c547": df["last_547_day_cnsmptn_qty"].fillna(0).to_numpy(),
}).groupby("g").agg(rows=("y", "size"), stock_rate=("y", "mean"),
                    pct_order_to_max=("policy", lambda s: (s == "Order To Max").mean()),
                    pct_dead=("aging", lambda s: (s == "Dead").mean()),
                    pct_any_usage=("c547", lambda s: (s > 0).mean())).round(3).to_string())

print(f"\n{'=' * 100}\n### COLLINEARITY OF THE IDENTITY / ORG COLUMNS (Cramer's V)\n{'=' * 100}")


def cramers_v(a: pd.Series, b: pd.Series) -> float:
    ct = pd.crosstab(a.astype(str), b.astype(str)).to_numpy()
    n = ct.sum()
    exp = np.outer(ct.sum(1), ct.sum(0)) / n
    chi2 = ((ct - exp) ** 2 / np.where(exp == 0, 1, exp)).sum()
    r_, k_ = ct.shape
    return float(np.sqrt((chi2 / n) / max(min(r_ - 1, k_ - 1), 1)))


IDS = ["machine_type", "supplier_name", "category_type", "new_modulle", "area_owner",
       "purchasing_group_name", "inventory_owner", "functional_group",
       "item_gl_account", "replenishment_policy"]
V = pd.DataFrame(index=IDS, columns=IDS, dtype=float)
for i in IDS:
    for j in IDS:
        V.loc[i, j] = round(cramers_v(df[i], df[j]), 2)
print(V.to_string())

# --------------------------------------------- 5. data artefacts worth naming
print(f"\n{'=' * 100}\n### DATA ARTEFACTS THAT WILL MISLEAD A MODEL\n{'=' * 100}")
clt = df["contractual_lead_time"]
vc = clt.value_counts().head(8)
print("contractual_lead_time -- top values and their stock rate (25 is a system default):")
print(pd.DataFrame({"rows": vc,
                    "stock_rate": [round(y[(clt == v).to_numpy()].mean(), 3) for v in vc.index]}).to_string())

uz = df["uzb_doi_group2"].astype(str)
df["_paren"] = uz.str.startswith("(")
print("\nuzb_doi_group2 -- the parenthesised prefix, not the band, carries the signal:")
print(pd.DataFrame({"paren_prefix": df["_paren"], "y": y}).groupby("paren_prefix")
        .agg(rows=("y", "size"), stock_rate=("y", "mean")).round(3).to_string())
print("\n  and it is near-identical to replenishment_policy:")
print(pd.crosstab(df["_paren"], df["replenishment_policy"]).to_string())

print(f"\n{'=' * 100}\n### SEPARATION TABLES (stock rate by level)\n{'=' * 100}")
for c in ["replenishment_policy", "aging_status", "sfm_recommendation", "uzb_doi_group2",
          "excess_status_tf", "gl_group", "ownership"]:
    t = (df.assign(y=y).groupby(c, dropna=False)["y"]
           .agg(rows="size", stock_rate="mean").sort_values("rows", ascending=False))
    t["stock_rate"] = (100 * t["stock_rate"]).round(1)
    print(f"\n-- {c} (overall {100*base:.1f}%)")
    print(t.head(6).to_string())
for c in ["vf_avail_qty", "unitprice", "last_547_day_cnsmptn_qty",
          "qry_msbidatadays_since_effective"]:
    v = df[c].astype("float64")
    b = pd.qcut(v, 6, duplicates="drop") if v.nunique() > 6 else v
    t = df.assign(y=y, b=b).groupby("b", observed=True)["y"].agg(rows="size", stock_rate="mean")
    t["stock_rate"] = (100 * t["stock_rate"]).round(1)
    print(f"\n-- {c} (binned)")
    print(t.to_string())

# --------------------------------------------------- 6. forward selection
print(f"\n{'=' * 100}\n### MULTIVARIATE FORWARD SELECTION (L2 logistic, 5-fold OOF)\n{'=' * 100}")


def onehot(col: str, nbin: int = 6, min_level: int = 25) -> np.ndarray:
    codes = discretise(df[col], nbin=nbin)
    if not pd.api.types.is_numeric_dtype(df[col]):
        vc2 = pd.Series(codes).value_counts()
        rare = list(vc2[vc2 < min_level].index)
        codes = np.where(np.isin(codes, rare), -99, codes)
    u = np.unique(codes)
    M = np.zeros((N, len(u)))
    M[np.arange(N), np.searchsorted(u, codes)] = 1.0
    return M[:, 1:] if M.shape[1] > 1 else M          # drop one level as reference


def fit_logit(X: np.ndarray, t: np.ndarray, l2: float = 1.0, iters: int = 250) -> np.ndarray:
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - t) / len(t)
        g[1:] += l2 * w[1:] / len(t)
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb / len(t)
        H[np.diag_indices_from(H)] += l2 / len(t) + 1e-6
        try:
            w -= np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            w -= 0.5 * g
    return w


def oof_ll(cols: list[str], k: int = 5, seed: int = 3) -> float:
    if not cols:
        return base_ll
    X = np.hstack([onehot(c) for c in cols])
    fold = np.random.default_rng(seed).permutation(N) % k
    pred = np.zeros(N)
    for f in range(k):
        tr, te = fold != f, fold == f
        w = fit_logit(X[tr], y[tr])
        pred[te] = 1 / (1 + np.exp(-np.clip(
            np.hstack([np.ones((te.sum(), 1)), X[te]]) @ w, -30, 30)))
    pred = np.clip(pred, 1e-6, 1 - 1e-6)
    return float(-(y * np.log(pred) + (1 - y) * np.log(1 - pred)).mean())


# Org/ownership deliberately held out of POOL: it encodes who owns the part, and
# with one reviewer on 97.5% of rows it is reviewer identity, not part physics.
POOL = ["replenishment_policy", "machine_type", "supplier_name", "uzb_doi_group2",
        "aging_status", "sfm_recommendation", "contractual_lead_time", "unitprice",
        "vf_avail_qty", "avail_qty", "last_547_day_cnsmptn_qty", "last_365_day_cnsmptn_qty",
        "days_since_last_issue", "frequencymonthswithusage", "partfreq", "sfm_criticality",
        "atm_recommended_max", "sfm_brr_max", "recom_max", "max_qty", "rop_qty",
        "new_modulle", "functional_group", "category_type", "excess_status_tf",
        "gl_group", "ownership", "qry_eoh_excess_qty", "open_po_qty",
        "qry_msbidatadays_since_effective", "shareable_indicator", "consignment_qty",
        "item_gl_account", "repair_type", "alert_1dlt"]


def forward(pool: list[str], steps: int, label: str) -> list[str]:
    sel, cur_ll = [], base_ll
    print(f"\n{label}\n  start (intercept only) OOF log loss = {base_ll:.4f}")
    for i in range(steps):
        best, best_ll = None, cur_ll
        for c in pool:
            if c in sel:
                continue
            ll = oof_ll(sel + [c])
            if ll < best_ll - 1e-4:
                best, best_ll = c, ll
        if best is None:
            print("  no further improvement")
            break
        sel.append(best)
        print(f"  step {i+1}: +{best:34s} {cur_ll:.4f} -> {best_ll:.4f}  "
              f"(cumulative {100*(base_ll - best_ll)/base_ll:.1f}% reduction)")
        cur_ll = best_ll
    print(f"  selected: {sel}")
    return sel


forward(POOL, 8, "Org/ownership EXCLUDED (recommended feature set):")
forward(POOL + ["area_owner", "inventory_owner", "purchasing_group_name"], 5,
        "Org/ownership ALLOWED (contrast -- they are never picked):")

# ------------------------------------------- 7. magnitude drivers (stage 2)
print(f"\n{'=' * 100}\n### MAGNITUDE DRIVERS among stocked rows (Spearman vs log1p new_max)\n{'=' * 100}")
tgt = np.log1p(stk[TMAX])
rows = []
for c in ["frequencymonthswithusage", "last_180_day_cnsmptn_qty", "recom_max", "rop_qty",
          "sfm_brr_max", "order_qty_multiple", "last_365_day_cnsmptn_qty",
          "last_90_day_cnsmptn_qty", "vf_avail_qty", "last_547_day_cnsmptn_qty",
          "days_since_last_issue", "unitprice", "atm_recommended_max",
          "last_30_day_cnsmptn_qty", "avail_qty", "qry_eoh_excess_qty", "open_po_qty",
          "sfm_mean_lt_cd", "consignment_qty", "contractual_lead_time", "max_qty"]:
    v = stk[c].astype("float64")
    m = v.notna()
    if m.sum() < 30:
        continue
    rows.append({"feature": c, "group": COL2GRP[c], "n": int(m.sum()),
                 "spearman_rho": round(float(np.corrcoef(v[m].rank(), tgt[m].rank())[0, 1]), 3)})
mg = pd.DataFrame(rows)
print(mg.reindex(mg.spearman_rho.abs().sort_values(ascending=False).index).to_string(index=False))

b = pd.cut(stk["last_547_day_cnsmptn_qty"].fillna(0), [-1e9, 0, 1, 3, 10, 1e9],
           labels=["0", "1", "2-3", "4-10", ">10"])
print("\napproved max vs 547-day consumption, stocked rows only:")
print(stk.assign(b=b).groupby("b", observed=True)
        .agg(rows=(TMAX, "size"), median_new_max=(TMAX, "median"),
             p90_new_max=(TMAX, lambda s: s.quantile(.9))).to_string())

print(f"\nwrote s9_feature_ranking.csv, s9_oof_univariate.csv to {OUT}")
