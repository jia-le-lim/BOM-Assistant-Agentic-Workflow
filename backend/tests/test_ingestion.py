import io

import pandas as pd

from conftest import ENG, VIEWER, make_row, rows_to_csv, upload


def test_normalize_fixes_typos_and_casing():
    from app.ingestion import normalize
    df = pd.DataFrame([{
        " item_id ": " 1 ", "new_modulle": "Module-TCB ", "senstivity_tag": "x",
        "sfm_recommendation": "maintain Algo",
    }])
    out = normalize(df)
    assert "item_id" in out.columns
    assert "new_module" in out.columns and "new_modulle" not in out.columns
    assert "sensitivity_tag" in out.columns and "senstivity_tag" not in out.columns
    assert out["sfm_recommendation"].iloc[0] == "Maintain Algo"
    assert out["item_id"].iloc[0] == "1"


def test_upload_summary_and_quarantine(client, synth_csv):
    r = upload(client, synth_csv)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["rows_loaded"] == 10          # BA row excluded by module filter
    assert s["rows_quarantined"] == 3      # neg consumption + 2 duplicate rows
    assert s["quarantine_reasons"] == {"DUPLICATE_KEY": 2, "NEGATIVE_CONSUMPTION": 1}


def test_upload_rbac(client, synth_csv):
    assert upload(client, synth_csv, headers=VIEWER).status_code == 403


def test_upload_rejects_non_csv(client):
    r = client.post("/upload-bom-file",
                    files={"file": ("x.xlsx", b"junk", "application/octet-stream")},
                    data={"label": "X"}, headers=ENG)
    assert r.status_code == 400


def _mt_upload(client, module_filter, module_match):
    rows = [
        make_row(item_id="T1", new_modulle="Module-TCB"),
        make_row(item_id="E1", new_modulle="Module-Epoxy,Module-TCB"),
        make_row(item_id="W1", new_modulle="Module-Wirebond"),
    ]
    return client.post(
        "/upload-bom-file",
        files={"file": ("m.csv", rows_to_csv(rows), "text/csv")},
        data={"label": "mt", "module_filter": module_filter, "module_match": module_match},
        headers=ENG)


def test_multitag_module_match_keeps_rows_tagged_among_others(client):
    r = _mt_upload(client, "Epoxy", "tag")
    assert r.status_code == 200, r.text
    assert r.json()["rows_loaded"] == 1                 # only E1 carries Module-Epoxy
    r2 = _mt_upload(client, "TCB", "tag")
    assert r2.json()["rows_loaded"] == 2                # T1 and E1 both carry Module-TCB


def test_exact_match_is_the_default_and_uses_the_module_column(client):
    # make_row sets module="TCB"; exact mode matches that single-module cell.
    r = _mt_upload(client, "Epoxy", "exact")
    assert r.status_code == 400                          # no module=="Epoxy" rows


def test_module_match_rejects_bad_mode(client):
    r = _mt_upload(client, "TCB", "sideways")
    assert r.status_code == 400


def test_upload_rejects_missing_columns(client):
    bad = rows_to_csv([{"item_id": "1", "module": "TCB"}])
    r = upload(client, bad)
    assert r.status_code == 400
    assert "Missing required columns" in r.json()["detail"]


def test_upload_rejects_empty_module_filter_result(client):
    rows = [make_row(item_id=1, module="BA")]
    r = upload(client, rows_to_csv(rows), module="TCB")
    assert r.status_code == 400
