"""The verdict truth table, and the properties that make the verdict worth having.

Two of these are the whole reason the layer is split in half. `evaluate()` is a
pure function, so the same evidence gives the same verdict every time and the
layer can be backtested against real decisions -- a generative model can give
neither. And an unmatched case defaults to `flag_for_review`, never to
`bulk_accept_candidate`: a needless human minute against an unreviewed stock
change is not a close call.
"""

from pathlib import Path

import pytest

from app.assist import rules
from app.engine_statistical import AGREE_TOL


def ev(**kw):
    """Evidence with two clean matching cycles -- the accept shape, by default."""
    base = {"n_cycles": 2, "diverge_streak": 0,
            "agreements": ["match", "match"], "justifications": ["", ""],
            "engine_max": 10, "last_final_max": 10, "exposure_usd": 100.0,
            "criticality": "m", "peer_override_rate": 0.0, "open_notes": 0}
    return {**base, **kw}


def verdict(**kw) -> str:
    return rules.evaluate(ev(**kw))["verdict"]


# --- the three verdicts ----------------------------------------------------

def test_cold_start_is_needs_context():
    """29% of live rows have no prior cycle. 'Nobody has looked at this' is a
    different instruction to a reviewer than 'this one looks wrong'."""
    out = rules.evaluate(ev(n_cycles=0, agreements=[]))
    assert out["verdict"] == "needs_context"
    assert out["reasons"] == ["NO_PRIOR_CYCLE"]


def test_clean_history_is_a_bulk_accept_candidate():
    out = rules.evaluate(ev())
    assert out["verdict"] == "bulk_accept_candidate"
    assert "MATCHED_LAST_CYCLES" in out["reasons"]


def test_unmatched_evidence_defaults_to_flag():
    """The safety default. An empty dict must never reach a pre-ticked box."""
    assert rules.evaluate({"n_cycles": 1})["verdict"] == "flag_for_review"


# --- every flag condition, both directions ---------------------------------

@pytest.mark.parametrize("kw,reason", [
    ({"diverge_streak": 2}, "DIVERGE_STREAK"),
    ({"justifications": ["bulk withdraw for the Q3 project", ""]},
     "BULK_WITHDRAW_HISTORY"),
    ({"exposure_usd": 5000.0}, "HIGH_EXPOSURE"),
    ({"criticality": "High"}, "CRITICAL_PART"),
    ({"peer_override_rate": 0.5}, "PEERS_OVERRIDDEN"),
    ({"open_notes": 1}, "OPEN_ITEM_NOTE"),
])
def test_each_flag_condition_fires(kw, reason):
    out = rules.evaluate(ev(**kw))
    assert out["verdict"] == "flag_for_review"
    assert reason in out["reasons"]


@pytest.mark.parametrize("kw", [
    {"diverge_streak": 1},          # one override is noise, not a pattern
    {"exposure_usd": 4999.99},      # the gate is inclusive at the threshold
    {"criticality": "Medium"},
    {"peer_override_rate": 0.49},
])
def test_just_below_each_flag_threshold_still_accepts(kw):
    assert verdict(**kw) == "bulk_accept_candidate"


def test_one_matching_cycle_is_not_enough():
    assert verdict(n_cycles=1, agreements=["match"]) == "flag_for_review"


def test_engine_moving_away_from_the_last_accepted_value_flags():
    """Acceptance falls from 58.3% to 23.3% as this gap grows past 50%."""
    inside = 10 * (1 + AGREE_TOL)
    assert verdict(engine_max=inside) == "bulk_accept_candidate"
    out = rules.evaluate(ev(engine_max=20))
    assert out["verdict"] == "flag_for_review"
    assert "ENGINE_MOVED_FROM_PRIOR_ACCEPTED" in out["reasons"]


def test_no_prior_accepted_value_flags_rather_than_accepts():
    out = rules.evaluate(ev(last_final_max=None))
    assert out["verdict"] == "flag_for_review"
    assert "NO_PRIOR_ACCEPTED_VALUE" in out["reasons"]


def test_a_flag_condition_beats_a_clean_accept_history():
    """Order is the contract: flags are checked before accepts, never after."""
    assert verdict(diverge_streak=3) == "flag_for_review"


# --- the properties --------------------------------------------------------

def test_verdict_is_deterministic():
    evidence = ev(diverge_streak=1, peer_override_rate=0.3)
    seen = {rules.evaluate(dict(evidence))["verdict"] for _ in range(100)}
    assert len(seen) == 1


def test_thresholds_are_configurable_not_hardcoded():
    strict = rules.evaluate(ev(diverge_streak=1),
                            {"assist_diverge_streak_flag": 1})
    assert strict["verdict"] == "flag_for_review"


def test_agree_tol_is_imported_not_copied():
    """One tolerance in the system. A second literal 0.10 here would drift from
    the engine's the first time the engine's is recalibrated."""
    source = Path(rules.__file__).read_text(encoding="utf-8")
    assert "0.10" not in source
    assert "AGREE_TOL" in source


def test_every_verdict_returned_is_a_declared_verdict():
    for kw in ({"n_cycles": 0}, {}, {"diverge_streak": 5}, {"criticality": "h"}):
        assert rules.evaluate(ev(**kw))["verdict"] in rules.VERDICTS


def test_garbage_inputs_do_not_raise():
    """The evidence comes from tools that return EMPTY on a miss, so half the
    keys are routinely absent or None."""
    for junk in ({}, {"n_cycles": None}, {"n_cycles": "3", "exposure_usd": "x"},
                 {"n_cycles": float("nan")}):
        assert rules.evaluate(junk)["verdict"] in rules.VERDICTS
