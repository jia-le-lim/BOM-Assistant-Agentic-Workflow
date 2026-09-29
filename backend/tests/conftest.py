import csv
import io
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Establish the offline test boundary here, before any `app.*` import, not in a
# fixture. app.config runs _load_dotenv() at import time -- which is collection
# time (test_schema_parity imports app.db), earlier than any fixture -- so the
# ignored real-runtime backend/.env would put DATABASE_URL back and point the
# suite at the real Supabase project. _load_dotenv() skips the file entirely
# when BOM_ALLOW_SQLITE is set; the empty values below are the second line of
# defence, since the reader only fills keys absent from the environment.
os.environ["BOM_ALLOW_SQLITE"] = "1"
os.environ["DATABASE_URL"] = ""
os.environ["LLM_BASE_URL"] = ""
os.environ["MEM0_ENABLED"] = "0"
os.environ["BOM_WORKSPACE_READ_ALL_USERS"] = ""
# The REST transport is a SECOND route to the real project, and it needs no
# DATABASE_URL: config.use_rest() switches on these alone, so clearing
# DATABASE_URL is no longer enough to keep the suite offline.
for _k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY"):
    os.environ[_k] = ""
# The workflow suite was written against the deterministic rule engine; pin it
# there. The statistical engine (production default) has its own test file.
os.environ["BOM_ENGINE"] = "rules"

from fastapi.testclient import TestClient  # noqa: E402

ENG = {"X-User": "alice", "X-Role": "engineer"}
SENIOR = {"X-User": "boss", "X-Role": "senior"}
ADMIN = {"X-User": "root", "X-Role": "admin"}
VIEWER = {"X-User": "eve", "X-Role": "viewer"}
# Same owner, read-only capability; VIEWER above remains a different user.
OWNER_VIEWER = {**ENG, "X-Role": "viewer"}
OWNER_ADMIN = {**ENG, "X-Role": "admin"}
OWNER_SENIOR = {**ENG, "X-Role": "senior"}
AUDITOR = {"X-User": "aud", "X-Role": "auditor"}


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    """SQLite is the TEST backend only.

    The application itself runs on Supabase Postgres and refuses to start
    without DATABASE_URL (config.require_database). Tests opt out explicitly
    via BOM_ALLOW_SQLITE so the suite stays offline and needs no secrets --
    every connection on this network must traverse an HTTP proxy, so a
    network-dependent suite would be unrunnable here.

    DATABASE_URL is cleared as well: a developer with backend/.env populated
    would otherwise have their real Supabase project silently used as the test
    database.
    """
    p = tmp_path / "test.db"
    monkeypatch.setenv("BOM_ALLOW_SQLITE", "1")
    # Keep an explicit empty value so config._load_dotenv() cannot repopulate
    # the real/placeholder DATABASE_URL from the ignored backend/.env.
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("BOM_DB_PATH", str(p))
    # A developer may have the ignored backend/.env configured for the real
    # LLM + mem0. Tests must remain offline and must never touch either service.
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("MEM0_ENABLED", "0")
    # Same reason, for the transport that needs no DATABASE_URL at all: with
    # these set, get_conn() returns a RestConn and every test writes to the real
    # Supabase project over HTTPS.
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("BOM_ENGINE", "rules")
    return p


@pytest.fixture()
def client(db_file):
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def make_row(**kw) -> dict:
    base = {
        "item_id": "", "item_desc": "TEST PART", "stockroom_id": "24", "module": "TCB",
        "machine_type": "ASM-Pacific,Phoenix", "aging_status": "Active Moving",
        "replenishment_policy": "Order to Demand", "partfreq": "",
        "sfm_recommendation": "Maintain Algo", "sfm_criticality": "",
        "max_qty": "0", "rop_qty": "0", "min_qty": "0", "unitprice": "10",
        "avail_qty": "0", "contractual_lead_time": "10", "open_po_qty": "0",
        "order_qty_multiple": "",
        "last_5_day_cnsmptn_qty": "0", "last_30_day_cnsmptn_qty": "0",
        "last_90_day_cnsmptn_qty": "0", "last_180_day_cnsmptn_qty": "0",
        "last_365_day_cnsmptn_qty": "0", "last_547_day_cnsmptn_qty": "0",
        "days_since_last_issue": "", "frequencymonthswithusage": "",
        "recom_max": "0", "recom_rop": "0", "recom_min": "0",
        "atm_recommended_max": "0", "atm_recommended_rop": "0", "atm_recommended_min": "0",
        "sfm_brr_max": "0", "sfm_brr_rop": "0", "sfm_brr_min": "0",
        "sfm_max": "", "sfm_rop": "", "sfm_min": "",
        "excess_status_tf": "No Excess Sharing Available",
        "new_modulle": "Module-TCB", "senstivity_tag": "",
        # PRD 5.1 output/memory columns, poisoned with sentinel values: if any of
        # these leak into the engine, tests fail loudly.
        "justification": "LEAK_IF_USED", "comments": "LEAK_IF_USED",
        "factory_recommended_new_max": "999", "factory_recommended_new_rop": "999",
        "factory_recommended_new_min": "999",
        "max_adoption": "LEAK", "rop_adoption": "LEAK", "ooq_adoption": "",
        "review_acknowledge": "Y", "modified_user": "x", "modified_date": "x",
    }
    base.update({k: str(v) for k, v in kw.items()})
    return base


def rows_to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


@pytest.fixture()
def synth_csv() -> bytes:
    rows = [
        # r1: clean maintain -> auto_cleared
        make_row(item_id=100001, max_qty=2, rop_qty=1, recom_max=2, recom_rop=1,
                 atm_recommended_max=2, atm_recommended_rop=1,
                 sfm_brr_max=2, sfm_brr_rop=1),
        # r2: the recom=0 trap -- stocked today, another source wants stock
        make_row(item_id=100002, max_qty=2, rop_qty=1, unitprice=200,
                 contractual_lead_time=30, aging_status="Dead",
                 recom_max=0, recom_rop=0, atm_recommended_max=2,
                 atm_recommended_rop=1, sfm_brr_max=2, sfm_brr_rop=1, avail_qty=1),
        # r3: negative consumption -> quarantine
        make_row(item_id=100003, last_30_day_cnsmptn_qty=-5),
        # r4: duplicate key (twice) -> quarantine both
        make_row(item_id=100004),
        make_row(item_id=100004),
        # r5: high-cost increase with real usage -> review Y, risk High
        make_row(item_id=100005, max_qty=1, unitprice=5000, contractual_lead_time=30,
                 last_90_day_cnsmptn_qty=1, last_180_day_cnsmptn_qty=1,
                 last_365_day_cnsmptn_qty=2, last_547_day_cnsmptn_qty=2,
                 recom_max=3, recom_rop=2, recom_min=1,
                 atm_recommended_max=3, atm_recommended_rop=2, atm_recommended_min=1,
                 sfm_brr_max=3, sfm_brr_rop=2, sfm_brr_min=1,
                 days_since_last_issue=30, frequencymonthswithusage=2),
        # r6: casing normalization check; dead + short LT + all-zero -> auto_cleared
        make_row(item_id=100006, sfm_recommendation="maintain algo",
                 aging_status="Dead", unitprice=5),
        # r7: dormant, recom=0, 90-day lead time -> R9 (wide) -> review Y
        make_row(item_id=100007, unitprice=300, contractual_lead_time=90,
                 aging_status="Dead"),
        # r8: engine proposes a change (2 -> 3, under the 50% threshold) but no
        # rule forces review -> pending via REQUIRE_REVIEW_FOR_ALL_CHANGES
        make_row(item_id=100008, max_qty=2, rop_qty=1, unitprice=100,
                 contractual_lead_time=20,
                 last_180_day_cnsmptn_qty=1, last_365_day_cnsmptn_qty=1,
                 last_547_day_cnsmptn_qty=1, days_since_last_issue=100,
                 recom_max=3, recom_rop=1, atm_recommended_max=3,
                 atm_recommended_rop=1, sfm_brr_max=3, sfm_brr_rop=1),
        # r10: dead maintain at 1 -> auto_cleared
        make_row(item_id=100010, max_qty=1, recom_max=1, atm_recommended_max=1,
                 sfm_brr_max=1, unitprice=20, aging_status="Dead"),
        # r11: different module -> excluded by TCB filter
        make_row(item_id=100011, module="BA"),
    ]
    return rows_to_csv(rows)


def upload(client, csv_bytes: bytes, headers=ENG, label="TEST", module="TCB"):
    return client.post(
        "/upload-bom-file",
        files={"file": ("test.csv", csv_bytes, "text/csv")},
        data={"label": label, "module_filter": module},
        headers=headers,
    )
