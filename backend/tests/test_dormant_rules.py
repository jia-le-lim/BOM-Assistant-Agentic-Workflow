"""Dormant stocking rules: resolution order, the confirm gate, and the override.

Ordering is the contract: an item rule beats a category rule beats the default.
Scope alone decides it -- (scope, match_key) is unique, so at most one rule can
match at each tier and there is no tie for a priority number to break.

The property that matters most is the last one: an EMPTY rule table must
reproduce the engine's own answer exactly. Layer 1 is a stock-moving change
(owner decision Q2, 2026-09-01), so "no confirmed rule" has to mean "nothing
moved", not "something slightly different".
"""

import pandas as pd
import pytest
from conftest import ENG, OWNER_SENIOR as SENIOR, OWNER_VIEWER as VIEWER

from app import dormant_rules as DR


def rule(scope="default", key="", policy="hold_current", qty=None):
    return {"scope": scope, "match_key": key, "policy": policy,
            "fixed_qty": qty}


# --- resolve ---------------------------------------------------------------

def test_item_beats_category_beats_default():
    rules = [rule(), rule("category", "filter", policy="fixed_qty", qty=2),
             rule("item", "100005", policy="fixed_qty", qty=9)]
    assert DR.resolve(rules, "100005", "filter")["fixed_qty"] == 9
    assert DR.resolve(rules, "999999", "filter")["fixed_qty"] == 2
    assert DR.resolve(rules, "999999", "cable")["scope"] == "default"


def test_item_rule_wins_however_the_table_is_ordered():
    """Scope is the whole contract. No priority number can invert it, which is
    the point of deleting that column: a per-part exception always holds."""
    rules = [rule("item", "100005", policy="fixed_qty", qty=9),
             rule("category", "filter", policy="fixed_qty", qty=2)]
    assert DR.resolve(rules, "100005", "filter")["fixed_qty"] == 9
    assert DR.resolve(rules[::-1], "100005", "filter")["fixed_qty"] == 9


def test_uncategorised_row_never_matches_a_category_rule():
    """'' is meaningful: an uncategorised part is not silently every category."""
    rules = [rule("category", "", policy="fixed_qty", qty=4)]
    assert DR.resolve(rules, "100005", "") is None


def test_no_rules_resolves_to_none():
    assert DR.resolve([], "100005", "filter") is None


# --- apply -----------------------------------------------------------------

def test_hold_current_keeps_the_current_level():
    assert DR.apply(rule(policy="hold_current"), 7) == (7, 7, 7)


@pytest.mark.parametrize("missing", [None, float("nan"), "", "n/a"])
def test_hold_current_without_a_current_level_declines(missing):
    """A missing stock level is a gap in the extract, not a decision to stock
    nothing -- returning 0 here is the exact silent zero this layer fixes."""
    assert DR.apply(rule(policy="hold_current"), missing) is None


def test_fixed_qty_and_zero():
    assert DR.apply(rule(policy="fixed_qty", qty=2), 7) == (2, 2, 2)
    assert DR.apply(rule(policy="zero", qty=None), 7) == (0, 0, 0)


def test_fixed_qty_without_a_quantity_declines():
    assert DR.apply(rule(policy="fixed_qty", qty=None), 7) is None


def test_apply_does_not_round_to_an_order_multiple():
    """The quantity is a number an engineer wrote down. Rounding it up would
    quietly return something they did not ask for."""
    assert DR.apply(rule(policy="fixed_qty", qty=3), 10) == (3, 3, 3)


# --- the engine override ---------------------------------------------------

DORMANT_ROW = {
    "item_id": "100005", "stockroom_id": "", "item_desc": "FILTER,ASSEMBLY,DIE",
    "part_category": "filter", "sfm_criticality": "Medium",
    "contractual_lead_time": 30, "order_qty_multiple": 1, "unitprice": 12.5,
    "max_qty": 4, "rop_qty": 2, "min_qty": 1, "frequencymonthswithusage": 0,
    **{f"last_{w}_day_cnsmptn_qty": 0 for w in (5, 30, 90, 180, 365, 547)},
}


def score(cfg):
    """The statistical engine, which conftest's BOM_ENGINE=rules does not pin."""
    from app import engine_statistical
    return engine_statistical.run(pd.DataFrame([DORMANT_ROW]), cfg)


def test_no_rules_reproduces_the_engines_own_zero():
    row = score({}).iloc[0]
    assert row["factory_recommended_new_max"] == 0
    assert "DORMANT_NONCRITICAL_ZERO" in row["reason_code"]
    assert "DORMANT_RULE_APPLIED" not in row["reason_code"]


def test_a_matching_rule_overrides_the_engine():
    row = score({"dormant_rules": [
        rule("category", "filter", policy="fixed_qty", qty=2)]}).iloc[0]
    assert row["factory_recommended_new_max"] == 2
    assert row["factory_recommended_new_rop"] == 2
    assert row["factory_recommended_new_min"] == 2
    assert "DORMANT_RULE_APPLIED" in row["reason_code"]


def test_a_rule_sized_row_still_reaches_a_human():
    """The rule changes the NUMBER, not whether anyone looks. review_required
    'N' here would silently clear 8,343 rows."""
    row = score({"dormant_rules": [rule(policy="hold_current")]}).iloc[0]
    assert row["review_required"] == "Y"
    assert row["factory_recommended_new_max"] == 4       # the current level


def test_a_declining_rule_leaves_the_engine_alone():
    """hold_current with no current level falls through to the existing branch."""
    df = pd.DataFrame([{**DORMANT_ROW, "max_qty": None}])
    from app import engine_statistical
    row = engine_statistical.run(df, {"dormant_rules": [rule()]}).iloc[0]
    assert row["factory_recommended_new_max"] == 0
    assert "DORMANT_NONCRITICAL_ZERO" in row["reason_code"]


def test_rule_beats_the_critical_keepalive_branch():
    df = pd.DataFrame([{**DORMANT_ROW, "sfm_criticality": "High"}])
    from app import engine_statistical
    row = engine_statistical.run(
        df, {"dormant_rules": [rule(policy="fixed_qty", qty=6)]}).iloc[0]
    assert row["factory_recommended_new_max"] == 6
    assert "DORMANT_CRITICAL_KEEPALIVE" not in row["reason_code"]


# --- the API: propose -> confirm -> engine reads ---------------------------

BODY = {"scope": "category", "match_key": "filter",
        "policy": "fixed_qty", "fixed_qty": 2}


def test_propose_is_pending_and_owner_needs_approval_rights(client):
    r = client.post("/config/dormant-rules", json=BODY, headers=ENG)
    assert r.status_code == 200, r.text
    rule_id = r.json()["rule_id"]
    assert r.json()["confirmed"] is False

    assert client.post(f"/config/dormant-rules/{rule_id}/confirm",
                       headers=ENG).status_code == 403
    assert client.post(f"/config/dormant-rules/{rule_id}/confirm",
                       headers=SENIOR).status_code == 200
    rows = client.get("/config/dormant-rules", headers=VIEWER).json()["rules"]
    assert [r for r in rows if r["rule_id"] == rule_id][0]["confirmed"] == 1


def test_reproposing_resets_the_confirmation(client):
    rule_id = client.post("/config/dormant-rules", json=BODY,
                          headers=ENG).json()["rule_id"]
    client.post(f"/config/dormant-rules/{rule_id}/confirm", headers=SENIOR)
    client.post("/config/dormant-rules", json={**BODY, "fixed_qty": 5},
                headers=ENG)
    rows = client.get("/config/dormant-rules", headers=ENG).json()["rules"]
    row = [r for r in rows if r["rule_id"] == rule_id][0]
    assert row["confirmed"] == 0 and row["fixed_qty"] == 5


def test_fixed_qty_without_a_quantity_is_rejected_at_the_api(client):
    r = client.post("/config/dormant-rules",
                    json={**BODY, "fixed_qty": None}, headers=ENG)
    assert r.status_code == 422


def test_load_rules_reads_confirmed_only(client, db_file):
    from app.db import get_conn
    client.post("/config/dormant-rules", json=BODY, headers=ENG)
    conn = get_conn()
    try:
        assert DR.load_rules(conn, "alice") == []
    finally:
        conn.close()


def test_delete_needs_approval_rights(client):
    rule_id = client.post("/config/dormant-rules", json=BODY,
                          headers=ENG).json()["rule_id"]
    assert client.delete(f"/config/dormant-rules/{rule_id}",
                         headers=ENG).status_code == 403
    assert client.delete(f"/config/dormant-rules/{rule_id}",
                         headers=SENIOR).status_code == 200


def test_coverage_reports_both_match_rate_and_stock_value(client, synth_csv):
    from conftest import upload
    batch = upload(client, synth_csv).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch}", headers=ENG)
    body = client.get(f"/config/dormant-rules/coverage?batch_id={batch}",
                      headers=ENG).json()
    assert set(body) >= {"dormant_rows", "matched", "engine_book_usd",
                         "proposed_book_usd", "delta_usd"}
    assert body["matched"] == 0, "no confirmed rule yet"
    assert body["delta_usd"] == 0.0


# --- the chat path ---------------------------------------------------------
#
# Same table, same confirm gate, reached from /chat instead of the console form.
# The properties below are the ones the console does not need and the chat tool
# does: a sentence must not un-confirm a live rule, and a model must not author
# a quantity.

import json  # noqa: E402  -- kept beside the tests that use it

from conftest import upload  # noqa: E402


def scored_batch(client, csv_bytes):
    batch_id = upload(client, csv_bytes).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={batch_id}", headers=ENG)
    return batch_id


def dispatch(conn, batch_id, question, args, role="engineer", user="alice"):
    """Call propose_dormant_rule the way the loop does, so a ToolError comes
    back as the {"error": ...} the model would actually read."""
    from app.agent import tools as T

    ctx = T.ToolContext(conn=conn, actor={"user": user, "role": role},
                        batch_id=batch_id, question=question)
    return json.loads(T.dispatch(ctx, "propose_dormant_rule", args))


def rule_row(conn, scope="category", key="filter"):
    r = conn.execute("SELECT * FROM user_dormant_rule_config WHERE owner_user='alice' AND scope=? AND "
                     "match_key=?", (scope, key)).fetchone()
    return dict(r) if r is not None else None


def test_chat_proposal_is_unconfirmed_and_sizes_nothing(client, synth_csv):
    """The safety property, asserted the way test_load_rules_reads_confirmed_only
    asserts it: the row exists, and the engine cannot see it."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    r = client.post("/chat", json={"question": "keep all filter parts at 2",
                                   "batch_id": batch_id}, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "propose"

    conn = get_conn()
    try:
        row = rule_row(conn)
        assert row is not None, "the chat turn recorded nothing"
        assert row["confirmed"] == 0
        assert row["policy"] == "fixed_qty" and row["fixed_qty"] == 2
        assert row["set_by"] == "alice"
        assert DR.load_rules(conn, "alice") == [], "an unconfirmed rule reached the engine"
    finally:
        conn.close()


def test_chat_cannot_replace_a_confirmed_rule(client, synth_csv):
    """The one that matters. The upsert resets confirmed=0 on conflict, so a
    restated sentence would silently un-confirm a LIVE rule and change what the
    next engine run sizes -- with nobody approving it."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    rule_id = client.post("/config/dormant-rules", json=BODY,
                          headers=ENG).json()["rule_id"]
    assert client.post(f"/config/dormant-rules/{rule_id}/confirm",
                       headers=SENIOR).status_code == 200

    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "keep all filter parts at 3",
                       {"scope": "category", "match_key": "filter",
                        "policy": "fixed_qty", "fixed_qty": 3})
        assert "CONFIRMED" in out["error"]
        row = rule_row(conn)
        assert row["confirmed"] == 1, "a refused call still un-confirmed the rule"
        assert row["fixed_qty"] == 2, "a refused call still changed the quantity"
        assert len(DR.load_rules(conn, "alice")) == 1
    finally:
        conn.close()


def test_chat_replaces_a_confirmed_rule_only_when_told(client, synth_csv):
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    rule_id = client.post("/config/dormant-rules", json=BODY,
                          headers=ENG).json()["rule_id"]
    client.post(f"/config/dormant-rules/{rule_id}/confirm", headers=SENIOR)

    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "keep all filter parts at 3",
                       {"scope": "category", "match_key": "filter",
                        "policy": "fixed_qty", "fixed_qty": 3,
                        "replace": True})
        assert out.get("error") is None, out
        conn.commit()
        row = rule_row(conn)
        assert row["confirmed"] == 0 and row["fixed_qty"] == 3
        assert DR.load_rules(conn, "alice") == [], "the replacement went live unapproved"
    finally:
        conn.close()


def test_chat_refuses_a_quantity_the_engineer_did_not_state(client, synth_csv):
    """A fixed_qty rule sets Min/ROP/Max on every matching row. That is a stock
    level, and the model does not author those."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "keep the filter parts a bit higher",
                       {"scope": "category", "match_key": "filter",
                        "policy": "fixed_qty", "fixed_qty": 3})
        assert "does not appear in the engineer's message" in out["error"]
        assert rule_row(conn) is None
    finally:
        conn.close()


def test_chat_refuses_a_default_scope_rule(client, synth_csv):
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "keep all dormant parts at 5",
                       {"scope": "default", "match_key": "",
                        "policy": "fixed_qty", "fixed_qty": 5})
        assert "whole tail" in out["error"]
        assert rule_row(conn, "default", "") is None
    finally:
        conn.close()


def test_chat_refuses_an_unknown_category(client, synth_csv):
    """An unvalidated key writes a rule that matches nothing and looks live."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "keep all widget parts at 2",
                       {"scope": "category", "match_key": "widgets",
                        "policy": "fixed_qty", "fixed_qty": 2})
        assert "not a part category" in out["error"]
        assert "filter" in out["error"], "the refusal must list the real ones"
        assert rule_row(conn, "category", "widgets") is None
    finally:
        conn.close()


def test_chat_refuses_an_item_not_in_the_batch(client, synth_csv):
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "hold the current level for 999999",
                       {"scope": "item", "match_key": "999999",
                        "policy": "hold_current"})
        assert "999999" in out["error"]
        assert rule_row(conn, "item", "999999") is None
    finally:
        conn.close()


def test_chat_refuses_fixed_qty_without_a_quantity(client, synth_csv):
    """Mirrors test_fixed_qty_without_a_quantity_is_rejected_at_the_api, at the
    tool boundary instead of the API one."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "keep all filter parts at a fixed qty",
                       {"scope": "category", "match_key": "filter",
                        "policy": "fixed_qty"})
        assert "needs fixed_qty" in out["error"]
        assert rule_row(conn) is None
    finally:
        conn.close()


def test_chat_refuses_a_quantity_on_a_policy_that_takes_none(client, synth_csv):
    """Dropping it silently would record something the engineer did not ask for."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    conn = get_conn()
    try:
        out = dispatch(conn, batch_id,
                       "hold the current level for filter parts, 4",
                       {"scope": "category", "match_key": "filter",
                        "policy": "hold_current", "fixed_qty": 4})
        assert "takes no quantity" in out["error"]
        assert rule_row(conn) is None
    finally:
        conn.close()


def test_viewer_cannot_propose_a_dormant_rule(client, synth_csv):
    """Two gates: the propose branch is a WRITE_INTENT the classifier downgrades
    for a read-only role, and the tool refuses one directly."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)

    r = client.post("/chat", json={"question": "keep all filter parts at 2",
                                   "batch_id": batch_id}, headers=VIEWER)
    assert r.status_code == 200, r.text
    assert r.json()["intent"] == "lookup", "a viewer reached the propose branch"

    conn = get_conn()
    try:
        out = dispatch(conn, batch_id, "keep all filter parts at 2",
                       {"scope": "category", "match_key": "filter",
                        "policy": "fixed_qty", "fixed_qty": 2},
                       role="viewer", user="alice")
        assert "review role" in out["error"]
        assert rule_row(conn) is None
    finally:
        conn.close()


def test_a_proposed_rule_does_not_rescore_a_batch(client, synth_csv):
    """Rules bite at the next /run-recommendation. A batch already on screen
    keeps the numbers its reviewer saw."""
    from app.db import get_conn

    batch_id = scored_batch(client, synth_csv)
    columns = ("SELECT item_id, new_max, new_rop, new_min FROM "
               "recommendation_result WHERE batch_id=? ORDER BY item_id")
    conn = get_conn()
    try:
        before = [dict(r) for r in conn.execute(columns, (batch_id,))]
    finally:
        conn.close()

    client.post("/chat", json={"question": "keep all filter parts at 2",
                               "batch_id": batch_id}, headers=ENG)

    conn = get_conn()
    try:
        after = [dict(r) for r in conn.execute(columns, (batch_id,))]
    finally:
        conn.close()
    assert before == after
