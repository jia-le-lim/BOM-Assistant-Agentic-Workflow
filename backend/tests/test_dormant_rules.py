"""Dormant stocking rules: resolution order, the confirm gate, and the override.

Ordering is the contract, the same way it is for part_category: an item rule
must beat a category rule which must beat the default, or a per-part exception
could never be written without renumbering the table.

The property that matters most is the last one: an EMPTY rule table must
reproduce the engine's own answer exactly. Layer 1 is a stock-moving change
(owner decision Q2, 2026-09-01), so "no confirmed rule" has to mean "nothing
moved", not "something slightly different".
"""

import os

import pandas as pd
import pytest
from conftest import ENG, SENIOR, VIEWER

from app import dormant_rules as DR


def rule(scope="default", key="", crit="", policy="hold_current", qty=None,
         priority=500):
    return {"scope": scope, "match_key": key, "criticality": crit,
            "policy": policy, "fixed_qty": qty, "priority": priority}


# --- resolve ---------------------------------------------------------------

def test_item_beats_category_beats_default():
    rules = [rule(), rule("category", "filter", policy="fixed_qty", qty=2),
             rule("item", "100005", policy="fixed_qty", qty=9)]
    assert DR.resolve(rules, "100005", "filter", "m")["fixed_qty"] == 9
    assert DR.resolve(rules, "999999", "filter", "m")["fixed_qty"] == 2
    assert DR.resolve(rules, "999999", "cable", "m")["scope"] == "default"


def test_lower_priority_wins_before_scope():
    """Priority is the outer key: an explicit 10 outranks a specific scope at 500."""
    rules = [rule("category", "filter", policy="fixed_qty", qty=2, priority=10),
             rule("item", "100005", policy="fixed_qty", qty=9, priority=500)]
    assert DR.resolve(rules, "100005", "filter", "m")["fixed_qty"] == 2


def test_criticality_filter_excludes_a_non_matching_row():
    rules = [rule("category", "filter", crit="h", policy="fixed_qty", qty=4)]
    assert DR.resolve(rules, "100005", "filter", "h")["fixed_qty"] == 4
    assert DR.resolve(rules, "100005", "filter", "m") is None


def test_blank_criticality_matches_anything():
    rules = [rule("category", "filter", crit="", policy="fixed_qty", qty=4)]
    for crit in ("h", "m", "l", "", None):
        assert DR.resolve(rules, "100005", "filter", crit) is not None


def test_uncategorised_row_never_matches_a_category_rule():
    """'' is meaningful: an uncategorised part is not silently every category."""
    rules = [rule("category", "", policy="fixed_qty", qty=4)]
    assert DR.resolve(rules, "100005", "", "m") is None


def test_no_rules_resolves_to_none():
    assert DR.resolve([], "100005", "filter", "m") is None


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

BODY = {"scope": "category", "match_key": "filter", "criticality": "",
        "policy": "fixed_qty", "fixed_qty": 2, "priority": 100}


def test_propose_is_pending_and_confirm_needs_a_second_person(client):
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
        assert DR.load_rules(conn) == []
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
