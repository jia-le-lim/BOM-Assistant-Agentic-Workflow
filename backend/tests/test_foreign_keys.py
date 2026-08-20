"""Referential integrity, and the ON DELETE choices behind it.

The delete behaviour is picked per relation, not uniformly, and each choice
encodes a rule about this system:

  CASCADE   bom_rows, recommendation_result, model_prediction_log,
            triage_result -- derived data, reproducible by re-running.
  RESTRICT  review_history, pending_change -- the audit trail. Deleting a batch
            that carries decisions must fail loudly rather than shed them.
  SET NULL  conversation_turn.batch_id, item_note.origin_* -- provenance only.
            An item note must outlive the batch it was written against; the
            monthly roster rotates, and surviving that is the reason the table
            is keyed on item_id in the first place.

SQLite enforces these too (`PRAGMA foreign_keys = ON` in db.get_conn), so the
same assertions hold on both backends. Verified identically against the live
Supabase instance when the constraints were declared.
"""

import re
import sqlite3

import pytest
from conftest import ENG, SENIOR, upload


def scored_batch(client, csv_bytes):
    b = upload(client, csv_bytes).json()["batch_id"]
    client.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    return b


def raw(db_file):
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_foreign_keys_are_enforced_at_all(client, synth_csv, db_file):
    """Guards against the constraints silently not being applied -- which is
    exactly the state the schema was in before this migration."""
    scored_batch(client, synth_csv)
    conn = raw(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bom_rows (batch_id, item_id, stockroom_id, payload) "
                "VALUES (99999, 'ghost', '', '{}')")
            conn.commit()
    finally:
        conn.close()


def test_orphan_recommendation_is_rejected(client, synth_csv, db_file):
    """The composite link. A score with no source row is meaningless."""
    scored_batch(client, synth_csv)
    conn = raw(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO recommendation_result (batch_id, item_id, "
                "stockroom_id, new_max, new_rop, new_min) "
                "VALUES (1, 'no-such-item', '', 1, 1, 1)")
            conn.commit()
    finally:
        conn.close()


def test_pending_change_cannot_point_at_a_missing_review(client, synth_csv, db_file):
    """The boundary crossing must not be forgeable."""
    scored_batch(client, synth_csv)
    client.post("/chat", json={"question": "set item 100005 max to 3"},
                headers=ENG)
    conn = raw(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE pending_change SET confirmed_review_id=424242")
            conn.commit()
    finally:
        conn.close()


def test_batch_with_decisions_cannot_be_deleted(client, synth_csv, db_file):
    """RESTRICT. A month of approved changes must not vanish with its batch."""
    b = scored_batch(client, synth_csv)
    client.post(f"/review/100005?batch_id={b}",
                json={"decision": "accept", "comment": "keep"}, headers=ENG)
    conn = raw(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM batches WHERE batch_id=?", (b,))
            conn.commit()
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM review_history").fetchone()[0] == 1
    finally:
        conn.close()


def test_batch_without_decisions_cascades_cleanly(client, synth_csv, db_file):
    """CASCADE. Derived rows go; nothing is stranded."""
    b = scored_batch(client, synth_csv)
    conn = raw(db_file)
    try:
        conn.execute("DELETE FROM batches WHERE batch_id=?", (b,))
        conn.commit()
        for table in ("bom_rows", "recommendation_result", "triage_result"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert n == 0, f"{table} left {n} orphaned rows"
    finally:
        conn.close()


def test_item_note_outlives_its_batch(client, synth_csv, db_file):
    """SET NULL, and the reason the table exists.

    The monthly extract is a rotating sample: a part discussed in January may be
    absent in February and back in March. A note that died with its batch would
    lose exactly the context it was written to carry.
    """
    b = scored_batch(client, synth_csv)
    conn = raw(db_file)
    try:
        conn.execute(
            "INSERT INTO item_note (item_id, note, author, origin_batch_id) "
            "VALUES ('100005', 'runs hot in summer', 'alice', ?)", (b,))
        conn.commit()

        conn.execute("DELETE FROM batches WHERE batch_id=?", (b,))
        conn.commit()

        row = conn.execute(
            "SELECT note, origin_batch_id FROM item_note WHERE item_id='100005'"
        ).fetchone()
        assert row is not None, "the note was deleted with its batch"
        assert row[0] == "runs hot in summer"
        assert row[1] is None, "origin_batch_id should be nulled, not dangling"
    finally:
        conn.close()


def test_every_declared_relation_is_in_both_ddl_blocks():
    """Parity: a constraint added to one dialect only would let the suite pass
    while production diverges -- the F7 failure mode.

    Each action is matched to ITS OWN `REFERENCES <parent>` clause. A plain
    `"ON DELETE RESTRICT" in body` passes on a table with two FKs even when the
    two actions are swapped -- exactly the drift this test exists to catch.
    """
    from app.db import FOREIGN_KEYS, POSTGRES_DDL, SQLITE_DDL

    for ddl, dialect in ((SQLITE_DDL, "sqlite"), (POSTGRES_DDL, "postgres")):
        for _name, child, _cols, parent, _pcols, action in FOREIGN_KEYS:
            body = ddl.split(f"CREATE TABLE IF NOT EXISTS {child} (")[1].split(");")[0]
            m = re.search(
                rf"REFERENCES\s+{parent}\s*(?:\([^)]*\))?\s*ON DELETE "
                rf"(CASCADE|RESTRICT|SET NULL)", body)
            assert m, f"{dialect}: {child} is missing REFERENCES {parent}"
            assert m.group(1) == action, (
                f"{dialect}: {child} -> {parent} is ON DELETE {m.group(1)}, "
                f"expected {action}")
        # ...and nothing is declared in the DDL that FOREIGN_KEYS does not know
        # about, or _ensure_foreign_keys() would never apply it to a database
        # that already exists.
        assert len(re.findall(r"REFERENCES\s+\w+", ddl)) == len(FOREIGN_KEYS), (
            f"{dialect}: DDL and FOREIGN_KEYS disagree on how many relations "
            f"there are")


def test_fk_supporting_indexes_are_declared():
    """Postgres does not index the referencing side of a FK. Without these,
    every parent delete and every RESTRICT check scans the child table."""
    from app.db import FK_INDEX_DDL, FOREIGN_KEYS

    # bom_rows and recommendation_result lead their PRIMARY KEY with the FK
    # columns, so the PK index already serves those two.
    pk_covered = {("bom_rows", ("batch_id",)),
                  ("recommendation_result",
                   ("batch_id", "item_id", "stockroom_id")),
                  ("triage_result",
                   ("batch_id", "item_id", "stockroom_id")),
                  ("similarity_result",
                   ("batch_id", "item_id", "stockroom_id")),
                  ("similarity_neighbour",
                   ("batch_id", "item_id", "stockroom_id"))}
    flat = FK_INDEX_DDL.replace("\n", " ")
    for _name, child, cols, parent, _pcols, _action in FOREIGN_KEYS:
        if (child, cols) in pk_covered:
            continue
        want = f"ON {child}({', '.join(cols)})".replace(" ", "")
        assert want in flat.replace(" ", ""), (
            f"no index supporting {child} -> {parent} on {cols}")
