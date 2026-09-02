import csv
import io
import json
import sqlite3

from conftest import (ADMIN, AUDITOR, ENG, SENIOR, VIEWER, make_row,
                      rows_to_csv, upload)


def scored_batch(client, synth_csv) -> int:
    b = upload(client, synth_csv).json()["batch_id"]
    r = client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    assert r.status_code == 200, r.text
    return b, r.json()


def test_run_summary_counts(client, synth_csv):
    _, s = scored_batch(client, synth_csv)
    assert s["rows_scored"] == 7
    assert s["review_required_Y"] == 3          # r2 (trap), r5 (high cost), r7 (R9 wide)
    assert s["review_required_N"] == 4
    assert "ZERO_RECOMMENDATION_OVERRIDE" in s["top_reason_codes"]
    assert s["rule_version"] == "0.2.0-tcb"


def test_no_leakage_from_poisoned_columns(client, synth_csv):
    """Fixture carries factory_recommended_new_max=999 on every row; if the
    engine could see it, outputs would echo it."""
    b, _ = scored_batch(client, synth_csv)
    r = client.get(f"/recommendations?batch_id={b}", headers=VIEWER).json()
    assert all(item["new_max"] != 999 for item in r["items"])


def test_status_derivation_and_filters(client, synth_csv):
    b, _ = scored_batch(client, synth_csv)
    pending = client.get(f"/recommendations?batch_id={b}&status=pending_review",
                         headers=VIEWER).json()
    cleared = client.get(f"/recommendations?batch_id={b}&status=auto_cleared",
                         headers=VIEWER).json()
    # pending = 3 review-Y rows + r8 (changed but unflagged, safety switch on)
    assert pending["total"] == 4
    assert {i["item_id"] for i in pending["items"]} == {"100002", "100005", "100007", "100008"}
    assert cleared["total"] == 3
    assert {i["item_id"] for i in cleared["items"]} == {"100001", "100006", "100010"}


def test_trap_item_detail(client, synth_csv):
    b, _ = scored_batch(client, synth_csv)
    d = client.get(f"/recommendations/100002?batch_id={b}", headers=VIEWER).json()
    assert "ZERO_RECOMMENDATION_OVERRIDE" in d["recommendation"]["reason_code"]
    assert d["recommendation"]["new_max"] >= 1      # protective: never zeroed
    assert d["status"] == "pending_review"


def test_full_review_and_export_flow(client, synth_csv):
    b, _ = scored_batch(client, synth_csv)

    # Viewer cannot review
    assert client.post(f"/review/100005?batch_id={b}",
                       json={"decision": "accept"}, headers=VIEWER).status_code == 403

    # r5: accept (risk High -> needs senior)
    r = client.post(f"/review/100005?batch_id={b}",
                    json={"decision": "accept", "comment": "ok"}, headers=ENG).json()
    assert r["requires_senior_approval"] is True
    assert r["status"] == "awaiting_senior"

    # Engineer cannot approve; approver cannot be the reviewer
    assert client.post(f"/review/100005/approve?batch_id={b}",
                       headers=ENG).status_code == 403
    same_user_senior = {"X-User": "alice", "X-Role": "senior"}
    assert client.post(f"/review/100005/approve?batch_id={b}",
                       headers=same_user_senior).status_code == 403
    assert client.post(f"/review/100005/approve?batch_id={b}",
                       headers=SENIOR).status_code == 200

    # r8: override (always needs senior); invalid ordering rejected first
    bad = client.post(f"/review/100008?batch_id={b}",
                      json={"decision": "override", "final_max": 1,
                            "final_rop": 2, "final_min": 0}, headers=ENG)
    assert bad.status_code == 422
    r = client.post(f"/review/100008?batch_id={b}",
                    json={"decision": "override", "final_max": 3, "final_rop": 1,
                          "final_min": 0, "justification": "constraint tool"},
                    headers=ENG).json()
    assert r["requires_senior_approval"] is True
    assert client.post(f"/review/100008/approve?batch_id={b}",
                       headers=SENIOR).status_code == 200

    # r2: accept -> engine says Maintain -> reviewed but nothing to export
    r = client.post(f"/review/100002?batch_id={b}",
                    json={"decision": "accept"}, headers=ENG).json()
    assert r["requires_senior_approval"] is False

    # r7 left pending -> export excludes it and reports it
    e = client.get(f"/export/wings?batch_id={b}", headers=ENG)
    assert e.status_code == 200
    assert e.headers["X-Pending-Review"] == "1"          # r7
    assert e.headers["X-Awaiting-Senior"] == "0"
    rows = list(csv.DictReader(io.StringIO(e.text)))
    assert e.headers["X-Rows-Exported"] == str(len(rows)) == "2"
    by_item = {r["item_id"]: r for r in rows}
    assert by_item["100005"]["current_max"] == "1"
    assert int(by_item["100005"]["new_max"]) >= 3        # engine value, senior-approved
    assert by_item["100008"]["current_max"] == "2"
    assert by_item["100008"]["new_max"] == "3"           # engineer override value
    assert by_item["100008"]["decision"] == "override"


def test_history_endpoint(client, synth_csv):
    b, _ = scored_batch(client, synth_csv)
    client.post(f"/review/100005?batch_id={b}", json={"decision": "accept"}, headers=ENG)
    h = client.get("/history/100005", headers=AUDITOR).json()
    assert len(h["reviews"]) == 1
    assert h["reviews"][0]["decision"] == "accept"
    assert h["reviews"][0]["rule_version"] == "0.2.0-tcb"


def test_justification_templates_include_definitions(client):
    templates = client.get(
        "/review/justification-templates", headers=VIEWER).json()["templates"]
    assert len(templates) == 10
    assert templates[0] == {
        "justification": "Follow SFM",
        "definition": "Select if you are adopting the SFM/BRR recommendation.",
    }
    assert all(template["justification"] and template["definition"]
               for template in templates)


def test_chat_read_only_tools(client, synth_csv):
    b, _ = scored_batch(client, synth_csv)
    why = client.post("/chat", json={"question": "why item 100007?"},
                      headers=VIEWER).json()
    assert "recommends 0" in why["answer"]
    assert why["sources"]

    client.post(f"/review/100005?batch_id={b}", json={"decision": "accept"}, headers=ENG)
    hist = client.post("/chat", json={"question": "history 100005"},
                       headers=VIEWER).json()
    assert "accept" in hist["answer"]

    top = client.post("/chat", json={"question": "top exposure items"},
                      headers=VIEWER).json()
    assert "100005" in top["answer"]

    unknown = client.post("/chat", json={"question": "what is the meaning of life"},
                          headers=VIEWER).json()
    assert "I don't know" in unknown["answer"]
    assert unknown["sources"] == []


def test_config_versioning(client, synth_csv, db_file):
    b, _ = scored_batch(client, synth_csv)
    cfg = client.get("/config/rules", headers=VIEWER).json()
    assert cfg["rule_version"] == "0.2.0-tcb"

    assert client.post("/config/rules", json={"rule_version": "x", "updates": {}},
                       headers=ENG).status_code == 403
    assert client.post("/config/rules",
                       json={"rule_version": "0.2.0-tcb",
                             "updates": {"long_lead_time_threshold": 45}},
                       headers=ADMIN).status_code == 400      # version must change
    assert client.post("/config/rules",
                       json={"rule_version": "0.2.1-test",
                             "updates": {"nonsense_key": 1}},
                       headers=ADMIN).status_code == 400
    r = client.post("/config/rules",
                    json={"rule_version": "0.2.1-test",
                          "updates": {"long_lead_time_threshold": 45}},
                    headers=ADMIN)
    assert r.status_code == 200

    # Re-scoring stamps the new version on results (determinism audit trail)
    s = client.post(f"/run-recommendation?batch_id={b}", headers=ENG).json()
    assert s["rule_version"] == "0.2.1-test"


def test_autoclear_knobs_are_editable_config(client):
    """The statistical-engine auto-clear knobs are versioned config: shown with
    their (off) defaults and writable by an admin as a new rule_version."""
    cfg = client.get("/config/rules", headers=VIEWER).json()["config"]
    assert "autoclear_immaterial_usd" in cfg          # default surfaced
    assert cfg["autoclear_reliable"] in (False, 0)
    r = client.post("/config/rules",
                    json={"rule_version": "ac-1",
                          "updates": {"autoclear_immaterial_usd": 150,
                                      "autoclear_reliable": True}},
                    headers=ADMIN)
    assert r.status_code == 200, r.text
    cfg2 = client.get("/config/rules", headers=VIEWER).json()["config"]
    assert cfg2["autoclear_immaterial_usd"] == 150
    assert cfg2["autoclear_reliable"] is True


def test_criticality_two_person_rule(client, synth_csv):
    scored_batch(client, synth_csv)
    r = client.post("/config/criticality",
                    json={"pattern": "KnS TCX3", "criticality": "High",
                          "service_level_target": 0.98}, headers=ENG)
    assert r.status_code == 200 and r.json()["confirmed"] is False

    # Not yet confirmed -> engine must not see it
    cfg = client.get("/config/rules", headers=VIEWER).json()
    assert "KnS TCX3" not in cfg["config"].get("machine_criticality", {})

    same_user = {"X-User": "alice", "X-Role": "senior"}
    assert client.post("/config/criticality/KnS TCX3/confirm",
                       headers=same_user).status_code == 403   # proposer == confirmer
    assert client.post("/config/criticality/KnS TCX3/confirm",
                       headers=SENIOR).status_code == 200
    cfg = client.get("/config/rules", headers=VIEWER).json()
    assert cfg["config"]["machine_criticality"]["KnS TCX3"] == "High"


def test_rescore_survives_an_existing_review(client, synth_csv):
    """review_history -> recommendation_result is ON DELETE RESTRICT.

    Re-scoring after a rule-config change used to DELETE the whole batch's
    results and hit that constraint -> IntegrityError -> HTTP 500. Reviewed rows
    are now kept on the recommendation their reviewer actually saw.
    """
    b, _ = scored_batch(client, synth_csv)
    assert client.post(f"/review/100005?batch_id={b}",
                       json={"decision": "accept"},
                       headers=ENG).status_code == 200

    r = client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["rows_preserved"] == 1

    d = client.get(f"/recommendations/100005?batch_id={b}", headers=ENG)
    assert d.status_code == 200
    assert d.json()["status"] == "awaiting_senior"      # High risk -> senior


def test_chat_rejects_a_batch_that_does_not_exist(client, synth_csv):
    """conversation_turn.batch_id is a real FK: an invented id is a bad
    request, not a 500 from the insert."""
    scored_batch(client, synth_csv)
    r = client.post("/chat", json={"question": "why item 100002",
                                   "batch_id": 99999}, headers=ENG)
    assert r.status_code == 404


def test_item_in_two_stockrooms_is_never_guessed(client):
    """The PK is (batch_id, item_id, stockroom_id). Lookups that dropped the
    stockroom picked an arbitrary row and stranded the other one forever."""
    csv_bytes = rows_to_csv([
        make_row(item_id=100020, stockroom_id="24", max_qty=2, rop_qty=1),
        make_row(item_id=100020, stockroom_id="31", max_qty=5, rop_qty=2),
    ])
    b = upload(client, csv_bytes).json()["batch_id"]
    assert client.post(f"/run-recommendation?batch_id={b}",
                       headers=ENG).status_code == 200

    assert client.get(f"/recommendations/100020?batch_id={b}",
                      headers=ENG).status_code == 409
    r = client.get(f"/recommendations/100020?batch_id={b}&stockroom_id=31",
                   headers=ENG)
    assert r.status_code == 200
    assert r.json()["recommendation"]["stockroom_id"] == "31"

    assert client.post(f"/review/100020?batch_id={b}",
                       json={"decision": "accept"},
                       headers=ENG).status_code == 409
    for stk in ("24", "31"):
        rv = client.post(f"/review/100020?batch_id={b}&stockroom_id={stk}",
                         json={"decision": "accept"}, headers=ENG)
        assert rv.status_code == 200, rv.text
        assert rv.json()["stockroom_id"] == stk


def test_confirmed_criticality_is_not_frozen_into_the_stored_config(
        client, synth_csv, db_file):
    """active_config() merges machine_criticality_config for the engine.
    Persisting that merged view would bake a snapshot of a table an admin can
    still edit into the rule_version stamp that is supposed to be immutable."""
    scored_batch(client, synth_csv)
    client.post("/config/criticality",
                json={"pattern": "ZZZ Test Machine", "criticality": "High",
                      "service_level_target": 0.98}, headers=ENG)
    assert client.post("/config/criticality/ZZZ Test Machine/confirm",
                       headers=SENIOR).status_code == 200

    assert client.post("/config/rules",
                       json={"rule_version": "0.2.1-test",
                             "updates": {"long_lead_time_threshold": 45}},
                       headers=ADMIN).status_code == 200

    conn = sqlite3.connect(db_file)
    stored = json.loads(conn.execute(
        "SELECT config_json FROM rule_config WHERE active=1").fetchone()[0])
    conn.close()
    assert stored["long_lead_time_threshold"] == 45
    assert "ZZZ Test Machine" not in stored.get("machine_criticality", {})

    # The engine still sees it -- through the merge, from the live table.
    cfg = client.get("/config/rules", headers=VIEWER).json()["config"]
    assert cfg["machine_criticality"]["ZZZ Test Machine"] == "High"


def test_building_the_app_does_not_touch_the_database(db_file):
    """init_db() belongs in the lifespan handler. At module scope it fired on
    any `import app.main` -- against real Supabase on a machine with a .env."""
    from app.main import create_app

    create_app()
    assert not db_file.exists(), "create_app() issued DDL before startup"


def test_everything_is_audited(client, synth_csv, db_file):
    b, _ = scored_batch(client, synth_csv)
    client.post(f"/review/100005?batch_id={b}", json={"decision": "accept"}, headers=ENG)
    client.post("/chat", json={"question": "why item 100002"}, headers=VIEWER)
    client.get(f"/export/wings?batch_id={b}", headers=ENG)

    conn = sqlite3.connect(db_file)
    entities = {r[0] for r in conn.execute("SELECT DISTINCT entity FROM audit_log")}
    users = {r[0] for r in conn.execute("SELECT DISTINCT user FROM audit_log")}
    conn.close()
    assert {"batch", "review", "chat"} <= entities
    assert "alice" in users


def test_queue_search_by_item_id(client, synth_csv):
    b, _ = scored_batch(client, synth_csv)
    hit = client.get(f"/recommendations?batch_id={b}&q=00005", headers=VIEWER).json()
    assert {i["item_id"] for i in hit["items"]} == {"100005"}
    assert hit["total"] == 1
    # Search composes with the other filters rather than replacing them.
    narrowed = client.get(f"/recommendations?batch_id={b}&q=00005&status=auto_cleared",
                          headers=VIEWER).json()
    assert narrowed["total"] == 0
    # Case-insensitive, and blank means "no search" rather than "match nothing".
    assert client.get(f"/recommendations?batch_id={b}&q=%20", headers=VIEWER).json()["total"] == 7
    assert client.get(f"/recommendations?batch_id={b}&q=nosuchpart",
                      headers=VIEWER).json()["total"] == 0


def test_queue_rows_carry_the_display_fields(client, synth_csv):
    """The queue shows description, category and the current Wings settings
    beside the engine's proposal. None of them lives in recommendation_result,
    so a dropped join would silently blank a column rather than error."""
    b, _ = scored_batch(client, synth_csv)
    items = client.get(f"/recommendations?batch_id={b}", headers=VIEWER).json()["items"]
    assert items and all(
        {"item_desc", "part_category", "current_max", "current_rop",
         "bench_max", "bench_rop"} <= set(i) for i in items)
    assert any(i["item_desc"] for i in items)
    # part_category is written by the similarity run, so it is "" until then --
    # blank, never missing.
    assert all(i["part_category"] == "" for i in items)


def test_queue_shows_the_part_category_once_peers_are_built(client):
    """part_category is written by the similarity run -- which now rides on the
    assist button -- so the column fills in only after peers exist."""
    csv_text = rows_to_csv([make_row(item_id="700001", item_desc="FLOW SENSOR ARM",
                                     max_qty="12", rop_qty="4")])
    b = upload(client, csv_text).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    before = client.get(f"/recommendations?batch_id={b}", headers=VIEWER).json()["items"][0]
    assert before["part_category"] == ""
    assert (before["current_max"], before["current_rop"]) == (12, 4)
    client.post("/similarity/run", headers=ENG, json={"batch_id": b})
    after = client.get(f"/recommendations?batch_id={b}", headers=VIEWER).json()["items"][0]
    assert after["part_category"] == "sensor"
