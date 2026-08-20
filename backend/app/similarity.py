"""Advisory KNN peer-evidence layer (spec sections 1-5, 7).

Runs AFTER the statistical engine scores a batch and BEFORE the engineer
reviews it. For every scored row it retrieves the closest historical PEER parts
-- different item_ids with comparable attributes -- from review_history joined
to that peer's own frozen bom_rows snapshot, and persists:

  * the neighbours themselves, with a business-language reason per match;
  * an outlier score, so a part with no comparable precedent is flagged;
  * distance-weighted analogue Max/ROP/Min medians and an interquartile range;
  * historical override / high-risk rates over those neighbours.

The boundary is the whole design. This module writes similarity_result and
similarity_neighbour and NOTHING else. It never touches recommendation_result,
never lowers a risk_level, and no number it produces can reach a WINGS export.
The engine calculates, this supplies historical evidence, the engineer approves.

Leakage safety: features come from the peer's OWN batch payload, which
ingestion writes once and nothing ever updates. A peer is therefore compared on
what was known when it was decided, not on today's corrected values (spec 8).
The peer's decision and final_* are retrieved EVIDENCE, not features, so there
is no circularity.
"""

from __future__ import annotations

import bisect
import json
import math

import numpy as np

from .db import Conn, active_config
from .part_category import UNCATEGORISED, categorise, load_rules

MODEL_VERSION = "knn-v1"

# Weights sum to 1.0, so the composite Gower distance is also in [0, 1].
# Criticality and machine identity dominate: without that a cheap consumable
# looks "similar" to a critical insurance spare whenever their consumption
# numbers happen to match.
FEATURE_WEIGHTS = {
    "machine_family":       0.25,   # first token of machine_type
    "criticality":          0.20,   # sfm_criticality, confirmed config wins
    "route":                0.10,   # engine demand route
    "consumable":           0.05,   #   ... + demand class = 15% demand group
    "lead_time_band":       0.15,   # ordinal, 5 bands
    "replenishment_policy": 0.10,
    "price_band":           0.05,   # ordinal, 4 bands
    "supplier":             0.05,
    "sharing":              0.05,   # shared vs local-only
}

FEATURES = tuple(FEATURE_WEIGHTS)          # locks column order for the arrays
ORDINAL = {"lead_time_band": 5, "price_band": 4}    # feature -> band count

K_NEIGHBOURS = 7            # retrieved per part (spec: find_similar_parts k=7)
MAX_DISTANCE = 0.35         # beyond this a peer is not "sufficiently similar"
MIN_NEIGHBOURS = 5          # fewer close peers than this -> NO_RELIABLE_ANALOGUE
DIVERGENCE_FRAC = 0.5       # |engine - analogue| beyond this share -> flag
KEEP_ALIVE = 1              # critical parts never get a zero analogue
EPS = 1e-6                  # distance-weight guard, w = 1/(d + EPS)
MAX_REASONS = 4             # matched features named per neighbour

# Peers loaded by scripts/backfill_history.py. Their final_* values are genuine
# engineer decisions and count fully toward the analogue medians, but they were
# never a response to THIS engine -- so they carry no accept/override signal and
# are excluded from the override / high-risk rates. Counting them would dilute
# every rate toward zero and quietly suppress the triage promotion.
BACKFILL_MODEL_VERSION = "backfill-v1"

LEAD_TIME_EDGES = (14, 30, 60, 120)         # -> bands 0..4, days
PRICE_EDGES = (100.0, 1000.0, 10000.0)      # -> bands 0..3, USD

NO_RELIABLE_ANALOGUE = "NO_RELIABLE_ANALOGUE"
ANALOGUE_DIVERGENCE = "ANALOGUE_DIVERGENCE"
# The weak rung of the benchmark ladder (engine_statistical._benchmark): the
# engine's Max sits inside the peers' own interquartile spread. Their spread IS
# the tolerance, so no arbitrary +/-% band has to be invented. Advisory only --
# analogue concurrence must never auto-clear (s15 put this class at ~71%
# precision); it buys a cheaper LLM route, nothing more.
ANALOGUE_CONCUR = "ANALOGUE_CONCUR"

# Business language, never the sensitive VALUE. redact.py masks by key name, so
# a supplier or machine_type embedded in this free text would sail straight
# past LLM_REDACT_PROMPTS (see redact.SENSITIVE_COLS).
_REASON_LABEL = {
    "machine_family": "same machine family",
    "criticality": "same criticality",
    "route": "same demand route",
    "consumable": "same demand class",
    "lead_time_band": "same lead-time band",
    "replenishment_policy": "same replenishment policy",
    "price_band": "same price band",
    "supplier": "same supplier",
    "sharing": "same sharing status",
}
# Only features whose value is NOT on the PRD 5.1 sensitive list may name it.
_REASON_VALUE = {"criticality", "route", "lead_time_band", "price_band"}
_CRIT_LABEL = {"h": "High", "m": "Medium", "l": "Low", "d": "Dead"}
_LEAD_TIME_LABEL = ("<=14d", "15-30d", "31-60d", "61-120d", ">120d")
_PRICE_LABEL = ("<$100", "$100-1k", "$1k-10k", ">$10k")


# ---------------------------------------------------------------------------
# feature extraction
# ---------------------------------------------------------------------------

def _num(value):
    """bom_rows.payload is written by ingestion with dtype=str, so every
    numeric field arrives as text and may be blank or non-numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _band(value, edges) -> int:
    v = _num(value)
    return -1 if v is None else bisect.bisect_right(edges, v)


def extract_features(payload: dict, rec, crit_config: dict,
                     category_rules=()) -> dict:
    """Decision-time attributes only.

    `payload` is the frozen bom_rows snapshot for the row's OWN batch, so a peer
    is compared on what was known when it was decided (spec section 8).
    Categorical values are strings ('' = missing); ordinals are band indices
    (-1 = missing).

    `part_category` is returned alongside the nine weighted features but is NOT
    one of them: it never appears in FEATURE_WEIGHTS/FEATURES/ORDINAL and never
    reaches the encoded matrices. run_similarity uses it as an eligibility
    filter instead -- see the block there.
    """
    machine = str(payload.get("machine_type") or "")
    family = machine.replace(";", ",").split(",")[0].strip().upper()

    # Engineer-confirmed criticality wins over the source column, exactly as
    # tools.get_procurement_context resolves it.
    configured = next((level for pattern, level in crit_config.items()
                       if pattern and pattern.lower() in machine.lower()), None)
    crit = str(configured or payload.get("sfm_criticality") or "").strip().lower()[:1]

    shareable = str(payload.get("shareable_indicator") or "").strip().upper()
    shared = str(payload.get("shared_parts") or "").strip().upper()
    if shareable.startswith("Y") or shared.startswith("Y"):
        sharing = "shared"
    elif shareable or shared:
        sharing = "local"
    else:
        sharing = ""

    return {
        "machine_family": family,
        "criticality": crit,
        "route": str(rec["route"] or "").strip(),
        "consumable": str(rec["consumable"] or "").strip(),
        "lead_time_band": _band(payload.get("contractual_lead_time"), LEAD_TIME_EDGES),
        "replenishment_policy": str(payload.get("replenishment_policy") or "").strip().lower(),
        "price_band": _band(payload.get("unitprice"), PRICE_EDGES),
        "supplier": str(payload.get("supplier_name") or "").strip().lower(),
        "sharing": sharing,
        # Constraint, not a weighted feature. Deliberately outside FEATURES.
        "part_category": categorise(payload.get("item_desc"), category_rules),
    }


def _encode(feature_dicts: list[dict], code_maps: dict) -> np.ndarray:
    """(N, F) integer matrix. -1 means missing on every feature.

    Codes only ever need to compare equal, so first-seen-wins numbering shared
    across pool and targets is sufficient.
    """
    arr = np.full((len(feature_dicts), len(FEATURES)), -1, dtype=np.int64)
    for r, feats in enumerate(feature_dicts):
        for c, name in enumerate(FEATURES):
            value = feats.get(name)
            if name in ORDINAL:
                arr[r, c] = int(value) if value is not None and value >= 0 else -1
            elif value:
                arr[r, c] = code_maps[name].setdefault(value, len(code_maps[name]))
    return arr


# ---------------------------------------------------------------------------
# distance
# ---------------------------------------------------------------------------

def feature_distances(target: np.ndarray, pool: np.ndarray,
                      band_counts: np.ndarray,
                      is_ordinal: np.ndarray) -> np.ndarray:
    """(N, F) per-feature distance in [0, 1] from one target to every pool row.

    Missingness is handled by side, which matters more than it looks:

      one side missing  -> 1.0. The match cannot be verified, and a peer we know
                           nothing about is not evidence.
      both sides missing -> 0.0. Equally uninformative is not the same as
                           dissimilar. Classic Gower drops such a pair from the
                           denominator entirely; scoring it 0 without
                           renormalising is the same thing while the weights
                           stay fixed, and it avoids a constant offset on every
                           distance whenever a column is sparse -- BOM_ENGINE=rules
                           emits no route/consumable at all, which would
                           otherwise add 0.15 to every pair in the batch.
    """
    one_missing = (pool < 0) ^ (target < 0)     # XOR: exactly one side unknown
    categorical = (pool != target).astype(float)
    ordinal = np.abs(pool - target) / np.maximum(band_counts - 1, 1)
    d = np.where(is_ordinal, np.minimum(ordinal, 1.0), categorical)
    return np.where(one_missing, 1.0, d)


def gower_distances(target: np.ndarray, pool: np.ndarray, weights: np.ndarray,
                    band_counts: np.ndarray,
                    is_ordinal: np.ndarray) -> np.ndarray:
    """Weighted Gower distance, shape (N,), in [0, 1] because the weights sum
    to 1. Mixed categorical/ordinal without any scaling artefact."""
    return feature_distances(target, pool, band_counts, is_ordinal) @ weights


def weighted_percentile(values: np.ndarray, weights: np.ndarray,
                        q: float) -> float:
    """Order-statistic percentile.

    Medians, not means: stocking values are heavily skewed and a single large
    peer would drag an average off the mode.
    """
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    return float(v[int(np.searchsorted(cum, q * cum[-1]))])


# ---------------------------------------------------------------------------
# neighbour narration
# ---------------------------------------------------------------------------

def _reason_value(name: str, feats: dict) -> str:
    if name == "criticality":
        return _CRIT_LABEL.get(feats["criticality"], "")
    if name == "route":
        return feats["route"]
    if name == "lead_time_band":
        band = feats["lead_time_band"]
        return _LEAD_TIME_LABEL[band] if 0 <= band < len(_LEAD_TIME_LABEL) else ""
    if name == "price_band":
        band = feats["price_band"]
        return _PRICE_LABEL[band] if 0 <= band < len(_PRICE_LABEL) else ""
    return ""


def _known(name: str, feats: dict) -> bool:
    value = feats.get(name)
    return value >= 0 if name in ORDINAL else bool(value)


def similarity_reasons(per_feature: np.ndarray, feats: dict,
                       peer_category: str = "") -> str:
    """Why this peer matched, in business terms rather than a bare 0.184.

    Heaviest-weighted matches first; sensitive features are named but their
    values are not. A feature that is UNKNOWN on both sides scores 0 distance
    (see feature_distances) but is never narrated as a match -- telling an
    engineer "same criticality" when neither part records one is worse than
    saying nothing.
    """
    matched = [(FEATURE_WEIGHTS[name], name)
               for i, name in enumerate(FEATURES)
               if per_feature[i] == 0.0 and _known(name, feats)]
    matched.sort(key=lambda wn: -wn[0])
    out = []
    # Category leads: it is the strongest statement about a peer and it is a
    # constraint rather than a weighted feature, so it does not consume one of
    # the MAX_REASONS weighted slots. Shown only when BOTH sides are known --
    # the block lets an uncategorised peer through, and claiming "same part
    # category" for a part nobody categorised would be false.
    category = feats.get("part_category", "")
    if category and peer_category and category == peer_category:
        out.append(f"same part category ({category})")
    for _, name in matched[:MAX_REASONS]:
        label = _REASON_LABEL[name]
        value = _reason_value(name, feats) if name in _REASON_VALUE else ""
        out.append(f"{label} ({value})" if value else label)
    return ", ".join(out)


# ---------------------------------------------------------------------------
# batch driver
# ---------------------------------------------------------------------------

_TARGET_SQL = (
    "SELECT r.batch_id, r.item_id, r.stockroom_id, r.new_max, r.new_rop, "
    "r.new_min, r.route, r.consumable, b.payload "
    "FROM recommendation_result r "
    "JOIN bom_rows b ON b.batch_id=r.batch_id AND b.item_id=r.item_id "
    "AND b.stockroom_id=r.stockroom_id "
    "LEFT JOIN similarity_result s ON s.batch_id=r.batch_id "
    "AND s.item_id=r.item_id AND s.stockroom_id=r.stockroom_id "
    "WHERE r.batch_id=? AND s.batch_id IS NULL ORDER BY r.item_id")

# Every reviewed row, with the snapshot it was reviewed against. ORDER BY
# review_id so the latest review per key overwrites earlier ones in the dict --
# the same idiom services.latest_reviews uses.
_POOL_SQL = (
    "SELECT h.batch_id, h.item_id, h.stockroom_id, h.decision, h.final_max, "
    "h.final_rop, h.final_min, h.engine_max, h.comment, h.justification, "
    "h.model_version AS peer_model_version, "
    "r.risk_level, r.reason_code, r.route, r.consumable, b.payload "
    "FROM review_history h "
    "JOIN recommendation_result r ON r.batch_id=h.batch_id "
    "AND r.item_id=h.item_id AND r.stockroom_id=h.stockroom_id "
    "JOIN bom_rows b ON b.batch_id=h.batch_id AND b.item_id=h.item_id "
    "AND b.stockroom_id=h.stockroom_id ORDER BY h.review_id")

# Column order in ONE place. _summarise returns a dict and the tuple is built
# from this list, so adding a column cannot silently shift every value by one --
# which a hand-maintained column list, placeholder count and positional tuple
# very much can.
_RESULT_COLUMNS = (
    "batch_id", "item_id", "stockroom_id", "similarity_model_version",
    "neighbour_count", "pool_size", "nearest_distance", "outlier_score",
    "is_outlier", "historical_override_rate", "historical_upward_override_rate",
    "historical_high_risk_rate", "analogue_max_median", "analogue_max_p25",
    "analogue_max_p75", "analogue_rop_median", "analogue_min_median",
    "part_category", "advisory_codes", "confidence")

_RESULT_INSERT = (
    "INSERT INTO similarity_result (" + ", ".join(_RESULT_COLUMNS) + ") "
    "VALUES (" + ",".join("?" * len(_RESULT_COLUMNS)) + ")")

_NEIGHBOUR_INSERT = (
    "INSERT INTO similarity_neighbour (batch_id, item_id, stockroom_id, "
    "neighbour_rank, neighbour_item_id, neighbour_stockroom_id, "
    "neighbour_batch_id, distance, similarity_reasons, neighbour_decision, "
    "neighbour_final_max, neighbour_final_rop, neighbour_final_min, "
    "neighbour_engine_max, neighbour_risk_level, neighbour_reason_code, "
    "neighbour_justification, neighbour_comment) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _moq(payload: dict) -> int:
    m = _as_int(payload.get("order_qty_multiple"), 1)
    return m if m >= 1 else 1


def _round_up(value: float, moq: int) -> int:
    return int(math.ceil(value / moq) * moq)


def _load_pool(conn: Conn, crit_config: dict, category_rules=()) -> list[dict]:
    """One precedent per stocking row, not one per month.

    Keyed on (item_id, stockroom_id) rather than including batch_id: the roster
    repeats, so a part reviewed in six monthly cycles would otherwise occupy six
    of the seven neighbour slots. neighbour_count would then claim seven
    independent precedents where there is really one part, and the
    distance-weighted median would tilt toward whichever part recurs most --
    the domination spec section 7 warns about, in a second guise. _POOL_SQL
    orders by review_id, so the surviving row is the most recent decision.
    """
    latest: dict[tuple, dict] = {}
    for row in conn.execute(_POOL_SQL):
        rec = dict(row)
        payload = json.loads(rec.pop("payload"))
        rec["features"] = extract_features(payload, rec, crit_config,
                                           category_rules)
        latest[(rec["item_id"], rec["stockroom_id"])] = rec
    return list(latest.values())


def run_similarity(conn: Conn, batch_id: int, refresh: bool = False) -> dict:
    batch = conn.execute("SELECT status FROM batches WHERE batch_id=?",
                         (batch_id,)).fetchone()
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    if batch["status"] != "scored":
        raise ValueError(f"batch {batch_id} has not been scored")

    cfg = active_config(conn)
    max_distance = float(cfg.get("similarity_max_distance", MAX_DISTANCE))
    min_neighbours = int(cfg.get("similarity_min_neighbours", MIN_NEIGHBOURS))
    k = int(cfg.get("similarity_k", K_NEIGHBOURS))
    divergence_frac = float(cfg.get("similarity_divergence_frac", DIVERGENCE_FRAC))
    crit_config = cfg.get("machine_criticality", {})
    # Compiled once per run, never per row -- an engineer-entered regex is
    # arbitrary work for the matching engine.
    category_rules, broken_rules = load_rules(conn)

    if refresh:
        # similarity_neighbour cascades off similarity_result.
        conn.execute("DELETE FROM similarity_result WHERE batch_id=?", (batch_id,))

    targets = []
    for row in conn.execute(_TARGET_SQL, (batch_id,)):
        rec = dict(row)
        payload = json.loads(rec.pop("payload"))
        rec["payload"] = payload
        rec["features"] = extract_features(payload, rec, crit_config,
                                           category_rules)
        targets.append(rec)

    pool = _load_pool(conn, crit_config, category_rules)
    results: list[dict] = []
    neighbours: list[tuple] = []
    n_outlier = n_diverge = n_no_analogue = n_categorised = 0

    if targets and pool:
        code_maps = {name: {} for name in FEATURES if name not in ORDINAL}
        pool_matrix = _encode([p["features"] for p in pool], code_maps)
        target_matrix = _encode([t["features"] for t in targets], code_maps)
        weights = np.array([FEATURE_WEIGHTS[n] for n in FEATURES], dtype=float)
        band_counts = np.array([ORDINAL.get(n, 2) for n in FEATURES], dtype=float)
        is_ordinal = np.array([n in ORDINAL for n in FEATURES])
        pool_items = np.array([p["item_id"] for p in pool], dtype=object)
        # dtype=object, not the inferred '<U..': a fixed-width string dtype
        # truncates to the longest value seen and comparisons then match wrongly.
        pool_cats = np.array([p["features"]["part_category"] for p in pool],
                             dtype=object)

        # ponytail: one Python iteration per target, vectorised across the whole
        # pool. 2.8k x pool is milliseconds; switch to a chunked (M,N,F) broadcast
        # if review_history ever passes ~100k rows.
        for target, encoded in zip(targets, target_matrix):
            per_feature = feature_distances(encoded, pool_matrix, band_counts,
                                            is_ordinal)
            distance = per_feature @ weights
            # Spec section 7: a part is never its own peer. Match on item_id
            # alone -- the same part in a second stockroom is still the same part.
            eligible = pool_items != target["item_id"]
            target_category = target["features"]["part_category"]
            if target_category:
                # Hard constraint, not a weighted feature: a cable is never a
                # screw's precedent however well machine, lead time and price
                # line up. A weight would only demote the mismatch; this removes
                # it. Peers whose own category is unknown stay eligible --
                # one-sided ignorance is not proof of a mismatch, the same logic
                # as the XOR rule in feature_distances.
                eligible &= (pool_cats == target_category) | (pool_cats == UNCATEGORISED)
            row, rows_n = _summarise(
                target, per_feature, distance, eligible, pool,
                max_distance, min_neighbours, k, divergence_frac)
            results.append(row)
            neighbours.extend(rows_n)
            if row["is_outlier"]:
                n_outlier += 1
            if NO_RELIABLE_ANALOGUE in row["advisory_codes"]:
                n_no_analogue += 1
            if ANALOGUE_DIVERGENCE in row["advisory_codes"]:
                n_diverge += 1
            if row["part_category"]:
                n_categorised += 1
    else:
        # No reviewed history yet. Honest answer: every row is unprecedented.
        for target in targets:
            category = target["features"]["part_category"]
            results.append({
                "batch_id": target["batch_id"], "item_id": target["item_id"],
                "stockroom_id": target["stockroom_id"],
                "similarity_model_version": MODEL_VERSION,
                "neighbour_count": 0, "pool_size": len(pool),
                "nearest_distance": None, "outlier_score": 1.0, "is_outlier": 1,
                "historical_override_rate": None,
                "historical_upward_override_rate": None,
                "historical_high_risk_rate": None,
                "analogue_max_median": None, "analogue_max_p25": None,
                "analogue_max_p75": None, "analogue_rop_median": None,
                "analogue_min_median": None, "part_category": category,
                "advisory_codes": NO_RELIABLE_ANALOGUE, "confidence": 0.0,
            })
            n_outlier += 1
            n_no_analogue += 1
            if category:
                n_categorised += 1

    if results:
        conn.executemany(_RESULT_INSERT,
                         [tuple(r[c] for c in _RESULT_COLUMNS) for r in results])
    if neighbours:
        conn.executemany(_NEIGHBOUR_INSERT, neighbours)

    return {"batch_id": batch_id, "candidates": len(targets),
            "scored": len(results), "neighbour_pool": len(pool),
            "outliers": n_outlier, "diverging": n_diverge,
            "no_analogue": n_no_analogue,
            "categorised": n_categorised,
            "uncategorised": len(results) - n_categorised,
            "broken_category_rules": broken_rules,
            "similarity_model_version": MODEL_VERSION}


def _summarise(target: dict, per_feature: np.ndarray, distance: np.ndarray,
               eligible: np.ndarray, pool: list[dict], max_distance: float,
               min_neighbours: int, k: int,
               divergence_frac: float) -> tuple[tuple, list[tuple]]:
    """One similarity_result row plus its similarity_neighbour rows."""
    codes: list[str] = []
    eligible_idx = np.flatnonzero(eligible)
    nearest = (float(distance[eligible_idx].min()) if eligible_idx.size
               else None)

    close = eligible_idx[distance[eligible_idx] <= max_distance]
    close = close[np.argsort(distance[close], kind="stable")][:k]
    count = int(close.size)

    if count:
        d = distance[close]
        w = 1.0 / (d + EPS)
        outlier_score = min(1.0, float(d.mean()) / max_distance)
        picked = [pool[i] for i in close]
        final_max = np.array([_as_int(p["final_max"]) for p in picked], dtype=float)
        final_rop = np.array([_as_int(p["final_rop"]) for p in picked], dtype=float)
        final_min = np.array([_as_int(p["final_min"]) for p in picked], dtype=float)
        # Rates are computed over decisions actually made against this engine.
        # Backfilled peers contribute their final_* to the medians above but
        # have no accept/override meaning; including them would drag every rate
        # toward zero and silently disable the triage promotion.
        rated = np.array([p.get("peer_model_version") != BACKFILL_MODEL_VERSION
                          for p in picked])
        if rated.any():
            rw = w[rated]
            rp = [p for p, keep in zip(picked, rated) if keep]
            override = np.array([p["decision"] == "override" for p in rp], dtype=float)
            upward = np.array([p["decision"] == "override"
                               and _as_int(p["final_max"]) > _as_int(p["engine_max"])
                               for p in rp], dtype=float)
            high_risk = np.array([p["risk_level"] == "High" for p in rp], dtype=float)
            total_w = float(rw.sum())
            override_rate = float(override @ rw) / total_w
            upward_rate = float(upward @ rw) / total_w
            high_risk_rate = float(high_risk @ rw) / total_w
        else:
            override_rate = upward_rate = high_risk_rate = None
    else:
        outlier_score = 1.0
        picked = []
        override_rate = upward_rate = high_risk_rate = None

    is_outlier = int(count < min_neighbours)
    coverage = min(1.0, count / min_neighbours) if min_neighbours else 0.0
    closeness = (max(0.0, 1.0 - nearest / max_distance)
                 if nearest is not None else 0.0)
    confidence = round(0.5 * coverage + 0.5 * closeness, 3)

    a_max = a_p25 = a_p75 = a_rop = a_min = None
    if count >= min_neighbours:
        moq = _moq(target["payload"])
        critical = target["features"]["criticality"] == "h"
        a_max = _round_up(weighted_percentile(final_max, w, 0.5), moq)
        a_p25 = _round_up(weighted_percentile(final_max, w, 0.25), moq)
        a_p75 = _round_up(weighted_percentile(final_max, w, 0.75), moq)
        a_rop = int(weighted_percentile(final_rop, w, 0.5))
        a_min = int(weighted_percentile(final_min, w, 0.5))
        if critical:
            # An analogue may never propose zeroing a critical spare, whatever
            # the peers happened to decide -- including the low end of the
            # displayed range, which an engineer reads as "0 is normal here".
            a_max = max(a_max, KEEP_ALIVE)
            a_p25 = max(a_p25, KEEP_ALIVE)
            a_p75 = max(a_p75, KEEP_ALIVE)
        # Independent percentiles can invert; the displayed triple must not.
        a_rop = min(a_rop, a_max)
        a_min = min(a_min, a_rop)
        a_p25 = min(a_p25, a_max)
        a_p75 = max(a_p75, a_max)
        engine_max = _as_int(target["new_max"])
        if abs(a_max - engine_max) > max(1.0, divergence_frac * engine_max):
            codes.append(ANALOGUE_DIVERGENCE)
        elif a_p25 <= engine_max <= a_p75:
            codes.append(ANALOGUE_CONCUR)
    else:
        codes.append(NO_RELIABLE_ANALOGUE)

    result = {
        "batch_id": target["batch_id"], "item_id": target["item_id"],
        "stockroom_id": target["stockroom_id"],
        "similarity_model_version": MODEL_VERSION,
        "neighbour_count": count, "pool_size": len(pool),
        "nearest_distance": nearest, "outlier_score": outlier_score,
        "is_outlier": is_outlier, "historical_override_rate": override_rate,
        "historical_upward_override_rate": upward_rate,
        "historical_high_risk_rate": high_risk_rate,
        "analogue_max_median": a_max, "analogue_max_p25": a_p25,
        "analogue_max_p75": a_p75, "analogue_rop_median": a_rop,
        "analogue_min_median": a_min,
        "part_category": target["features"]["part_category"],
        "advisory_codes": ",".join(codes), "confidence": confidence,
    }

    neighbour_rows = [
        (target["batch_id"], target["item_id"], target["stockroom_id"],
         rank, peer["item_id"], peer["stockroom_id"], peer["batch_id"],
         float(distance[idx]),
         similarity_reasons(per_feature[idx], target["features"],
                            peer["features"]["part_category"]),
         peer["decision"], peer["final_max"], peer["final_rop"],
         peer["final_min"], peer["engine_max"], peer["risk_level"],
         peer["reason_code"], peer["justification"], peer["comment"])
        for rank, (idx, peer) in enumerate(zip(close, picked), start=1)
    ]
    return result, neighbour_rows


def attach_neighbour_desc(conn: Conn, neighbours: list[dict]) -> list[dict]:
    """Add neighbour_item_desc from the peer's OWN frozen bom_rows payload.

    Read-time, not stored: item_desc already lives in bom_rows and copying it
    into similarity_neighbour would need a migration plus a re-run for every
    row already scored. Keyed on (batch_id, item_id) only -- the same item in
    two stockrooms carries the same description.
    """
    cache: dict[tuple, str | None] = {}
    for n in neighbours:
        key = (n["neighbour_batch_id"], n["neighbour_item_id"])
        if key not in cache:
            row = conn.execute(
                "SELECT payload FROM bom_rows WHERE batch_id=? AND item_id=? "
                "LIMIT 1", key).fetchone()
            cache[key] = json.loads(row["payload"]).get("item_desc") if row else None
        n["neighbour_item_desc"] = cache[key]
    return neighbours
