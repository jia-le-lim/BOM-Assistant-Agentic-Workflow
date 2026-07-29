"""Referential integrity, and the ON DELETE choices behind it.

The delete behaviour is picked per relation, not uniformly, and each choice
encodes a rule about this system:

  CASCADE   bom_rows, recommendation_result, model_prediction_log -- derived
            data, reproducible by re-running ingest and score.
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
        for table in ("bom_rows", "recommendation_result"):
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
    while production diverges -- the F7 failure mode."""
    from app.db import POSTGRES_DDL, SQLITE_DDL

    expected = [
        ("bom_rows", "batches", "CASCADE"),
        ("recommendation_result", "batches", "CASCADE"),
        ("recommendation_result", "bom_rows", "CASCADE"),
        ("review_history", "batches", "RESTRICT"),
        ("review_history", "recommendation_result", "RESTRICT"),
        ("pending_change", "batches", "RESTRICT"),
        ("pending_change", "review_history", "RESTRICT"),
        ("conversation_turn", "batches", "SET NULL"),
        ("item_note", "batches", "SET NULL"),
        ("item_note", "conversation_turn", "SET NULL"),
        ("model_prediction_log", "batches", "CASCADE"),
    ]
    for ddl, name in ((SQLITE_DDL, "sqlite"), (POSTGRES_DDL, "postgres")):
        for child, parent, action in expected:
            body = ddl.split(f"CREATE TABLE IF NOT EXISTS {child} (")[1].split(");")[0]
            ref = f"REFERENCES {parent}"
            assert ref in body, f"{name}: {child} is missing {ref}"
            assert f"ON DELETE {action}" in body, (
                f"{name}: {child} -> {parent} should be ON DELETE {action}")
