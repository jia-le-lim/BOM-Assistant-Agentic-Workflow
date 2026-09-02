"""How much stock a DORMANT part keeps -- an engineer-owned rule, not the engine's zero.

The statistical engine sizes a part with no consumption in any window to zero
unless it is critical. Measured over eight review cycles (2024-07 -> 2026-01,
`analysis/output/s20_payloads.pkl`) that answer is overridden by hand on 1,344
of 8,343 dormant rows: these are wear-and-tear parts, and the engineer keeps a
constant quantity regardless of consumption. The engine cannot learn that from
demand data, because the whole point is that there is no demand signal.

So the quantity is a rule an engineer owns, on the same terms as
part_category_config: anyone with review rights may PROPOSE, a second person
with approval rights CONFIRMS, and the engine reads confirmed rows only.

Rules take effect at SCORE time, not review time -- an edited rule changes the
next `/run-recommendation`, never a batch that is already on screen.
"""

from __future__ import annotations

import math

MODEL_VERSION = "dormant-v1"

# hold_current  keep whatever the part is stocked at today (the engineer's
#               standing decision, 522 of the 1,344 overridden rows)
# fixed_qty     a named quantity for this scope
# zero          the engine's own answer, stated explicitly so a rule can
#               deliberately switch a category back off
POLICIES = ("hold_current", "fixed_qty", "zero")

SCOPES = ("item", "category", "default")

# Seeded on first read. Exactly one row: the measured median kept quantity is 1
# and the modal decision is "leave it where it is", so hold_current is the
# honest default. Category rules are NOT seeded -- inventing thresholds nobody
# measured is how a rule table stops meaning anything.
DEFAULT_RULES: list[tuple[int, str, str, str, str, int | None]] = [
    # (priority, scope, match_key, criticality, policy, fixed_qty)
    (900, "default", "", "", "hold_current", None),
]

# Lower priority wins, then the more specific scope. Ordering IS the contract:
# an item rule must beat a category rule at the same priority, or a per-part
# exception could never be written without renumbering the whole table.
_SCOPE_RANK = {"item": 0, "category": 1, "default": 2}


def load_rules(conn) -> list[dict]:
    """Confirmed rules in resolution order. The only DB-touching function here.

    `WHERE confirmed=1` is the safety property, not a nicety: an unconfirmed
    proposal must not move a stock level.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT rule_id, scope, match_key, criticality, policy, fixed_qty, "
        "priority, updated_at FROM dormant_rule_config WHERE confirmed=1")]
    return sorted(rows, key=_order)


def seed(conn) -> int:
    """Insert DEFAULT_RULES unconfirmed if the table is empty; returns rows added.

    Unconfirmed on purpose. Q2 makes this a stock-moving change, so even the
    default has to be looked at by a human before it sizes anything.
    """
    if conn.execute("SELECT 1 FROM dormant_rule_config LIMIT 1").fetchone():
        return 0
    for priority, scope, key, crit, policy, qty in DEFAULT_RULES:
        conn.execute(
            "INSERT INTO dormant_rule_config (scope, match_key, criticality, "
            "policy, fixed_qty, priority, set_by, confirmed) "
            "VALUES (?,?,?,?,?,?,'seed',0)",
            (scope, key, crit, policy, qty, priority))
    return len(DEFAULT_RULES)


def _order(rule: dict) -> tuple[int, int, str]:
    return (int(rule.get("priority") or 500),
            _SCOPE_RANK.get(str(rule.get("scope") or ""), 9),
            str(rule.get("match_key") or ""))


def _crit(value) -> str:
    """First letter, lowercased -- the same normalisation the engine's _sl uses."""
    return str(value or "").strip().lower()[:1]


def resolve(rules, item_id, category, criticality) -> dict | None:
    """First match wins; None when nothing matches and the engine keeps its own answer.

    Takes plain data, never a connection: this is what lets engine_statistical
    stay database-free and re-runnable offline over history.
    """
    crit = _crit(criticality)
    item = str(item_id or "")
    cat = str(category or "")
    for rule in sorted(rules, key=_order):
        want = _crit(rule.get("criticality"))
        if want and want != crit:
            continue
        scope = str(rule.get("scope") or "")
        key = str(rule.get("match_key") or "")
        if scope == "default":
            return rule
        if scope == "item" and key == item:
            return rule
        if scope == "category" and cat and key == cat:
            return rule
    return None


def apply(rule, current_max) -> tuple[int, int, int] | None:
    """(min, rop, max) the rule implies, or None to leave the engine alone.

    hold_current with no current level returns None rather than 0: a missing
    stock level is a gap in the extract, not a decision to stock nothing --
    the exact silent-zero this layer exists to stop.

    No MOQ rounding, unlike the engine's own branches. The quantity here is a
    number an engineer wrote down; rounding it up to an order multiple would
    quietly return something they did not ask for.
    """
    if not rule:
        return None
    policy = str(rule.get("policy") or "")
    if policy == "zero":
        return (0, 0, 0)
    if policy == "fixed_qty":
        qty = rule.get("fixed_qty")
        if qty is None:
            return None
        q = max(int(qty), 0)
        return (q, q, q)
    if policy == "hold_current":
        if current_max is None:
            return None
        try:
            value = float(current_max)
        except (TypeError, ValueError):
            return None
        if math.isnan(value) or value < 0:
            return None
        q = int(value)
        return (q, q, q)
    return None
