"""Backtest advisory triage on the January consumable/non-consumable splits."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "output" / "s16_triage_backtest.csv"
SPLITS = {
    "consumable": ROOT / "analysis" / "output" / "tcb_consumable_moving.csv",
    "non_consumable": ROOT / "analysis" / "output" / "tcb_nonconsumable_dead.csv",
}


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update({
            "BOM_ALLOW_SQLITE": "1",
            "BOM_DB_PATH": str(Path(tmp) / "triage.db"),
            "DATABASE_URL": "",
            "LLM_BASE_URL": "",
            "BOM_ENGINE": "statistical",
        })
        sys.path.insert(0, str(ROOT / "backend"))
        from app.agent.triage import run_triage
        from app.db import active_config, get_conn, init_db
        from app.engine_adapter import score_batch
        from app.ingestion import ingest

        init_db()
        conn = get_conn()
        cards = []
        try:
            precision_bar = float(active_config(conn, "s16-backtest").get(
                "triage_clear_precision_bar", 0.98))
            for segment, path in SPLITS.items():
                batch_id = ingest(conn, path.read_bytes(), segment, path.name,
                                  None, "s16-backtest")["batch_id"]
                score_batch(conn, batch_id)
                while True:
                    summary = run_triage(conn, batch_id)
                    conn.commit()
                    if not summary["budget_exhausted"]:
                        break

                rows = [dict(r) for r in conn.execute(
                    "SELECT r.agreement, t.triage_tier FROM recommendation_result r "
                    "JOIN triage_result t ON t.batch_id=r.batch_id "
                    "AND t.item_id=r.item_id AND t.stockroom_id=r.stockroom_id "
                    "WHERE r.batch_id=? AND r.review_required='Y'", (batch_id,))]
                clear = [r for r in rows if r["triage_tier"] == "clear_candidate"]
                correct = sum(r["agreement"] == "match" for r in clear)
                divergence = [r for r in rows if r["agreement"] == "diverge"]
                divergence_cleared = sum(
                    r["triage_tier"] == "clear_candidate" for r in divergence)
                precision = correct / len(clear) if clear else None
                cards.append({
                    "segment": segment,
                    "review_rows": len(rows),
                    "divergence_rows": len(divergence),
                    "divergence_cleared": divergence_cleared,
                    "clear_candidates": len(clear),
                    "clear_precision": round(precision, 4) if precision is not None else None,
                    "precision_bar": precision_bar,
                    "gate_pass": bool(divergence_cleared == 0 and
                                      (precision is None or precision >= precision_bar)),
                })
        finally:
            conn.close()

    total_clear = sum(r["clear_candidates"] for r in cards)
    total_correct = sum(r["clear_candidates"] * (r["clear_precision"] or 0)
                        for r in cards)
    total_precision = total_correct / total_clear if total_clear else 0.0
    cards.append({
        "segment": "total",
        "review_rows": sum(r["review_rows"] for r in cards),
        "divergence_rows": sum(r["divergence_rows"] for r in cards),
        "divergence_cleared": sum(r["divergence_cleared"] for r in cards),
        "clear_candidates": total_clear,
        "clear_precision": round(total_precision, 4),
        "precision_bar": cards[0]["precision_bar"],
        "gate_pass": bool(total_clear and total_precision >= cards[0]["precision_bar"]
                          and not sum(r["divergence_cleared"] for r in cards)),
    })
    card = pd.DataFrame(cards)
    card.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(card.to_string(index=False))
    print(f"\nwrote: {OUT.relative_to(ROOT)}")
    if not bool(card.loc[card["segment"] == "total", "gate_pass"].iloc[0]):
        raise SystemExit("triage precision gate failed")


if __name__ == "__main__":
    main()
