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
from .db import Conn

import numpy as np
import pandas as pd

COLUMN_RENAMES = {"new_modulle": "new_module", "senstivity_tag": "sensitivity_tag"}
CONS_COLS = ["last_5_day_cnsmptn_qty", "last_30_day_cnsmptn_qty", "last_90_day_cnsmptn_qty",
             "last_180_day_cnsmptn_qty", "last_365_day_cnsmptn_qty", "last_547_day_cnsmptn_qty"]
REQUIRED_COLS = ["item_id", "max_qty", "rop_qty", "min_qty", "unitprice"]


class IngestionError(ValueError):
    pass


class IngestionConflict(IngestionError):
    pass


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _read_table(content: bytes, filename: str) -> pd.DataFrame:
    """Parse a BOM review file (CSV or Excel) into all-string columns.

    Excel workbooks may carry banner rows above the header, so the row that
    contains 'item_id' is auto-detected and used as the header."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xls")):
        try:
            probe = pd.read_excel(io.BytesIO(content), header=None, nrows=25, dtype=str)
            hdr = 0
            for i in range(len(probe)):
                cells = probe.iloc[i].astype(str).str.strip().str.lower()
                if cells.eq("item_id").any():
                    hdr = i
                    break
            df = pd.read_excel(io.BytesIO(content), header=hdr, dtype=str)
            return df.fillna("")
        except Exception as e:  # noqa: BLE001 - surface as 400, not 500
            raise IngestionError(f"Could not parse Excel: {e}") from e
    try:
        return pd.read_csv(io.BytesIO(content), dtype=str,
                           encoding="utf-8-sig", keep_default_na=False)
    except Exception as e:  # noqa: BLE001 - surface as 400, not 500
        raise IngestionError(f"Could not parse CSV: {e}") from e


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


def ingest(conn: Conn, content: bytes, label: str, filename: str,
           module_filter: str | None, user: str, match_mode: str = "exact",
           batch_id: int | None = None) -> dict:
    df = _read_table(content, filename)

    df = normalize(df)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise IngestionError(f"Missing required columns: {missing}")

    if module_filter and module_filter.upper() != "ALL":
        if match_mode == "tag":
            # Multi-tag rows carry a comma list (e.g. "Module-TCB,Module-Epoxy")
            # in new_module; a substring match keeps a part in every module it
            # is tagged with, not just an exact single-module cell.
            col = "new_module" if "new_module" in df.columns else "module"
            if col not in df.columns:
                raise IngestionError("no module/new_module column but module_filter requested")
            needle = module_filter.strip().lower()
            mask = df[col].astype(str).str.lower().str.contains(needle, na=False, regex=False)
            df = df[mask].copy()
        else:
            if "module" not in df.columns:
                raise IngestionError("module column missing but module_filter requested")
            df = df[df["module"] == module_filter].copy()
    if df.empty:
        raise IngestionError(f"No rows remain after module filter '{module_filter}'")
    df = df.reset_index(drop=True)

    reason = quarantine_mask(df)
    n_quar = int((reason != "").sum())

    if batch_id is None:
        batch_id = conn.insert_returning(
            "INSERT INTO batches (label, source_filename, uploaded_by, module_filter, "
            "row_count, quarantined_count) VALUES (?,?,?,?,?,?)",
            (label, filename, user, module_filter or "ALL", len(df), n_quar),
            "batches",
        )
        workspace_upload = False
    else:
        workspace_upload = True

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
    if workspace_upload:
        update = (
            "UPDATE batches SET source_filename=?, uploaded_at=datetime('now'), "
            "row_count=?, quarantined_count=?, status='loaded' WHERE batch_id=? "
            "AND uploaded_by=? AND status='draft' RETURNING batch_id")
        params = (filename, len(df), n_quar, batch_id, user)
        if conn.is_postgres:
            # A single statement keeps the claim and all rows atomic even over
            # PostgREST, where commit/rollback cannot span HTTP calls.
            records = [dict(zip(("item_id", "stockroom_id", "module", "quarantined",
                                 "quarantine_reason", "payload"), row[1:])) for row in rows]
            claimed = conn.execute(
                f"WITH claimed AS ({update}), inserted AS ("
                "INSERT INTO bom_rows (batch_id, item_id, stockroom_id, module, quarantined, quarantine_reason, payload) "
                "SELECT claimed.batch_id, r.item_id, r.stockroom_id, r.module, r.quarantined, r.quarantine_reason, r.payload "
                "FROM claimed CROSS JOIN jsonb_to_recordset(CAST(? AS jsonb)) AS r("
                "item_id text, stockroom_id text, module text, quarantined integer, quarantine_reason text, payload text) "
                "RETURNING batch_id) SELECT batch_id FROM claimed",
                (*params, json.dumps(records))).fetchone()
        else:
            claimed = conn.execute(update, params).fetchone()
        if claimed is None:
            raise IngestionConflict("This workspace already has an upload. Refresh to see its current status.")
    if not workspace_upload or not conn.is_postgres:
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
