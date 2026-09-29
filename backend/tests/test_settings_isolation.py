"""Exercise account isolation through APIs, engine runs, and chat tools."""

import json
from urllib.parse import quote

import pytest

from conftest import make_row, rows_to_csv, upload

ALICE = {"X-User": "alice", "X-Role": "admin"}
BOB = {"X-User": "bob", "X-Role": "admin"}


def config(client, actor):
    response = client.get("/config/rules", headers=actor)
    assert response.status_code == 200, response.text
    return response.json()["config"]


def save(client, actor, version, **updates):
    response = client.post("/config/rules", headers=actor,
                           json={"rule_version": version, "updates": updates})
    assert response.status_code == 200, response.text


def propose(client, actor, qty):
    response = client.post("/config/dormant-rules", headers=actor, json={
        "scope": "item", "match_key": "PART-1", "policy": "fixed_qty", "fixed_qty": qty,
    })
    assert response.status_code == 200, response.text
    return response.json()["rule_id"]


def test_rule_versions_and_autoclear_are_private_even_for_admins(client):
    baseline = config(client, BOB)
    save(client, ALICE, "personal-v1", autoclear_reliable=True,
         autoclear_immaterial_usd=123, low_cost_threshold=17,
         triage_guarded_assist_enabled=True)
    assert config(client, ALICE)["autoclear_immaterial_usd"] == 123
    assert config(client, BOB) == baseline
    # A new account also starts from the original baseline, never Alice's edits.
    assert config(client, {**BOB, "X-User": "charlie"}) == baseline
    save(client, BOB, "personal-v1", autoclear_immaterial_usd=456)
    assert config(client, ALICE)["autoclear_immaterial_usd"] == 123
    assert config(client, BOB)["autoclear_immaterial_usd"] == 456
    # Browser-controlled owner arguments do not select somebody else's settings.
    assert client.get("/config/rules?owner_user=alice", headers=BOB).json()["config"][
        "autoclear_immaterial_usd"] == 456


def test_dormant_rules_cannot_be_seen_confirmed_or_deleted_by_another_owner(client):
    alice_id = propose(client, ALICE, 2)
    bob_id = propose(client, BOB, 7)
    assert alice_id != bob_id
    for owner, other, own_id, foreign_id, qty in (
        (ALICE, BOB, alice_id, bob_id, 2), (BOB, ALICE, bob_id, alice_id, 7),
    ):
        listed = client.get("/config/dormant-rules", headers=owner).json()["rules"]
        assert [(r["rule_id"], r["fixed_qty"]) for r in listed] == [(own_id, qty)]
        assert client.post(f"/config/dormant-rules/{foreign_id}/confirm",
                           headers=owner).status_code == 404
        assert client.delete(f"/config/dormant-rules/{foreign_id}",
                             headers=owner).status_code == 404
        assert client.post(f"/config/dormant-rules/{own_id}/confirm",
                           headers=owner).status_code == 200
    assert client.delete(f"/config/dormant-rules/{alice_id}", headers=ALICE).status_code == 200
    assert client.get("/config/dormant-rules", headers=ALICE).json()["rules"] == []
    assert client.get("/config/dormant-rules", headers=BOB).json()["confirmed"] == 1


@pytest.mark.parametrize("endpoint,body,field,alice_value,bob_value", [
    ("criticality", {"pattern": "PRIVATE-MACHINE"}, "criticality", "High", "Low"),
    ("part-categories", {"pattern": "PRIVATE-PART"}, "category", "sensor", "valve"),
])
def test_other_setting_sections_are_private(client, endpoint, body, field, alice_value, bob_value):
    path = f"/config/{endpoint}"
    assert client.post(path, headers=ALICE, json={**body, field: alice_value}).status_code == 200
    confirm = f"{path}/{quote(body['pattern'], safe='')}/confirm"
    assert client.post(confirm, headers=BOB).status_code == 404
    assert client.post(confirm, headers=ALICE).status_code == 200
    assert client.post(path, headers=BOB, json={**body, field: bob_value}).status_code == 200
    assert client.post(confirm, headers=BOB).status_code == 200
    for actor, expected in ((ALICE, alice_value), (BOB, bob_value)):
        if endpoint == "criticality":
            assert config(client, actor)["machine_criticality"][body["pattern"]] == expected
        else:
            rows = client.get(path, headers=actor).json()["rules"]
            assert next(r for r in rows if r["pattern"] == body["pattern"])[field] == expected


def test_engine_and_chat_use_the_workspace_owners_settings(client, monkeypatch):
    from app.agent.tools import ToolContext, dispatch
    from app.db import get_conn

    monkeypatch.setenv("BOM_ENGINE", "statistical")
    csv = rows_to_csv([make_row(item_id="PART-1", item_desc="FILTER", max_qty="4",
                              aging_status="Dormant", days_since_last_issue="900")])
    for actor, qty in ((ALICE, 2), (BOB, 7)):
        save(client, actor, f"private-{qty}", autoclear_immaterial_usd=qty)
        rule_id = propose(client, actor, qty)
        assert client.post(f"/config/dormant-rules/{rule_id}/confirm", headers=actor).status_code == 200
        response = upload(client, csv, headers=actor)
        assert response.status_code == 200, response.text
        batch = response.json()["batch_id"]
        result = client.post(f"/run-recommendation?batch_id={batch}", headers=actor)
        assert result.status_code == 200, result.text
        assert result.json()["rule_version"] == f"private-{qty}"
        detail = client.get(f"/recommendations/PART-1?batch_id={batch}", headers=actor).json()
        assert detail["recommendation"]["new_max"] == qty
        conn = get_conn()
        try:
            ctx = ToolContext(conn=conn, actor={"user": actor["X-User"], "role": "admin"},
                              batch_id=batch, question="show my rules")
            rules = json.loads(dispatch(ctx, "explain_rules", {}))
            assert rules["config"]["autoclear_immaterial_usd"] == qty
        finally:
            conn.close()


def test_existing_shared_settings_copy_once_and_survive_restart(client):
    from app.db import get_conn, init_db

    conn = get_conn()
    try:
        # Simulate settings that existed before upgrading the app.
        conn.execute("INSERT INTO dormant_rule_config "
                     "(scope, match_key, policy, fixed_qty, set_by, confirmed) "
                     "VALUES ('item','OLD-PART','fixed_qty',3,'legacy',1)")
        conn.commit()
    finally:
        conn.close()
    for actor in (ALICE, BOB):
        rules = client.get("/config/dormant-rules", headers=actor).json()["rules"]
        assert len(rules) == 1 and rules[0]["fixed_qty"] == 3 and rules[0]["confirmed"] == 1
        if actor == ALICE:
            assert client.delete(f"/config/dormant-rules/{rules[0]['rule_id']}",
                                 headers=actor).status_code == 200
    save(client, ALICE, "saved-personal", autoclear_immaterial_usd=99)
    init_db()
    assert client.get("/config/dormant-rules", headers=ALICE).json()["rules"] == []
    assert client.get("/config/dormant-rules", headers=BOB).json()["confirmed"] == 1
    assert config(client, ALICE)["autoclear_immaterial_usd"] == 99
    assert config(client, BOB)["rule_version"] != "saved-personal"


def test_deleted_seed_is_not_recreated(client):
    first = client.get("/config/dormant-rules", headers=ALICE).json()["rules"]
    assert len(first) == 1 and not first[0]["confirmed"]
    assert client.delete(f"/config/dormant-rules/{first[0]['rule_id']}", headers=ALICE).status_code == 200
    assert client.get("/config/dormant-rules", headers=ALICE).json()["rules"] == []
    assert len(client.get("/config/dormant-rules", headers=BOB).json()["rules"]) == 1


def test_chat_proposals_do_not_replace_another_accounts_rule(client):
    from app.agent.tools import ToolContext, dispatch
    from app.db import get_conn

    alice_id = client.post("/config/dormant-rules", headers=ALICE, json={
        "scope": "category", "match_key": "filter", "policy": "fixed_qty", "fixed_qty": 2,
    }).json()["rule_id"]
    assert client.post(f"/config/dormant-rules/{alice_id}/confirm", headers=ALICE).status_code == 200
    conn = get_conn()
    try:
        ctx = ToolContext(conn=conn, actor={"user": "bob", "role": "admin"},
                          batch_id=None, question="keep filter parts at 7")
        result = json.loads(dispatch(ctx, "propose_dormant_rule", {
            "scope": "category", "match_key": "filter", "policy": "fixed_qty", "fixed_qty": 7,
        }))
        assert "error" not in result
        conn.commit()
    finally:
        conn.close()
    alice = client.get("/config/dormant-rules", headers=ALICE).json()["rules"][0]
    bob = client.get("/config/dormant-rules", headers=BOB).json()["rules"][0]
    assert alice["confirmed"] == 1 and alice["fixed_qty"] == 2
    assert bob["confirmed"] == 0 and bob["fixed_qty"] == 7


def test_deleting_a_rule_before_first_list_does_not_add_a_default(client):
    rule_id = propose(client, ALICE, 2)
    assert client.delete(f"/config/dormant-rules/{rule_id}", headers=ALICE).status_code == 200
    assert client.get("/config/dormant-rules", headers=ALICE).json()["rules"] == []
