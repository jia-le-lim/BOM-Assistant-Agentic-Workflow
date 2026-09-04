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

# Resolution order, most specific first. This tuple IS the contract: an item
# rule always beats a category rule, which always beats the default. There is
# no priority number and no criticality filter -- (scope, match_key) is unique,
# so at most one rule can match at each tier and the winner is never ambiguous.
SCOPES = ("item", "category", "default")

# Seeded on first read. Exactly one row: the measured median kept quantity is 1
# and the modal decision is "leave it where it is", so hold_current is the
# honest default. Category rules are NOT seeded -- inventing thresholds nobody
# measured is how a rule table stops meaning anything.
DEFAULT_RULES: list[tuple[str, str, str, int | None]] = [
    # (scope, match_key, policy, fixed_qty)
    ("default", "", "hold_current", None),
]


def load_rules(conn) -> list[dict]:
    """Confirmed rules. The only DB-touching function here.

    `WHERE confirmed=1` is the safety property, not a nicety: an unconfirmed
    proposal must not move a stock level.
    """
    return [dict(r) for r in conn.execute(
        "SELECT rule_id, scope, match_key, policy, fixed_qty, updated_at "
        "FROM dormant_rule_config WHERE confirmed=1")]


def seed(conn) -> int:
    """Insert DEFAULT_RULES unconfirmed if the table is empty; returns rows added.

    Unconfirmed on purpose. Q2 makes this a stock-moving change, so even the
    default has to be looked at by a human before it sizes anything.
    """
    if conn.execute("SELECT 1 FROM dormant_rule_config LIMIT 1").fetchone():
        return 0
    for scope, key, policy, qty in DEFAULT_RULES:
        conn.execute(
            "INSERT INTO dormant_rule_config (scope, match_key, policy, "
            "fixed_qty, set_by, confirmed) VALUES (?,?,?,?,'seed',0)",
            (scope, key, policy, qty))
    return len(DEFAULT_RULES)


def resolve(rules, item_id, category) -> dict | None:
    """Item rule, else category rule, else default. None = engine keeps its answer.

    Takes plain data, never a connection: this is what lets engine_statistical
    stay database-free and re-runnable offline over history.

    An empty `category` never matches a category rule -- an uncategorised part
    is not silently every category.
    """
    by = {(str(r.get("scope") or ""), str(r.get("match_key") or "")): r
          for r in rules}
    item, cat = str(item_id or ""), str(category or "")
    return (by.get(("item", item)) if item else None) \
        or (by.get(("category", cat)) if cat else None) \
        or by.get(("default", ""))


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
