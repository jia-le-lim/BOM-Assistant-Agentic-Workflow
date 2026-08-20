"""The advisory KNN peer layer: maths, persistence, and the safety boundary.

Note on the engine: conftest pins BOM_ENGINE=rules for the whole suite, and the
rule engine emits no route/consumable (engine_adapter defaults them to ''), so
those two features are missing on every row here and contribute their full
0.15 to every distance. The unit tests below therefore build their own vectors
rather than asserting thresholds against engine output.
"""

import json
import sqlite3

import numpy as np
import pytest
from conftest import ENG, SENIOR, VIEWER, make_row, rows_to_csv, upload

from app import similarity as S


# ---------------------------------------------------------------------------
# unit: distance and percentiles
# ---------------------------------------------------------------------------

def _arrays():
    weights = np.array([S.FEATURE_WEIGHTS[n] for n in S.FEATURES], dtype=float)
    bands = np.array([S.ORDINAL.get(n, 2) for n in S.FEATURES], dtype=float)
    ordinal = np.array([n in S.ORDINAL for n in S.FEATURES])
    return weights, bands, ordinal


def _vec(**overrides) -> np.ndarray:
    """A fully-populated encoded row; override by feature name."""
    base = {name: 1 for name in S.FEATURES}
    base.update({"lead_time_band": 0, "price_band": 0})
    base.update(overrides)
    return np.array([base[n] for n in S.FEATURES], dtype=np.int64)


def test_weights_sum_to_one():
    """A mistyped weight silently denormalises every distance."""
    assert sum(S.FEATURE_WEIGHTS.values()) == pytest.approx(1.0)


def test_identical_rows_have_zero_distance():
    w, b, o = _arrays()
    t = _vec()
    assert S.gower_distances(t, t.reshape(1, -1), w, b, o)[0] == 0.0


def test_one_sided_missing_scores_full_distance():
    """Known on one side, unknown on the other: the match cannot be verified."""
    w, b, o = _arrays()
    t = _vec()
    pool = _vec(machine_family=-1).reshape(1, -1)
    assert S.gower_distances(t, pool, w, b, o)[0] == pytest.approx(0.25)


def test_both_sides_missing_is_not_dissimilar():
    """Equally uninformative is not the same as dissimilar. Without this, a
    column that is empty for the whole batch (BOM_ENGINE=rules emits no
    route/consumable) would add a constant offset to every distance."""
    w, b, o = _arrays()
    t = _vec(machine_family=-1, lead_time_band=-1)
    pool = _vec(machine_family=-1, lead_time_band=-1).reshape(1, -1)
    assert S.gower_distances(t, pool, w, b, o)[0] == 0.0


def test_criticality_dominates_consumption_match():
    """The spec's core safety property: matching demand numbers must not make a
    cheap consumable 'similar' to a critical insurance spare."""
    w, b, o = _arrays()
    t = _vec(machine_family=1, criticality=1)
    peer = _vec(machine_family=2, criticality=2).reshape(1, -1)
    assert S.gower_distances(t, peer, w, b, o)[0] > S.MAX_DISTANCE


def test_ordinal_band_distance_is_proportional():
    w, b, o = _arrays()
    t = _vec(lead_time_band=0)
    one = S.gower_distances(t, _vec(lead_time_band=1).reshape(1, -1), w, b, o)[0]
    four = S.gower_distances(t, _vec(lead_time_band=4).reshape(1, -1), w, b, o)[0]
    assert four == pytest.approx(4 * one)


def test_weighted_percentile_is_distance_weighted():
    values = np.array([1.0, 10.0])
    weights = np.array([100.0, 1.0])
    assert S.weighted_percentile(values, weights, 0.5) == 1.0


def test_similarity_reasons_never_name_a_sensitive_value():
    """redact.py masks by key NAME, so a supplier or machine_type inside this
    free text would bypass LLM_REDACT_PROMPTS entirely."""
    per_feature = np.zeros(len(S.FEATURES))
    feats = {"machine_family": "ASM-PACIFIC", "criticality": "h",
             "route": "active", "consumable": "constant",
             "lead_time_band": 3, "replenishment_policy": "order to max",
             "price_band": 2, "supplier": "acme pte ltd", "sharing": "shared"}
    text = S.similarity_reasons(per_feature, feats)
    assert "ASM-PACIFIC" not in text.upper()
    assert "ACME" not in text.upper()
    assert "same machine family" in text
    assert "same criticality (High)" in text


def test_similarity_reasons_skip_features_unknown_on_both_sides():
    """Both-missing scores 0 distance, but narrating it as a match would tell
    the engineer 'same criticality' when neither part records one."""
    per_feature = np.zeros(len(S.FEATURES))
    feats = {name: "" for name in S.FEATURES}
    feats.update({"lead_time_band": -1, "price_band": -1,
                  "machine_family": "ASM-PACIFIC"})
    text = S.similarity_reasons(per_feature, feats)
    assert text == "same machine family"


# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------

def scored_batch(client, csv_bytes, label="TEST"):
    b = upload(client, csv_bytes, label=label).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b


def rows(db_file, sql, params=()):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def review_everything(client, batch_id, decision="accept"):
    """Push every pending row through the one legitimate path into
    review_history (review.py:37-45 -- nothing else may INSERT there)."""
    listing = client.get(f"/recommendations?batch_id={batch_id}&limit=500",
                         headers=ENG).json()
    done = 0
    for item in listing["items"]:
        body = {"decision": decision, "comment": "long lead time insurance",
                "justification": "Critical insurance spare"}
        r = client.post(f"/review/{item['item_id']}?batch_id={batch_id}"
                        f"&stockroom_id={item['stockroom_id']}",
                        json=body, headers=SENIOR)
        if r.status_code == 200:
            done += 1
    return done


def test_run_on_empty_pool_flags_everything(client, synth_csv, db_file):
    """First ever run: no reviewed history exists, so nothing is precedented.
    Must be honest about that rather than crash or invent a range."""
    batch_id = scored_batch(client, synth_csv)
    r = client.post("/similarity/run", json={"batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["neighbour_pool"] == 0
    assert summary["scored"] == summary["candidates"] > 0
    assert summary["outliers"] == summary["scored"]
    assert summary["no_analogue"] == summary["scored"]

    saved = rows(db_file, "SELECT is_outlier, advisory_codes, "
                          "analogue_max_median FROM similarity_result")
    assert saved and all(s[0] == 1 for s in saved)
    assert all(s[1] == "NO_RELIABLE_ANALOGUE" for s in saved)
    assert all(s[2] is None for s in saved)


def test_run_persists_results_and_neighbours(client, synth_csv, db_file):
    first = scored_batch(client, synth_csv, label="JAN")
    assert review_everything(client, first) > 0

    second = scored_batch(client, synth_csv, label="FEB")
    summary = client.post("/similarity/run", json={"batch_id": second},
                          headers=ENG).json()
    assert summary["neighbour_pool"] > 0
    assert summary["scored"] > 0
    assert rows(db_file, "SELECT COUNT(*) FROM similarity_neighbour "
                         "WHERE batch_id=?", (second,))[0][0] > 0


def test_item_is_never_its_own_neighbour(client, synth_csv, db_file):
    """Spec section 7. Match on item_id alone -- the same part in a second
    stockroom is still the same part."""
    first = scored_batch(client, synth_csv, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, synth_csv, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    self_matches = rows(db_file,
                        "SELECT COUNT(*) FROM similarity_neighbour "
                        "WHERE item_id = neighbour_item_id")
    assert self_matches[0][0] == 0


def test_similarity_never_writes_recommendation_or_review(client, synth_csv,
                                                          db_file):
    """The boundary. This layer adds evidence; it decides nothing."""
    first = scored_batch(client, synth_csv, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, synth_csv, label="FEB")

    before_rec = rows(db_file, "SELECT * FROM recommendation_result ORDER BY "
                               "batch_id, item_id, stockroom_id")
    before_rev = rows(db_file, "SELECT * FROM review_history ORDER BY review_id")
    before_pending = rows(db_file, "SELECT COUNT(*) FROM pending_change")

    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    assert rows(db_file, "SELECT * FROM recommendation_result ORDER BY "
                         "batch_id, item_id, stockroom_id") == before_rec
    assert rows(db_file, "SELECT * FROM review_history "
                         "ORDER BY review_id") == before_rev
    assert rows(db_file, "SELECT COUNT(*) FROM pending_change") == before_pending


def test_analogue_range_is_null_below_min_neighbours(client, synth_csv, db_file):
    """One reviewed peer is not evidence. No range may be emitted."""
    first = scored_batch(client, synth_csv, label="JAN")
    listing = client.get(f"/recommendations?batch_id={first}&limit=500",
                         headers=ENG).json()["items"][0]
    client.post(f"/review/{listing['item_id']}?batch_id={first}"
                f"&stockroom_id={listing['stockroom_id']}",
                json={"decision": "accept"}, headers=SENIOR)

    second = scored_batch(client, synth_csv, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)
    saved = rows(db_file, "SELECT analogue_max_median, advisory_codes, "
                          "is_outlier FROM similarity_result WHERE batch_id=?",
                 (second,))
    assert saved
    assert all(s[0] is None for s in saved)
    assert all("NO_RELIABLE_ANALOGUE" in s[1] for s in saved)
    assert all(s[2] == 1 for s in saved)


def _multi_peer_csv(order_multiple="", criticality="", final_zero=False):
    """Eight near-identical parts so the neighbour pool clears MIN_NEIGHBOURS."""
    return rows_to_csv([
        make_row(item_id=700000 + i, max_qty=4, rop_qty=2, min_qty=1,
                 unitprice=250, contractual_lead_time=45,
                 order_qty_multiple=order_multiple,
                 sfm_criticality=criticality,
                 machine_type="ASM-Pacific,Phoenix",
                 replenishment_policy="Order to Demand",
                 supplier_name="Acme Pte Ltd",
                 shareable_indicator="N",
                 last_90_day_cnsmptn_qty=1, last_180_day_cnsmptn_qty=1,
                 last_365_day_cnsmptn_qty=2, last_547_day_cnsmptn_qty=2)
        for i in range(8)
    ])


def test_analogue_max_respects_order_multiple(client, db_file):
    csv_bytes = _multi_peer_csv(order_multiple=5)
    first = scored_batch(client, csv_bytes, label="JAN")
    assert review_everything(client, first) >= 5

    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)
    saved = rows(db_file, "SELECT analogue_max_median FROM similarity_result "
                          "WHERE batch_id=? AND analogue_max_median IS NOT NULL",
                 (second,))
    assert saved, "expected at least one row with enough neighbours"
    assert all(s[0] % 5 == 0 for s in saved)


def test_critical_analogue_never_zero(client, db_file):
    """Keep-alive floor: peers that all decided zero must not zero a critical
    spare's analogue."""
    csv_bytes = _multi_peer_csv(criticality="H")
    first = scored_batch(client, csv_bytes, label="JAN")
    listing = client.get(f"/recommendations?batch_id={first}&limit=500",
                         headers=ENG).json()["items"]
    for item in listing:
        client.post(f"/review/{item['item_id']}?batch_id={first}"
                    f"&stockroom_id={item['stockroom_id']}",
                    json={"decision": "override", "final_max": 0,
                          "final_rop": 0, "final_min": 0,
                          "justification": "zeroed"}, headers=SENIOR)

    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)
    saved = rows(db_file, "SELECT analogue_max_median FROM similarity_result "
                          "WHERE batch_id=? AND analogue_max_median IS NOT NULL",
                 (second,))
    assert saved
    assert all(s[0] >= S.KEEP_ALIVE for s in saved)


def test_refresh_rebuilds_and_resume_skips(client, synth_csv):
    batch_id = scored_batch(client, synth_csv)
    first = client.post("/similarity/run", json={"batch_id": batch_id},
                        headers=ENG).json()
    assert first["scored"] > 0

    resumed = client.post("/similarity/run", json={"batch_id": batch_id},
                          headers=ENG).json()
    assert resumed["candidates"] == 0
    assert resumed["scored"] == 0

    rebuilt = client.post("/similarity/run",
                          json={"batch_id": batch_id, "refresh": True},
                          headers=ENG).json()
    assert rebuilt["scored"] == first["scored"]


def test_run_requires_scored_batch(client, synth_csv):
    batch_id = upload(client, synth_csv).json()["batch_id"]
    r = client.post("/similarity/run", json={"batch_id": batch_id}, headers=ENG)
    assert r.status_code == 400
    assert "not been scored" in r.text


def test_unknown_batch_is_rejected(client):
    assert client.post("/similarity/run", json={"batch_id": 99999},
                       headers=ENG).status_code == 400
    assert client.get("/similarity/99999", headers=ENG).status_code == 404


def test_viewer_cannot_run_or_read_similarity(client, synth_csv):
    batch_id = scored_batch(client, synth_csv)
    assert client.post("/similarity/run", json={"batch_id": batch_id},
                       headers=VIEWER).status_code == 403
    assert client.get(f"/similarity/{batch_id}", headers=VIEWER).status_code == 403


def test_missing_item_returns_404(client, synth_csv):
    batch_id = scored_batch(client, synth_csv)
    client.post("/similarity/run", json={"batch_id": batch_id}, headers=ENG)
    assert client.get(f"/similarity/{batch_id}/nosuchitem",
                      headers=ENG).status_code == 404


# ---------------------------------------------------------------------------
# chat tools and the triage promotion
# ---------------------------------------------------------------------------

def test_get_similar_parts_withholds_stockroom(client, synth_csv):
    """stockroom_id is PRD 5.1 sensitive and redact.py masks by key name, so the
    neighbour-prefixed column must simply not be returned."""
    from app.agent import tools as T
    from app.db import get_conn

    first = scored_batch(client, synth_csv, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, synth_csv, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    conn = get_conn()
    try:
        item = conn.execute("SELECT item_id FROM similarity_result "
                            "WHERE batch_id=? LIMIT 1", (second,)).fetchone()
        ctx = T.ToolContext(conn=conn, actor={"user": "alice"},
                            batch_id=second, question="similar parts")
        out = json.loads(T.dispatch(ctx, "get_similar_parts",
                                    {"item_id": item["item_id"]}))
    finally:
        conn.close()
    assert "neighbour_stockroom_id" not in json.dumps(out)
    assert out.get("caveat")


def test_search_similar_reviews_finds_a_comment(client, synth_csv):
    from app.agent import tools as T
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    review_everything(client, batch_id)

    conn = get_conn()
    try:
        ctx = T.ToolContext(conn=conn, actor={"user": "alice"},
                            batch_id=batch_id, question="lead time")
        out = json.loads(T.dispatch(ctx, "search_similar_reviews",
                                    {"query": "lead time"}))
    finally:
        conn.close()
    assert out.get("results"), out
    assert ctx.sources


def test_outlier_blocks_clear_candidate(monkeypatch):
    """Asymmetry: peer evidence may add risk, never cancel a rule."""
    from app.agent.specialists import synthesis
    from app.llm.provider import Response

    class ClearProvider:
        name, model = "stub", "stub-1"

        def chat(self, messages, tools):
            return Response(content='{"tier":"clear_candidate",'
                                    '"priority_score":10,"rationale":"fine",'
                                    '"confidence":1,"focus_question":"none"}')

    monkeypatch.setattr("app.agent.specialists.get_provider",
                        lambda: ClearProvider())
    verdict = synthesis({
        "recommendation": {"item_id": "X", "agreement": "match",
                           "risk_level": "Low", "confidence": 0.95},
        "features": {"critical": False, "high_exposure": False,
                     "similarity_outlier": True, "similarity_elevated": False},
        "clear_confidence_threshold": 0.8,
    })
    assert verdict["triage_tier"] == "review"


def test_elevated_peers_raise_priority_only(monkeypatch):
    from app.agent.specialists import synthesis
    from app.llm.provider import Response

    class Provider:
        name, model = "stub", "stub-1"

        def chat(self, messages, tools):
            return Response(content='{"tier":"review","priority_score":50,'
                                    '"rationale":"r","confidence":0.5,'
                                    '"focus_question":"q"}')

    monkeypatch.setattr("app.agent.specialists.get_provider", lambda: Provider())
    state = {
        "recommendation": {"item_id": "X", "agreement": "match",
                           "risk_level": "Low", "confidence": 0.5},
        "features": {"critical": False, "high_exposure": False,
                     "similarity_outlier": False, "similarity_elevated": True},
        "clear_confidence_threshold": 0.8,
    }
    assert synthesis(state)["priority_score"] == 65

    state["features"]["similarity_elevated"] = False
    assert synthesis(state)["priority_score"] == 50


def test_synthesis_tolerates_absent_similarity_keys(monkeypatch):
    """Callers that predate this feature pass a features dict without the two
    similarity keys; .get() must keep them working."""
    from app.agent.specialists import synthesis
    from app.llm.provider import Response

    class Provider:
        name, model = "stub", "stub-1"

        def chat(self, messages, tools):
            return Response(content='{"tier":"review","priority_score":42,'
                                    '"rationale":"r","confidence":0.5,'
                                    '"focus_question":"q"}')

    monkeypatch.setattr("app.agent.specialists.get_provider", lambda: Provider())
    verdict = synthesis({
        "recommendation": {"item_id": "X", "agreement": "match",
                           "risk_level": "Low", "confidence": 0.5},
        "features": {"critical": False, "high_exposure": False},
        "clear_confidence_threshold": 0.8,
    })
    assert verdict["priority_score"] == 42


def test_a_peer_appears_once_however_many_months_it_was_reviewed(client, db_file):
    """The roster repeats monthly. Without collapsing to the latest decision per
    stocking row, one part reviewed six times fills six of seven neighbour slots
    and neighbour_count claims independent precedents that do not exist."""
    csv_bytes = _multi_peer_csv()
    for label in ("M1", "M2", "M3"):
        b = scored_batch(client, csv_bytes, label=label)
        review_everything(client, b)

    target = scored_batch(client, csv_bytes, label="NOW")
    client.post("/similarity/run", json={"batch_id": target}, headers=ENG)

    dupes = rows(db_file,
                 "SELECT item_id, neighbour_item_id, COUNT(*) n "
                 "FROM similarity_neighbour WHERE batch_id=? "
                 "GROUP BY item_id, neighbour_item_id HAVING COUNT(*) > 1",
                 (target,))
    assert dupes == [], f"peer repeated across months: {dupes}"


def test_backfilled_peers_feed_medians_but_not_rates(client, db_file):
    """A backfilled decision is a real engineer number, so it counts toward the
    analogue medians. It was never a response to THIS engine, so it must not
    dilute the override / high-risk rates that drive triage promotion."""
    import sqlite3 as sq

    csv_bytes = _multi_peer_csv()
    first = scored_batch(client, csv_bytes, label="JAN")
    assert review_everything(client, first) >= 5

    conn = sq.connect(db_file)
    try:
        conn.execute("UPDATE review_history SET model_version=?, decision=?",
                     (S.BACKFILL_MODEL_VERSION, "historical"))
        conn.commit()
    finally:
        conn.close()

    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    saved = rows(db_file, "SELECT analogue_max_median, historical_override_rate, "
                          "historical_high_risk_rate, neighbour_count "
                          "FROM similarity_result WHERE batch_id=? "
                          "AND neighbour_count > 0", (second,))
    assert saved, "expected neighbours to be found"
    assert all(s[0] is not None for s in saved), "medians must still be produced"
    assert all(s[1] is None and s[2] is None for s in saved), \
        "rates must be NULL when every peer is backfilled"


# ---------------------------------------------------------------------------
# part-category constraint
# ---------------------------------------------------------------------------

def _mixed_category_csv(target_desc="POWER CABLE ASSY"):
    """One target plus peers of two different categories, otherwise identical.

    Everything except item_desc is held constant, so the ONLY thing that can
    separate a cable from a fastener here is the category block.
    """
    rows = [make_row(item_id=800000, item_desc=target_desc, max_qty=4, rop_qty=2,
                     min_qty=1, unitprice=250, contractual_lead_time=45,
                     machine_type="ASM-Pacific,Phoenix",
                     replenishment_policy="Order to Demand",
                     supplier_name="Acme Pte Ltd", shareable_indicator="N")]
    for i in range(6):
        rows.append(make_row(item_id=810000 + i, item_desc="POWER CABLE ASSY",
                             max_qty=4, rop_qty=2, min_qty=1, unitprice=250,
                             contractual_lead_time=45,
                             machine_type="ASM-Pacific,Phoenix",
                             replenishment_policy="Order to Demand",
                             supplier_name="Acme Pte Ltd",
                             shareable_indicator="N"))
    for i in range(6):
        rows.append(make_row(item_id=820000 + i, item_desc="HEX SCREW M4",
                             max_qty=4, rop_qty=2, min_qty=1, unitprice=250,
                             contractual_lead_time=45,
                             machine_type="ASM-Pacific,Phoenix",
                             replenishment_policy="Order to Demand",
                             supplier_name="Acme Pte Ltd",
                             shareable_indicator="N"))
    return rows_to_csv(rows)


def _peer_descs(db_file, batch_id, item_id):
    return [r[0] for r in rows(
        db_file,
        "SELECT n.neighbour_item_id FROM similarity_neighbour n "
        "WHERE n.batch_id=? AND n.item_id=?", (batch_id, str(item_id)))]


def test_different_category_is_never_a_peer(client, db_file):
    """The whole feature. Cables and screws here differ ONLY by description."""
    csv_bytes = _mixed_category_csv()
    first = scored_batch(client, csv_bytes, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    peers = _peer_descs(db_file, second, 800000)
    assert peers, "expected the cable to find cable peers"
    assert all(p.startswith("81") for p in peers), \
        f"a fastener leaked into a cable's peer set: {peers}"

    cat = rows(db_file, "SELECT part_category FROM similarity_result "
                        "WHERE batch_id=? AND item_id='800000'", (second,))
    assert cat[0][0] == "cable"


def test_uncategorised_target_is_not_restricted(client, db_file):
    """No rule matches, so the block must not fire -- behaves as before."""
    csv_bytes = _mixed_category_csv(target_desc="ZZZQQQ UNKNOWN THING")
    first = scored_batch(client, csv_bytes, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    cat = rows(db_file, "SELECT part_category FROM similarity_result "
                        "WHERE batch_id=? AND item_id='800000'", (second,))
    assert cat[0][0] == ""
    peers = _peer_descs(db_file, second, 800000)
    assert peers, "an uncategorised part must still get peers"
    assert any(p.startswith("82") for p in peers), \
        "an unrestricted target should be able to reach both categories"


def test_category_leads_the_reasons_when_known(client, db_file):
    csv_bytes = _mixed_category_csv()
    first = scored_batch(client, csv_bytes, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    reasons = rows(db_file, "SELECT similarity_reasons FROM similarity_neighbour "
                            "WHERE batch_id=? AND item_id='800000'", (second,))
    assert reasons
    assert all(r[0].startswith("same part category (cable)") for r in reasons)


def test_category_never_narrated_when_unknown(client, db_file):
    csv_bytes = _mixed_category_csv(target_desc="ZZZQQQ UNKNOWN THING")
    first = scored_batch(client, csv_bytes, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    reasons = rows(db_file, "SELECT similarity_reasons FROM similarity_neighbour "
                            "WHERE batch_id=? AND item_id='800000'", (second,))
    assert reasons
    assert not any("same part category" in r[0] for r in reasons)


def test_unconfirmed_rule_does_not_affect_retrieval(client, db_file):
    """A proposal must not change what an engineer is shown."""
    import sqlite3 as sq
    csv_bytes = _mixed_category_csv(target_desc="ZZZQQQ UNKNOWN THING")
    first = scored_batch(client, csv_bytes, label="JAN")
    review_everything(client, first)

    r = client.post("/config/part-categories",
                    json={"pattern": r"\bZZZQQQ\b", "category": "widget",
                          "priority": 1}, headers=ENG)
    assert r.status_code == 200, r.text

    second = scored_batch(client, csv_bytes, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)
    cat = rows(db_file, "SELECT part_category FROM similarity_result "
                        "WHERE batch_id=? AND item_id='800000'", (second,))
    assert cat[0][0] == "", "an unconfirmed rule was applied"

    conn = sq.connect(db_file)
    try:
        conn.execute("UPDATE part_category_config SET confirmed=1, "
                     "confirmed_by='boss' WHERE pattern=?", (r"\bZZZQQQ\b",))
        conn.commit()
    finally:
        conn.close()
    client.post("/similarity/run", json={"batch_id": second, "refresh": True},
                headers=ENG)
    cat = rows(db_file, "SELECT part_category FROM similarity_result "
                        "WHERE batch_id=? AND item_id='800000'", (second,))
    assert cat[0][0] == "widget", "a confirmed rule was not applied"


def test_run_summary_reports_category_coverage(client, synth_csv):
    batch_id = scored_batch(client, synth_csv)
    s = client.post("/similarity/run", json={"batch_id": batch_id},
                    headers=ENG).json()
    assert s["categorised"] + s["uncategorised"] == s["scored"]
    assert s["broken_category_rules"] == []


def test_feature_weights_are_untouched_by_the_category():
    """Category is a constraint, not a weighted feature. If someone adds it to
    FEATURE_WEIGHTS the distances silently denormalise."""
    assert sum(S.FEATURE_WEIGHTS.values()) == pytest.approx(1.0)
    assert "part_category" not in S.FEATURE_WEIGHTS
    assert "part_category" not in S.FEATURES


def test_triage_still_runs_without_similarity(client, synth_csv):
    """Similarity is optional; triage must not depend on it having been run."""
    batch_id = scored_batch(client, synth_csv)
    r = client.post("/triage/run", json={"batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["triaged"] > 0


def test_neighbours_carry_the_peer_description(client, synth_csv, db_file):
    """The engineer identifies a peer by what it IS, not by its item number."""
    first = scored_batch(client, synth_csv, label="JAN")
    review_everything(client, first)
    second = scored_batch(client, synth_csv, label="FEB")
    client.post("/similarity/run", json={"batch_id": second}, headers=ENG)

    item_id = rows(db_file, "SELECT item_id FROM similarity_neighbour "
                            "WHERE batch_id=? LIMIT 1", (second,))[0][0]
    got = client.get(f"/similarity/{second}/{item_id}", headers=ENG).json()
    assert got["neighbours"], "no neighbours to check"
    for n in got["neighbours"]:
        want = json.loads(rows(db_file, "SELECT payload FROM bom_rows "
                                        "WHERE batch_id=? AND item_id=? LIMIT 1",
                               (n["neighbour_batch_id"],
                                n["neighbour_item_id"]))[0][0])["item_desc"]
        assert n["neighbour_item_desc"] == want
