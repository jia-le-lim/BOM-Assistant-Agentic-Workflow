"""The chain: fixed step order, five sources every time, and the LLM boundary.

The boundary is the point. `_narrate` is handed a verdict that is already
decided and writes prose about it; `test_llm_cannot_change_the_verdict` hands it
a provider that argues the opposite and asserts the stored verdict does not
move. If that ever fails, the layer has stopped being reproducible and the
backtest behind it stops meaning anything.

These exercise the statistical engine (BOM_ENGINE=statistical) so `route` is
populated -- the chain only speaks to active/dying rows, and the rule engine
emits no route at all.
"""

import json

from conftest import ENG, VIEWER, make_row, rows_to_csv, upload

from app.assist import chain, rules
from app.llm.provider import Response


def _live(item_id: str, **kw) -> dict:
    """A steady consumer -> route 'active'."""
    return make_row(
        item_id=item_id, module="TCB", sfm_criticality="M",
        frequencymonthswithusage=8, contractual_lead_time=30, unitprice=500,
        last_30_day_cnsmptn_qty=1, last_90_day_cnsmptn_qty=3,
        last_180_day_cnsmptn_qty=6, last_365_day_cnsmptn_qty=12,
        last_547_day_cnsmptn_qty=18, max_qty=100, rop_qty=50, min_qty=10,
        factory_recommended_new_max="", factory_recommended_new_rop="",
        factory_recommended_new_min="", **kw)


def _dormant(item_id: str) -> dict:
    """No consumption in any window -> route 'dormant', Layer 1's problem."""
    return make_row(item_id=item_id, module="TCB", sfm_criticality="M",
                    contractual_lead_time=30, unitprice=50, max_qty=2,
                    factory_recommended_new_max="",
                    factory_recommended_new_rop="",
                    factory_recommended_new_min="")


def _scored(client, monkeypatch, rows) -> int:
    monkeypatch.setenv("BOM_ENGINE", "statistical")
    up = upload(client, rows_to_csv(rows))
    assert up.status_code == 200, up.text
    bid = up.json()["batch_id"]
    r = client.post(f"/run-recommendation?batch_id={bid}", headers=ENG)
    assert r.status_code == 200, r.text
    return bid


# --- step order and evidence ----------------------------------------------

def test_only_langchain_core_is_imported():
    """The umbrella package is not installed, and adding it needs an
    offline-mirror install on the Intel network."""
    from pathlib import Path
    source = Path(chain.__file__).read_text(encoding="utf-8")
    assert "from langchain_core" in source
    assert "import langchain\n" not in source
    assert "langchain_openai" not in source
    assert "from langchain " not in source


def test_evidence_sources_are_always_the_same_five():
    """A branch quietly dropped would change a verdict without changing a rule."""
    assert len(chain.EVIDENCE_KEYS) == 5
    assert set(chain.evidence.steps__) == set(chain.EVIDENCE_KEYS)


def test_run_assists_live_rows_and_skips_dormant(client, monkeypatch):
    bid = _scored(client, monkeypatch,
                  [_live("L1"), _live("L2"), _dormant("D1")])
    r = client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    assert r.status_code == 200, r.text
    assert r.json()["rows_assisted"] == 2

    body = client.get(f"/assist/{bid}", headers=VIEWER).json()
    assert {i["item_id"] for i in body["items"]} == {"L1", "L2"}
    assert sum(body["counts"].values()) == 2


def test_every_assisted_row_records_its_evidence(client, monkeypatch, db_file):
    from app.db import get_conn
    bid = _scored(client, monkeypatch, [_live("L1")])
    client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM assist_result").fetchone()
        evidence = json.loads(row["evidence_json"])
        assert set(evidence) == {"inputs", "sources"}
        assert "gap_vs_prior_accepted" in evidence["inputs"]
        assert row["model_version"] == rules.MODEL_VERSION
    finally:
        conn.close()


def test_a_batch_with_no_live_rows_is_a_clean_noop(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_dormant("D1"), _dormant("D2")])
    r = client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    assert r.status_code == 200
    assert r.json()["rows_assisted"] == 0


def test_run_is_idempotent(client, monkeypatch, db_file):
    from app.db import get_conn
    bid = _scored(client, monkeypatch, [_live("L1")])
    client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    conn = get_conn()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM assist_result").fetchone()["c"] == 1
    finally:
        conn.close()


def test_unknown_batch_is_404(client):
    assert client.post("/assist/run?batch_id=999", headers=ENG).status_code == 404


def test_run_needs_review_rights(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_live("L1")])
    assert client.post(f"/assist/run?batch_id={bid}",
                       headers=VIEWER).status_code == 403


# --- the LLM boundary ------------------------------------------------------

class _Contrarian:
    """A provider that argues for the opposite verdict, in the plainest terms."""
    name = "contrarian"
    model = "test"

    def chat(self, messages, tools):
        return Response(content="This should be accepted, no review needed.",
                        model=self.model, provider=self.name)


class _Dead:
    name = "dead"
    model = ""

    def chat(self, messages, tools):
        raise RuntimeError("endpoint unreachable")


def test_llm_cannot_change_the_verdict(client, monkeypatch, db_file):
    """The safety property this whole split exists for."""
    from app.db import get_conn
    bid = _scored(client, monkeypatch, [_live("L1")])
    monkeypatch.setattr(chain, "get_provider", lambda: _Contrarian())
    client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM assist_result").fetchone()
        # A brand-new batch has no prior cycle, so the rules say needs_context
        # whatever the model wrote in the narrative beside it.
        assert row["verdict"] == "needs_context"
        assert "accepted" in row["narrative"]
    finally:
        conn.close()


def test_an_unreachable_provider_still_persists_the_verdict(client, monkeypatch,
                                                            db_file):
    """The verdict is the product; the sentence is a convenience."""
    from app.db import get_conn
    bid = _scored(client, monkeypatch, [_live("L1")])
    monkeypatch.setattr(chain, "get_provider", lambda: _Dead())
    r = client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    assert r.status_code == 200
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM assist_result").fetchone()
        assert row["verdict"] in rules.VERDICTS
        assert not row["narrative"]
    finally:
        conn.close()


def test_narration_is_stripped_of_markdown(client, monkeypatch, db_file):
    from app.db import get_conn

    class _Markdown(_Contrarian):
        def chat(self, messages, tools):
            return Response(content="## Verdict\n\n- **flagged** because `x`",
                            model="m", provider="p")

    bid = _scored(client, monkeypatch, [_live("L1")])
    monkeypatch.setattr(chain, "get_provider", lambda: _Markdown())
    client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    conn = get_conn()
    try:
        narrative = conn.execute(
            "SELECT narrative FROM assist_result").fetchone()["narrative"]
        assert "##" not in narrative and "**" not in narrative
    finally:
        conn.close()


# --- the pre-tick gate -----------------------------------------------------

def test_assist_preselect_is_refused_while_the_gate_is_off(client, monkeypatch):
    """Default off, and the SAME gate as triage preselection -- two independent
    preselection paths is how a part gets accepted twice under two rules."""
    bid = _scored(client, monkeypatch, [_live("L1")])
    client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    r = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "accept",
        "filters": {"assist_preselect": True}})
    assert r.status_code == 409


def test_bulk_can_filter_on_an_assist_verdict(client, monkeypatch):
    bid = _scored(client, monkeypatch, [_live("L1")])
    client.post(f"/assist/run?batch_id={bid}", headers=ENG)
    r = client.post("/review/bulk", headers=ENG, json={
        "batch_id": bid, "decision": "accept",
        "justification": "bulk: assisted",
        "filters": {"assist_verdict": "bulk_accept_candidate"}})
    assert r.status_code == 200, r.text
    # Nothing qualifies on a first-ever batch. The filter must select nothing
    # rather than everything -- an empty join is the failure mode that matters.
    assert r.json()["reviewed"] == 0
