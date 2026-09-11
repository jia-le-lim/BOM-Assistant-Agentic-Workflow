"""Prepare, apply atomically, and verify a TCB-only historical archive import.

Uses the normal ingest/score/backfill functions in an isolated SQLite staging
database. Apply streams the reviewed inserts to the local Docker Postgres in
one transaction, after checking that the live source snapshot is unchanged.
Existing rows are never updated. Audit files belong in ignored analysis/output.
"""

import argparse
import hashlib
import json
import math
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.db import Conn, SQLITE_DDL, get_conn  # noqa: E402
from app.engine_adapter import score_batch  # noqa: E402
from app.ingestion import ingest, normalize  # noqa: E402
from backfill_history import as_ts, synthesise_reviews  # noqa: E402

ROOT = BACKEND.parent
TABLE_KEYS = {
    "batches": "batch_id",
    "bom_rows": "batch_id,item_id,stockroom_id",
    "recommendation_result": "batch_id,item_id,stockroom_id",
    "review_history": "review_id",
    "rule_config": "config_id",
    "machine_criticality_config": "pattern",
    "part_category_config": "pattern",
    "dormant_rule_config": "rule_id",
}
DATA_TABLES = tuple(TABLE_KEYS)[:4]
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def cycle_for(payload, batch):
    cycle = str(payload.get("bom_review_cylce") or "").strip()
    if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", cycle):
        return cycle
    # Legacy monthly workbooks have no cycle column. Their modified_date can
    # belong to an older snapshot, so the named review month takes precedence.
    if not str(batch.get("label", "")).lower().startswith("hist"):
        return None
    name = batch.get("source_filename") or batch.get("label", "")
    found = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[^a-z0-9]*(20\d{2}|\d{2})(?!\d)",
        name.lower())
    if found:
        year = int(found[2])
        return f"{year + 2000 if year < 100 else year:04d}-{MONTHS[found[1]]:02d}"
    raise ValueError(f"Cannot establish cycle for historical batch {batch['batch_id']}")


def fingerprint_sql(table, predicate="TRUE"):
    keys = TABLE_KEYS[table]
    return ("SELECT COUNT(*) AS n, md5(COALESCE(string_agg(row_to_json(t)::text, '' "
            f"ORDER BY {keys}), '')) AS digest FROM "
            f"(SELECT * FROM {table} WHERE {predicate}) t")


def fingerprints(conn, predicates=None):
    return {t: dict(conn.execute(fingerprint_sql(t, (predicates or {}).get(t, "TRUE"))).fetchone())
            for t in TABLE_KEYS}


def dump_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


class StageConn(Conn):
    """Keep SQLite SQL local even while app.config points to live Postgres."""

    def execute(self, sql, params=()):
        return self._raw.execute(sql, params)

    def executemany(self, sql, seq):
        self._raw.executemany(sql, seq)


def open_stage(path):
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=ON")
    return StageConn(raw, False)


def prepare(source, output):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "prepared.db").exists():
        raise ValueError("Use a fresh audit directory; a prepared import already exists")
    parts, total = [], 0
    for frame in pd.read_csv(source, dtype=str, keep_default_na=False,
                             encoding="utf-8-sig", chunksize=20000):
        total += len(frame)
        parts.append(frame[frame.module.str.strip().eq("TCB")].copy())
    source_df = normalize(pd.concat(parts, ignore_index=True))
    key_columns = ["item_id", "stockroom_id", "bom_review_cylce"]
    if source_df.empty or source_df[key_columns].eq("").any().any():
        raise ValueError("Missing TCB records or required historical identity")
    if not source_df.bom_review_cylce.str.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])").all():
        raise ValueError("Invalid source cycle")
    exact_duplicates = int(source_df.duplicated().sum())
    source_df = source_df.drop_duplicates().reset_index(drop=True)
    if source_df.duplicated(key_columns).any():
        raise ValueError("Conflicting records share an item/stockroom/review cycle")

    live = get_conn()
    stage = open_stage(output / "prepared.db")
    try:
        before = fingerprints(live)
        stage._raw.executescript(SQLITE_DDL)
        for table in TABLE_KEYS:
            records = [dict(r) for r in live.execute(f"SELECT * FROM {table}")]
            if records:
                columns = list(records[0])
                local_columns = {r["name"] for r in stage.execute(f"PRAGMA table_info({table})")}
                for column in set(columns) - local_columns:
                    if not re.fullmatch(r"[a-z_][a-z0-9_]*", column):
                        raise ValueError("Unexpected database column name")
                    kind = live.execute(
                        "SELECT data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=? AND column_name=?",
                        (table, column)).fetchone()["data_type"]
                    local_type = "INTEGER" if kind in ("bigint", "integer", "smallint") else "REAL" if kind in ("double precision", "real", "numeric") else "TEXT"
                    stage.execute(f"ALTER TABLE {table} ADD COLUMN {column} {local_type}")
                stage.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [tuple(row[c] for c in columns) for row in records])
            print(f"Snapshot {table}: {len(records)}", flush=True)
        stage.commit()
        if before != fingerprints(live):
            raise ValueError("Live data changed during snapshot; prepare again")
        backup = sqlite3.connect(output / "before.db")
        stage._raw.backup(backup)
        backup.close()

        batches = {r["batch_id"]: dict(r) for r in stage.execute("SELECT * FROM batches")}
        existing, unknown, baseline_duplicates = {}, 0, 0
        for row in stage.execute("SELECT * FROM bom_rows"):
            payload = json.loads(row["payload"])
            if str(payload.get("module", "")).strip() != "TCB":
                continue
            cycle = cycle_for(payload, batches[row["batch_id"]])
            if cycle is None:
                unknown += 1
                continue
            key = (str(payload.get("item_id", row["item_id"])).strip(),
                   str(payload.get("stockroom_id", row["stockroom_id"])).strip(), cycle)
            baseline_duplicates += int(key in existing)
            existing[key] = row["batch_id"]
        pending, skipped = [], []
        for record in source_df.to_dict("records"):
            key = tuple(record[k] for k in key_columns)
            if key in existing:
                skipped.append(dict(zip(key_columns, key), existing_batch_id=existing[key]))
            else:
                pending.append(record)
        pd.DataFrame(skipped, columns=key_columns + ["existing_batch_id"]).to_csv(
            output / "skipped_existing.csv", index=False)
        plan = {
            "source": str(source.resolve()),
            "source_sha256": hashlib.file_digest(source.open("rb"), "sha256").hexdigest(),
            "source_total_rows": total, "source_tcb_rows": len(source_df) + exact_duplicates,
            "source_exact_duplicates_skipped": exact_duplicates,
            "database_duplicates_skipped": len(skipped), "new_rows": len(pending),
            "nonhistorical_rows_without_cycle": unknown,
            "baseline_duplicate_historical_keys": baseline_duplicates,
            "identity": "item_id + stockroom_id + bom_review_cylce; legacy historical filenames supply missing cycle",
            "historical_date": "source_modified_date when present, otherwise modified_date; date-only values use midnight",
            "before": before, "original_batch_ids": sorted(batches),
            "original_review_max": stage.execute("SELECT COALESCE(MAX(review_id),0) AS n FROM review_history").fetchone()["n"],
            "cycles": [],
        }
        new_df = pd.DataFrame(pending)
        groups = new_df.groupby("bom_review_cylce", sort=True) if pending else []
        for cycle, frame in groups:
            label = f"hist-tcb-archive-{cycle}"
            summary = ingest(stage, frame.to_csv(index=False).encode("utf-8"), label,
                             source.name, "TCB", "archive-backfill", match_mode="exact")
            bid = summary["batch_id"]
            if summary["rows_loaded"] > summary["rows_quarantined"]:
                score = score_batch(stage, bid)
                reviews = synthesise_reviews(stage, bid, score["rule_version"])
                for record in frame.to_dict("records"):
                    timestamp = as_ts(record.get("source_modified_date")) or as_ts(record.get("modified_date"))
                    if timestamp is None:
                        raise ValueError("Historical decision date is missing")
                    reviewer = record.get("source_modified_user") or record.get("modified_user") or "backfill"
                    stage.execute("UPDATE review_history SET reviewed_at=?,reviewer=? WHERE batch_id=? AND item_id=? AND stockroom_id=?",
                                  (timestamp, reviewer[:120], bid, record["item_id"], record["stockroom_id"]))
                stage.commit()
            else:
                score = {"rows_scored": 0}
                reviews = {"reviews": 0, "no_engineer_number": 0, "inconsistent_triples": 0}
            result = {"cycle": cycle, **summary, "scored": score["rows_scored"], **reviews}
            plan["cycles"].append(result)
            print(json.dumps(result), flush=True)
        assert not list(stage.execute("PRAGMA foreign_key_check"))
        plan["total_reviews_added"] = sum(c["reviews"] for c in plan["cycles"])
        plan["total_quarantined"] = sum(c["rows_quarantined"] for c in plan["cycles"])
        dump_json(output / "plan.json", plan)
        write_sql(stage, plan, output / "apply.sql")
        print(json.dumps({k: plan[k] for k in (
            "source_tcb_rows", "database_duplicates_skipped", "new_rows", "total_reviews_added", "total_quarantined")}), flush=True)
    finally:
        stage.close()
        live.close()


def literal(value):
    # No shell interpolation. PostgreSQL string literals escape apostrophes by
    # doubling them; the script explicitly enables standard_conforming_strings.
    return "'" + str(value).replace("'", "''") + "'"


def write_sql(stage, plan, path):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\\set ON_ERROR_STOP on\nBEGIN;\nSET LOCAL standard_conforming_strings=on;\nSET LOCAL lock_timeout='10s';\n")
        stream.write("LOCK TABLE " + ",".join(TABLE_KEYS) + " IN SHARE ROW EXCLUSIVE MODE;\n")
        for table, expected in plan["before"].items():
            stream.write("DO $guard$ DECLARE actual record; BEGIN SELECT * INTO actual FROM (" +
                         fingerprint_sql(table) + ") s; IF actual.n <> " + str(expected["n"]) +
                         " OR actual.digest <> " + literal(expected["digest"]) +
                         " THEN RAISE EXCEPTION 'Live snapshot changed: " + table +
                         "'; END IF; END $guard$;\n")
        for cycle in plan["cycles"]:
            bid = cycle["batch_id"]
            data = {table: [dict(r) for r in stage.execute(f"SELECT * FROM {table} WHERE batch_id=?", (bid,))]
                    for table in DATA_TABLES}
            encoded = json.dumps(data)
            delimiter = "$import_" + hashlib.sha256(encoded.encode()).hexdigest()[:20] + "$"
            if delimiter in encoded:
                raise ValueError("Source collides with SQL block delimiter")
            stream.write("DO " + delimiter + " DECLARE p jsonb := " + literal(encoded) +
                         "::jsonb; new_batch_id bigint; BEGIN\n")
            for table in DATA_TABLES:
                if not data[table]:
                    continue
                columns = [c for c in data[table][0] if c not in ("batch_id", "review_id")]
                if table == "batches":
                    target = ",".join(columns)
                    values = ",".join("r." + c for c in columns)
                else:
                    target = "batch_id," + ",".join(columns)
                    values = "new_batch_id," + ",".join("r." + c for c in columns)
                stream.write(f"INSERT INTO {table} ({target}) SELECT {values} FROM "
                             f"jsonb_populate_recordset(NULL::{table}, p->{literal(table)}) r")
                if table == "batches":
                    stream.write(" RETURNING batch_id INTO new_batch_id")
                stream.write(";\n")
            stream.write("END " + delimiter + ";\n")
        stream.write("COMMIT;\n")


def verify(output):
    plan = json.loads((output / "plan.json").read_text(encoding="utf-8"))
    live = get_conn()
    stage = open_stage(output / "prepared.db")
    try:
        ids = ",".join(map(str, plan["original_batch_ids"])) or "NULL"
        predicates = {t: f"batch_id IN ({ids})" for t in DATA_TABLES[:3]}
        predicates["review_history"] = f"review_id <= {int(plan['original_review_max'])}"
        old = fingerprints(live, predicates)
        if old != plan["before"]:
            raise ValueError("Original database rows changed")
        checked = []
        for cycle in plan["cycles"]:
            found = live.execute("SELECT * FROM batches WHERE label=? AND source_filename=?",
                                 (cycle["label"], Path(plan["source"]).name)).fetchall()
            if len(found) != 1:
                raise ValueError(f"Expected one imported batch for {cycle['cycle']}")
            bid = found[0]["batch_id"]
            for table in DATA_TABLES:
                actual = [dict(r) for r in live.execute(f"SELECT * FROM {table} WHERE batch_id=?", (bid,))]
                expected = [dict(r) for r in stage.execute(f"SELECT * FROM {table} WHERE batch_id=?", (cycle["batch_id"],))]
                def canonical(records):
                    return sorted(
                        ({k: v for k, v in row.items() if k not in ("batch_id", "review_id")} for row in records),
                        key=lambda row: (row.get("item_id", ""), row.get("stockroom_id", "")))
                actual, expected = canonical(actual), canonical(expected)
                def same_value(a, b):
                    # REST's JSON serialization can trim the last floating
                    # point digit. Integer quantities and source text stay exact.
                    if isinstance(b, float) and isinstance(a, (float, int)):
                        return math.isclose(a, b, rel_tol=1e-14, abs_tol=1e-12)
                    return a == b
                if (len(actual) != len(expected) or any(
                    a.keys() != e.keys() or any(not same_value(a[k], e[k]) for k in e)
                    for a, e in zip(actual, expected)
                )):
                    raise ValueError(f"Stored data differs from prepared data: {table}, {cycle['cycle']}")
            checked.append({"cycle": cycle["cycle"], "batch_id": bid, "rows": cycle["rows_loaded"],
                            "reviews": cycle["reviews"], "quarantined": cycle["rows_quarantined"]})
        batches = {r["batch_id"]: dict(r) for r in live.execute("SELECT * FROM batches")}
        seen = set()
        duplicates = []
        for row in live.execute("SELECT batch_id,payload FROM bom_rows WHERE module='TCB'"):
            payload = json.loads(row["payload"])
            cycle = cycle_for(payload, batches[row["batch_id"]])
            if cycle is None:
                continue
            key = (payload.get("item_id"), payload.get("stockroom_id"), cycle)
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        baseline_duplicates = plan["baseline_duplicate_historical_keys"]
        if len(duplicates) != baseline_duplicates:
            raise ValueError(f"Historical duplicate count changed: {len(duplicates)}")
        result = {"verified": True, "original_rows_unchanged": True,
                  "new_duplicate_historical_keys": 0, "preexisting_duplicate_historical_keys": baseline_duplicates,
                  "cycles": checked, "total_bom_rows": live.execute("SELECT COUNT(*) AS n FROM bom_rows").fetchone()["n"],
                  "total_reviews": live.execute("SELECT COUNT(*) AS n FROM review_history").fetchone()["n"]}
        dump_json(output / "verification.json", result)
        print(json.dumps(result), flush=True)
    finally:
        stage.close()
        live.close()


def apply(output, container):
    plan = json.loads((output / "plan.json").read_text(encoding="utf-8"))
    if not plan["new_rows"]:
        print("Nothing to import")
        return
    with (output / "apply.sql").open("rb") as sql_file:
        result = subprocess.run(["docker", "exec", "-i", container, "psql", "-X", "-U", "postgres",
                                 "-d", "postgres", "-v", "ON_ERROR_STOP=1"], stdin=sql_file,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # PostgreSQL may include source literals in error context. Keep them in the
    # ignored audit directory rather than printing business records to stdout.
    (output / "apply.stdout.log").write_bytes(result.stdout)
    (output / "apply.stderr.log").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError("Import transaction failed; inspect the local apply.stderr.log")
    print("Import transaction committed", flush=True)
    verify(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply", "verify"))
    parser.add_argument("--source", type=Path, default=ROOT / "BOM table/BOM Review Archive 1.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis/output/archive_tcb_import_20260911")
    parser.add_argument("--container", default="supabase-db")
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.source, args.output)
    elif args.mode == "apply":
        apply(args.output, args.container)
    else:
        verify(args.output)


if __name__ == "__main__":
    main()
