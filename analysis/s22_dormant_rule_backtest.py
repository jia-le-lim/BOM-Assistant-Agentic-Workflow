"""S22 -- what an engineer-owned dormant rule would have done, over eight cycles.

The engine sizes a part with no consumption in any window to zero unless it is
critical. Across 2024-07 -> 2026-01 the engineer overrode that answer on 1,344
of 8,343 dormant rows. This replays the shipped engine over the stored payloads
with and without a candidate rule set and reports, per cycle:

  matched      dormant rows a rule actually fired on
  agreement    engine-vs-engineer agreement on Max/ROP, before and after
  book value   sum(unitprice * Max), before and after

Both halves are the point. Q2 (owner decision, 2026-09-01) makes this a
stock-moving change: a rule set that lifts agreement while adding $400k of
inventory is not obviously a win, and this script is deliberately silent about
which trade to take.

Scoring is the REVIEW SCORECARD rule, matching s19/s20: a strict 10% relative
tolerance on Max and ROP, no absolute floor, Min excluded. It comes from the
engine's own AGREE_TOL so this harness cannot drift from it.

Run: python analysis/s22_dormant_rule_backtest.py   (offline, needs s20 payloads)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import dormant_rules as DR          # noqa: E402
from app import engine_statistical as E      # noqa: E402
from app.part_category import DEFAULT_RULES as CAT_RULES  # noqa: E402
from app.part_category import categorise     # noqa: E402
# Shared with s20: one copy of the scorecard rule, so it cannot drift from the
# engine's own tolerance.
from scorecard import matched as _matched, num as _n, pair as _pair  # noqa: E402

PKL = ROOT / "analysis" / "output" / "s20_payloads.pkl"
OUT = ROOT / "analysis" / "output" / "s22_dormant_backtest.csv"


def _rule(scope, key="", crit="", policy="hold_current", qty=None, priority=500):
    return {"scope": scope, "match_key": key, "criticality": crit,
            "policy": policy, "fixed_qty": qty, "priority": priority}


# Candidate rule sets, written the way dormant_rule_config stores them rather
# than as free parameters -- each arm is a thing an engineer would be asked to
# CONFIRM. None of them is seeded into the product: dormant_rules.DEFAULT_RULES
# ships one unconfirmed default, and these arms exist to say what confirming it
# would have cost.
ARMS: dict[str, list[dict]] = {
    "engine_only": [],
    "hold_current": [_rule("default", policy="hold_current", priority=900)],
    "fixed_1": [_rule("default", policy="fixed_qty", qty=1, priority=900)],
    "hold_current_non_critical": [
        # High-criticality dormant parts keep the engine's KEEP_ALIVE insurance
        # branch: that one is a safety decision, not a wear-and-tear judgement.
        _rule("default", crit="h", policy="zero", priority=100),
        _rule("default", policy="hold_current", priority=900),
    ],
}


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Attach part_category the way engine_adapter does at score time.

    The lexicon's shipped DEFAULT_RULES are used rather than the database's
    confirmed rows: this script is offline, and the defaults are what a fresh
    install categorises with anyway.
    """
    compiled = [(priority, category, re.compile(pattern, re.IGNORECASE))
                for priority, category, pattern in CAT_RULES]
    df = df.copy()
    df["part_category"] = [categorise(d, compiled)
                           for d in df.get("item_desc", pd.Series("", index=df.index))]
    return df


def backtest(df: pd.DataFrame) -> pd.DataFrame:
    price = _n(df, "unitprice").fillna(0.0).to_numpy()
    bench = np.c_[_n(df, "_final_max"), _n(df, "_final_rop")].astype(float)
    gradable = ~np.isnan(bench).any(axis=1)
    month = df["_month"].astype(str).to_numpy()

    # Routing is a property of the demand data, not of the rules, so the dormant
    # mask is taken once from the untouched engine and reused for every arm.
    dormant = (E.run(df, {})["route"] == "dormant").to_numpy()

    rows = []
    for arm, rules in ARMS.items():
        out = E.run(df, {"dormant_rules": rules})
        engine = _pair(out)
        agree = _matched(engine, bench)
        applied = out.reason_code.str.contains("DORMANT_RULE_APPLIED").to_numpy()
        book = price * engine[:, 0]
        for m in sorted(set(month)):
            sel = (month == m) & dormant
            gr = sel & gradable
            rows.append({
                "arm": arm, "cycle": m,
                "dormant_rows": int(sel.sum()),
                "rule_applied": int(applied[sel].sum()),
                "gradable": int(gr.sum()),
                "agreed": int(agree[gr].sum()),
                "agreement_pct": round(100 * agree[gr].mean(), 1) if gr.any() else 0.0,
                "book_usd": round(float(book[sel].sum()), 2),
            })
    return pd.DataFrame(rows)


def report(table: pd.DataFrame) -> None:
    base = table[table.arm == "engine_only"].set_index("cycle")
    for arm in ARMS:
        part = table[table.arm == arm].set_index("cycle")
        print(f"\n=== {arm} ===")
        print(f"{'cycle':<10}{'dormant':>9}{'applied':>9}{'agree%':>9}"
              f"{'d-agree':>9}{'book $':>14}{'d-book $':>14}")
        for cycle in part.index:
            r, b = part.loc[cycle], base.loc[cycle]
            print(f"{cycle:<10}{r.dormant_rows:>9,}{r.rule_applied:>9,}"
                  f"{r.agreement_pct:>9.1f}{r.agreement_pct - b.agreement_pct:>+9.1f}"
                  f"{r.book_usd:>14,.0f}{r.book_usd - b.book_usd:>+14,.0f}")
        arm_agree = 100 * part.agreed.sum() / max(part.gradable.sum(), 1)
        base_agree = 100 * base.agreed.sum() / max(base.gradable.sum(), 1)
        print(f"{'TOTAL':<10}{part.dormant_rows.sum():>9,}{part.rule_applied.sum():>9,}"
              f"{arm_agree:>9.1f}{arm_agree - base_agree:>+9.1f}"
              f"{part.book_usd.sum():>14,.0f}"
              f"{part.book_usd.sum() - base.book_usd.sum():>+14,.0f}")


def _selfcheck() -> None:
    """Pin resolution order, the decline cases, and that no rules is a no-op."""
    rules = [_rule("default"),
             _rule("category", "filter", policy="fixed_qty", qty=2),
             _rule("item", "X", policy="fixed_qty", qty=9)]
    assert DR.resolve(rules, "X", "filter", "m")["fixed_qty"] == 9
    assert DR.resolve(rules, "Y", "filter", "m")["fixed_qty"] == 2
    assert DR.resolve(rules, "Y", "cable", "m")["scope"] == "default"
    assert DR.resolve([_rule("category", "filter", crit="h")], "Y", "filter", "m") is None

    assert DR.apply(_rule("default", policy="hold_current"), 7) == (7, 7, 7)
    assert DR.apply(_rule("default", policy="hold_current"), float("nan")) is None
    assert DR.apply(_rule("default", policy="fixed_qty", qty=None), 7) is None
    assert DR.apply(_rule("default", policy="zero"), 7) == (0, 0, 0)

    # The property the whole layer rests on: no rules == today's engine, exactly.
    row = {"item_id": "X", "item_desc": "FILTER,ASSY", "part_category": "filter",
           "sfm_criticality": "Medium", "max_qty": 4, "unitprice": 1.0,
           "contractual_lead_time": 30, "order_qty_multiple": 1,
           **{E.CONS[w]: 0 for w in E.WINDOWS}}
    df = pd.DataFrame([row])
    bare, empty = E.run(df), E.run(df, {"dormant_rules": []})
    assert bare.equals(empty), "an empty rule set changed the engine's output"
    assert bare.factory_recommended_new_max.iloc[0] == 0
    held = E.run(df, {"dormant_rules": [_rule("default")]})
    assert held.factory_recommended_new_max.iloc[0] == 4
    assert "DORMANT_RULE_APPLIED" in held.reason_code.iloc[0]
    print("selfcheck ok")


def main() -> None:
    _selfcheck()
    if not PKL.exists():
        print(f"\n{PKL} not found -- run analysis/s20_diverge_features.py first "
              f"(it needs DB access). The selfcheck above still ran.")
        return
    table = backtest(_prepare(pd.read_pickle(PKL)))
    report(table)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT, index=False)
    print(f"\nwrote {OUT}")
    print("\nThe book-value column is not a footnote. A rule set that raises "
          "agreement\nwhile adding inventory is a trade, and the owner makes it: "
          "confirm nothing in\ndormant_rule_config until this table has been read.")


if __name__ == "__main__":
    main()
