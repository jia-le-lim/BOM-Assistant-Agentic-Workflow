"""Ingestion layer -- CSV -> normalize -> quarantine -> persist.

Implements the ingestion fixes from Analysis_Phase0_TCB_Jan26.md section 7.4:
  1. strip whitespace on headers and every cell
  2. fix source column typos (new_modulle -> new_module, senstivity_tag -> sensitivity_tag)
  3. normalize sfm_recommendation casing ("maintain Algo" -> "Maintain Algo")
  4. quarantine hard corruption (negative consumption, broken consumption ladder,
     missing item_id, duplicate item_id+stockroom key)

Split of responsibilities: QUARANTINE = do not score at all (data must be fixed
upstream). Engine Layer-1 validation flags (bad price, bad lead time, max<rop)
still score but force review_required = Y -- the engine handles those itself.
"""

import io
import json
import sqlite3

import numpy as np
import pandas as pd

COLUMN_RENAMES = {"new_modulle": "new_module", "senstivity_tag": "sensitivity_tag"}
CONS_COLS = ["last_5_day_cnsmptn_qty", "last_30_day_cnsmptn_qty", "last_90_day_cnsmptn_qty",
             "last_180_day_cnsmptn_qty", "last_365_day_cnsmptn_qty", "last_547_day_cnsmptn_qty"]
REQUIRED_COLS = ["item_id", "max_qty", "rop_qty", "min_qty", "unitprice"]


class IngestionError(ValueError):
    pass


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        if hasattr(df[c], "str"):
            df[c] = df[c].str.strip()
    df = df.rename(columns=COLUMN_RENAMES)
    if "sfm_recommendation" in df.columns:
        low = df["sfm_recommendation"].str.lower()
        for verb in ("Increase", "Maintain", "Decrease"):
            df.loc[low == f"{verb.lower()} algo", "sfm_recommendation"] = f"{verb} Algo"
    return df


def quarantine_mask(df: pd.DataFrame) -> pd.Series:
    """Returns a Series of quarantine reasons ('' = keep)."""
    reason = pd.Series("", index=df.index, dtype="object")

    item = df.get("item_id", pd.Series("", index=df.index)).astype(str).str.strip()
    reason[item == ""] = "MISSING_ITEM_ID"

    key = item + "|" + df.get("stockroom_id", pd.Series("", index=df.index)).astype(str)
    dup = key.duplicated(keep=False) & (item != "")
    reason[dup] = "DUPLICATE_KEY"

    neg = pd.Series(False, index=df.index)
    for c in CONS_COLS:
        neg |= _num(df, c) < 0
    reason[neg & (reason == "")] = "NEGATIVE_CONSUMPTION"

    ladder = pd.Series(False, index=df.index)
    for a, b in zip(CONS_COLS, CONS_COLS[1:]):
        ladder |= _num(df, a) > _num(df, b)
    reason[ladder & (reason == "")] = "CONSUMPTION_LADDER_BROKEN"
    return reason


def ingest(conn: sqlite3.Connection, content: bytes, label: str, filename: str,
           module_filter: str | None, user: str) -> dict:
    try:
        df = pd.read_csv(io.BytesIO(content), dtype=str,
                         encoding="utf-8-sig", keep_default_na=False)
    except Exception as e:  # noqa: BLE001 - surface as 400, not 500
        raise IngestionError(f"Could not parse CSV: {e}") from e

    df = normalize(df)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise IngestionError(f"Missing required columns: {missing}")

    if module_filter and module_filter.upper() != "ALL":
        if "module" not in df.columns:
            raise IngestionError("module column missing but module_filter requested")
        df = df[df["module"] == module_filter].copy()
    if df.empty:
        raise IngestionError(f"No rows remain after module filter '{module_filter}'")
    df = df.reset_index(drop=True)

    reason = quarantine_mask(df)
    n_quar = int((reason != "").sum())

    cur = conn.execute(
        "INSERT INTO batches (label, source_filename, uploaded_by, module_filter, "
        "row_count, quarantined_count) VALUES (?,?,?,?,?,?)",
        (label, filename, user, module_filter or "ALL", len(df), n_quar),
    )
    batch_id = cur.lastrowid

    rows = []
    seen = set()
    for i, rec in enumerate(df.to_dict(orient="records")):
        item = str(rec.get("item_id", "")).strip()
        stk = str(rec.get("stockroom_id", "")).strip()
        pk = (item, stk)
        # Duplicate-key rows cannot share a PRIMARY KEY slot; suffix duplicates so
        # every offending row is preserved and visibly quarantined.
        if pk in seen or item == "":
            item = f"{item}#dup{i}"
        seen.add((item, stk))
        rows.append((batch_id, item, stk, rec.get("module", ""),
                     1 if reason.iloc[i] else 0, reason.iloc[i] or None,
                     json.dumps(rec)))
    conn.executemany(
        "INSERT INTO bom_rows (batch_id, item_id, stockroom_id, module, quarantined, "
        "quarantine_reason, payload) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()

    return {
        "batch_id": batch_id,
        "label": label,
        "module_filter": module_filter or "ALL",
        "rows_loaded": len(df),
        "rows_quarantined": n_quar,
        "quarantine_reasons": reason[reason != ""].value_counts().to_dict(),
    }
