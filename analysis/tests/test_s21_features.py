"""S21 confidence-model guard rails.

Two properties are the point of this file and get explicit tests rather than
being implied by a metric looking reasonable:

  1. the target IS engine_statistical's own agreement rule, not a copy of it
  2. the prior-cycle feature cannot see the row it describes (leakage)

Everything else here is the PRD 5.1 governance check and the numerics.
Deliberately NOT in backend/tests: that suite must keep passing without the
analysis stack, and it never imports from analysis/.
"""

import numpy as np
import pandas as pd

import s21_confidence_features as F
from app import engine_statistical as E  # F put backend/ on sys.path
from common import MEMORY_COLS, OUTPUT_COLS

# The scorecard cases, one per boundary: exact / 10% is inclusive / both zero /
# ROP alone breaks the row.
BENCH = np.array([[2.0, 2.0], [10.0, 10.0], [0.0, 0.0], [5.0, 5.0]])
HIT = np.array([[2.0, 2.0], [11.0, 11.0], [0.0, 0.0], [5.0, 5.0]])
# 2 admits only 2 / 12 is 2 off a base of 10 / 0 admits only 0 / ROP differs
MISS = np.array([[1.0, 2.0], [12.0, 10.0], [1.0, 0.0], [5.0, 7.0]])


def test_target_matches_engine():
    """The target must be the engine's rule, row for row -- if _agreement ever
    changes, this fails instead of the model silently training on a stale one."""
    assert list(F._matched(HIT, BENCH)) == [True] * 4
    assert list(F._matched(MISS, BENCH)) == [False] * 4
    for cand, want in ((HIT, "match"), (MISS, "diverge")):
        for e, b in zip(cand, BENCH):
            assert E._agreement((e[0], e[1], 0), (b[0], b[1], 999)) == want


def test_no_leakage_in_prior_features():
    """Cycle 1 is cold-start; cycle 2 is graded against cycle 1's decision and
    never against its own. A one-pass history would make gap 0 on both rows."""
    two = pd.DataFrame([F.synthetic_row("2024-01", "A", 40, 7, 7),
                        F.synthetic_row("2024-02", "A", 40, 99, 99)])
    X, _, _, month = F.build(two)

    assert X.has_prior.tolist() == [0.0, 1.0]
    assert X.gap_prior_decision.iloc[0] == F.COLD_START_GAP
    assert list(month) == ["2024-01", "2024-02"]

    engine_max = F._pair(E.run(two))[:, 0]
    assert np.isclose(X.gap_prior_decision.iloc[1], abs(engine_max[1] - 7) / 7)
    # ...and specifically NOT its own final_max of 99.
    assert not np.isclose(X.gap_prior_decision.iloc[1], abs(engine_max[1] - 99) / 99)


def test_prior_zero_max_does_not_divide_by_zero():
    """An engineer who zeroed a part last cycle is a real case; the gap must
    stay finite rather than becoming inf and poisoning the fit."""
    two = pd.DataFrame([F.synthetic_row("2024-01", "A", 40, 0, 0),
                        F.synthetic_row("2024-02", "A", 40, 3, 3)])
    X, _, _, _ = F.build(two)
    assert X.has_prior.tolist() == [0.0, 1.0]
    assert np.isfinite(X.gap_prior_decision).all()


def test_forbidden_columns_absent():
    """PRD 5.1: no engine OUTPUT and no memory/decision-history column of the
    current cycle may be an ML input."""
    assert not set(F.FEATURES) & set(OUTPUT_COLS)
    assert not set(F.FEATURES) & set(MEMORY_COLS)
    assert len(set(F.FEATURES)) == len(F.FEATURES)


def test_module_selfcheck():
    """The module's own self-check: tolerance pinning, the leakage assertion,
    and the numerics (coefficient signs, logaddexp overflow, AUC, ECE)."""
    F._selfcheck()
