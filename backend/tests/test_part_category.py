"""The part-category lexicon: precedence, abbreviations, and the confirm gate.

Ordering is the contract here, the same way it is for echo.py's intent routing:
generic rules last, or "SENSOR BRACKET ASSY" resolves to a bracket.
"""

import re

from conftest import ENG, OWNER_SENIOR as SENIOR, OWNER_VIEWER as VIEWER, upload

from app import part_category as PC


def _rules():
    """The seeded lexicon, compiled the way load_rules() compiles it."""
    return [(pri, cat, re.compile(pat, re.IGNORECASE))
            for pri, cat, pat in PC.DEFAULT_RULES]


# ---------------------------------------------------------------------------
# lexicon
# ---------------------------------------------------------------------------

def test_default_rules_all_compile():
    for _pri, _cat, pattern in PC.DEFAULT_RULES:
        re.compile(pattern)


def test_priorities_are_unique_and_ordered():
    priorities = [pri for pri, _c, _p in PC.DEFAULT_RULES]
    assert priorities == sorted(priorities)
    assert len(set(priorities)) == len(priorities)


def test_patterns_are_within_the_length_cap():
    for _pri, _cat, pattern in PC.DEFAULT_RULES:
        assert len(pattern) <= PC.MAX_PATTERN_LEN


def test_specific_beats_generic():
    """The reason holder/assembly sit last: their keywords appear inside
    descriptions of far more specific parts."""
    rules = _rules()
    assert PC.categorise("SENSOR BRACKET ASSY", rules) == "sensor"
    assert PC.categorise("CABLE MOUNT PLATE", rules) == "cable"
    assert PC.categorise("VACUUM SOLENOID VALVE CABLE", rules) == "valve"


def test_abbreviations_match():
    """SNR and SOL alone are worth ~7pp of coverage on the real corpus."""
    rules = _rules()
    assert PC.categorise("CARRIAGE AB FLOW SNR(L)", rules) == "sensor"
    assert PC.categorise("HT WC COL SOL(L)", rules) == "valve"
    assert PC.categorise("POST HEAT STATION VAC SNR 1(L)", rules) == "sensor"


def test_no_match_returns_uncategorised():
    rules = _rules()
    assert PC.categorise("ZZZQQQ WIDGET", rules) == PC.UNCATEGORISED


def test_empty_description_is_uncategorised():
    rules = _rules()
    assert PC.categorise("", rules) == PC.UNCATEGORISED
    assert PC.categorise(None, rules) == PC.UNCATEGORISED
    assert PC.categorise("   ", rules) == PC.UNCATEGORISED


def test_case_insensitive():
    rules = _rules()
    assert PC.categorise("cable assy", rules) == "cable"
    assert PC.categorise("Timing Belt 516MXL3.2", rules) == "belt"


def test_word_boundaries_prevent_substring_matches():
    r"""\b anchors stop SET matching OFFSET."""
    rules = _rules()
    assert PC.categorise("OFFSET CALIBRATION WIDGET", rules) != "assembly"


def test_categories_lists_in_priority_order():
    cats = PC.categories(_rules())
    assert cats[0] == "sensor"
    assert cats[-1] == "assembly"
    assert len(cats) == len(set(cats))


# ---------------------------------------------------------------------------
# database-backed rule loading
# ---------------------------------------------------------------------------

def test_seed_populates_confirmed_rules_on_first_init(client, db_file):
    from app.db import get_conn
    conn = get_conn()
    try:
        rules, broken = PC.load_rules(conn, "alice")
        assert len(rules) == len(PC.DEFAULT_RULES)
        assert broken == []
        n = conn.execute("SELECT COUNT(*) c FROM user_part_category_config "
                         "WHERE confirmed=1").fetchone()["c"]
        assert n == len(PC.DEFAULT_RULES)
    finally:
        conn.close()


def test_only_confirmed_rules_are_loaded(client, db_file):
    """The safety property: a proposal must not move a number."""
    from app.db import get_conn
    conn = get_conn()
    try:
        before = len(PC.load_rules(conn, "alice")[0])
        conn.execute(
            "INSERT INTO user_part_category_config (owner_user, pattern, category, priority, "
            "set_by, confirmed) VALUES ('alice', 'WIDGETRON', 'widget', 5, 'alice', 0)")
        conn.commit()
        rules, _ = PC.load_rules(conn, "alice")
        assert len(rules) == before
        assert PC.categorise("WIDGETRON 9000", rules) == PC.UNCATEGORISED
    finally:
        conn.close()


def test_invalid_stored_regex_is_skipped_not_raised(client, db_file):
    """One bad engineer-entered rule must not take down the whole batch."""
    from app.db import get_conn
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_part_category_config (owner_user, pattern, category, priority, "
            "set_by, confirmed, confirmed_by) VALUES ('alice', '(', 'broken', 1, "
            "'alice', 1, 'boss')")
        conn.commit()
        rules, broken = PC.load_rules(conn, "alice")
        assert broken == ["("]
        assert PC.categorise("CABLE ASSY", rules) == "cable"   # others still work
    finally:
        conn.close()


def test_overlong_pattern_is_skipped(client, db_file):
    from app.db import get_conn
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_part_category_config (owner_user, pattern, category, priority, "
            "set_by, confirmed, confirmed_by) VALUES ('alice', ?, 'huge', 1, 'a', 1, 'b')",
            ("X" * (PC.MAX_PATTERN_LEN + 1),))
        conn.commit()
        _rules_out, broken = PC.load_rules(conn, "alice")
        assert len(broken) == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

def test_list_returns_seeded_rules(client):
    r = client.get("/config/part-categories", headers=ENG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["confirmed"] == len(PC.DEFAULT_RULES)
    assert body["pending"] == 0


def test_propose_then_confirm(client):
    rule = {"pattern": r"\bWIDGETRON\b", "category": "widget", "priority": 5}
    r = client.post("/config/part-categories", json=rule, headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["confirmed"] is False

    # the owner still needs approval rights
    same = client.post(
        f"/config/part-categories/{rule['pattern']}/confirm", headers=ENG)
    assert same.status_code == 403

    ok = client.post(
        f"/config/part-categories/{rule['pattern']}/confirm", headers=SENIOR)
    assert ok.status_code == 200, ok.text
    assert ok.json()["confirmed"] is True


def test_reproposing_resets_confirmation(client):
    rule = {"pattern": r"\bGIZMO\b", "category": "gizmo", "priority": 6}
    client.post("/config/part-categories", json=rule, headers=ENG)
    client.post(f"/config/part-categories/{rule['pattern']}/confirm",
                headers=SENIOR)
    client.post("/config/part-categories",
                json={**rule, "category": "gadget"}, headers=ENG)
    rows = client.get("/config/part-categories", headers=ENG).json()["rules"]
    row = next(r for r in rows if r["pattern"] == rule["pattern"])
    assert row["category"] == "gadget"
    assert not row["confirmed"], "an edited rule must lose its approval"
    assert row["confirmed_by"] is None


def test_invalid_regex_is_rejected_at_the_api(client):
    r = client.post("/config/part-categories",
                    json={"pattern": "(", "category": "broken"}, headers=ENG)
    assert r.status_code == 422


def test_confirm_unknown_pattern_is_404(client):
    assert client.post("/config/part-categories/NOSUCH/confirm",
                       headers=SENIOR).status_code == 404


def test_viewer_cannot_propose(client):
    assert client.post("/config/part-categories",
                       json={"pattern": r"\bX\b", "category": "x"},
                       headers=VIEWER).status_code == 403


def test_coverage_reports_pct_and_samples(client, synth_csv):
    batch_id = upload(client, synth_csv).json()["batch_id"]
    r = client.get(f"/config/part-categories/coverage?batch_id={batch_id}",
                   headers=ENG)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] > 0
    assert body["categorised"] + body["uncategorised"] == body["total"]
    assert 0 <= body["pct"] <= 100
    assert isinstance(body["uncategorised_samples"], list)


def test_coverage_unknown_batch_is_404(client):
    assert client.get("/config/part-categories/coverage?batch_id=99999",
                      headers=ENG).status_code == 404
