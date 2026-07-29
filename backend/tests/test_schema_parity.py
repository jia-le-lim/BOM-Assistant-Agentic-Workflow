"""The two DDL blocks in app/db.py are written per dialect, not generated.

This test is what keeps them from drifting: it parses both and asserts the
table and column sets are identical. If someone adds a column to one dialect
and forgets the other, this fails before the port silently diverges.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import IDENTITY_PK, POSTGRES_DDL, SQLITE_DDL  # noqa: E402

TABLE_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", re.S)


def parse(ddl: str) -> dict[str, list[str]]:
    """table -> ordered column names (constraint lines skipped)."""
    out: dict[str, list[str]] = {}
    for table, body in TABLE_RE.findall(ddl):
        cols: list[str] = []
        depth = 0
        for raw in body.split("\n"):
            line = raw.strip()
            if not line or line.startswith("--"):
                continue
            # Column defs are comma-separated but a line may hold several
            # ("new_max INTEGER, new_rop INTEGER, new_min INTEGER").
            for part in split_top_level(line):
                part = part.strip().rstrip(",")
                if not part:
                    continue
                head = part.split()[0].strip('"')
                if head.upper() in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK",
                                    "CONSTRAINT"):
                    continue
                cols.append(head)
            depth += line.count("(") - line.count(")")
        out[table] = cols
    return out


def split_top_level(line: str) -> list[str]:
    """Split on commas that are not inside parentheses or quotes."""
    parts, buf, depth, quote = [], [], 0, False
    for ch in line:
        if ch == "'":
            quote = not quote
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


SQLITE = parse(SQLITE_DDL)
POSTGRES = parse(POSTGRES_DDL)


def test_same_tables():
    assert set(SQLITE) == set(POSTGRES)


def test_expected_table_count():
    # 7 original + pending_change, conversation_turn, item_note,
    # model_prediction_log
    assert len(SQLITE) == 11, sorted(SQLITE)


@pytest.mark.parametrize("table", sorted(SQLITE))
def test_same_columns(table):
    assert SQLITE[table] == POSTGRES[table], (
        f"{table}: sqlite={SQLITE[table]} postgres={POSTGRES[table]}")


def test_identity_pk_map_matches_ddl():
    """Every table with a generated PK must be in IDENTITY_PK, or
    insert_returning() will KeyError on Postgres at runtime."""
    generated = {t for t in POSTGRES
                 if "GENERATED ALWAYS AS IDENTITY" in POSTGRES_DDL.split(
                     f"CREATE TABLE IF NOT EXISTS {t} (")[1].split(");")[0]}
    assert generated == set(IDENTITY_PK), (
        f"generated={sorted(generated)} mapped={sorted(IDENTITY_PK)}")
    for table, pk in IDENTITY_PK.items():
        assert pk in SQLITE[table]


def test_postgres_quotes_reserved_user_column():
    """`user` is reserved in Postgres; unquoted it is a syntax error."""
    for table in ("audit_log", "conversation_turn"):
        body = POSTGRES_DDL.split(
            f"CREATE TABLE IF NOT EXISTS {table} (")[1].split(");")[0]
        assert '"user"' in body, table
