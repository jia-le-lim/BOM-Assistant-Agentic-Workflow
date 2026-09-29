"""Opt-in live provider evaluation. Reads an existing workspace; writes no turns.

Run from backend: python scripts/check_chat_retrieval.py --batch-id 13
Uses the configured provider and database. Tool execution is restricted to reads.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import tools as T
from app.agent.graph import run_chat
from app.agent.prompts import DONT_KNOW
from app.db import get_conn


class ReadOnly:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        if not sql.lstrip().upper().startswith("SELECT ") or ";" in sql:
            raise RuntimeError("The live retrieval check permits SELECT statements only.")
        return self.connection.execute(sql, params)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", type=int, required=True)
    parser.add_argument("--case", help="Run only the named case(s), comma-separated")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = [
        ("original_cable", "show me what the item that catogorise in cable", "category"),
        ("category_count", "How many items are categorised as cables? Show the first 5.", "category"),
        ("description", "Find items whose description contains tubing.", "description"),
        ("uncovered_dormant", "Show the first 8 dormant rows with no confirmed stocking rule, including current Max/ROP/Min and risk.", "dormant"),
        ("missing_item", "Show an item's review history.", "clarification"),
        ("unsupported_spending", "What was the actual purchasing spend for TCB in January 2026?", "unsupported"),
        ("no_matching_description", "Find items whose description contains ZZZ_NO_SUCH_PART_95173.", "empty"),
        ("standard_queue", "Show the first 5 high risk review items.", "queue"),
        ("active_route", "Show the first 5 items on the active route.", "active"),
        ("unknown_category", "List items in the category unicorn_parts.", "clarification"),
        ("description_explanation", "can u explain to me why tubing is reduce from 133 max to 1", "description"),
    ]
    connection = get_conn()
    conn = ReadOnly(connection)
    actor = {"user": "retrieval-diagnostic", "role": "engineer"}
    reports = []
    try:
        expected = T.search_items(T.ToolContext(conn, actor, args.batch_id, "verify cable count"), category="cable")["total_count"]
        for name, question, kind in cases:
            if args.case and name not in args.case.split(","):
                continue
            result = run_chat(conn, question, args.batch_id, actor, allow_writes=False,
                              page_context={"path": "/chat", "title": "Chat", "batch_id": args.batch_id})
            calls = result["tool_calls"]
            searches = [c for c in calls if c["name"] in {"search_items", "list_review_queue"} and c["ok"]]
            passed = result["answer"] != DONT_KNOW
            if kind == "category":
                passed &= any(c["args"].get("category", "").casefold() in {"cable", "cables"} for c in searches)
                if name == "category_count":
                    passed &= str(expected) in result["answer"].replace(",", "")
            elif kind == "description":
                passed &= any("tubing" in c["args"].get("query", "").casefold() for c in searches)
            elif kind == "dormant":
                passed &= any(c["args"].get("uncovered_dormant") is True for c in searches)
            elif kind in {"clarification", "unsupported"}:
                passed &= result["response_status"] == kind
            elif kind == "empty":
                passed &= bool(searches) and bool(result["sources"])
            elif kind == "queue":
                passed &= any(c["args"].get("risk_level") == "High" for c in searches)
            elif kind == "active":
                passed &= any(c["args"].get("route") == "active" for c in searches)
            reports.append({"case": name, "passed": bool(passed), "intent": result["intent"],
                            "provider": result["provider"], "model": result["model"],
                            "response_status": result["response_status"], "model_calls": result["model_calls"],
                            "tools": calls, "answer": result["answer"]})
            print(json.dumps({key: value for key, value in reports[-1].items() if key != "answer"}), flush=True)
    finally:
        connection.close()
    if args.output:
        args.output.write_text(json.dumps({"batch_id": args.batch_id, "expected_cable_rows": expected,
                                          "cases": [{k: v for k, v in row.items() if k != "answer"} for row in reports]}, indent=2), encoding="utf-8")
    if not reports or not all(row["passed"] for row in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
