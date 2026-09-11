import pandas as pd
import pytest

from s28_pooled_months import COUNT_METRICS, aggregate_scores


def score(cycle, n, hits, mae):
    row = {column: 0 for column in COUNT_METRICS}
    row.update(candidate="test", segment="live", cycle=cycle, n=n,
               matches=hits, match_pct=100 * hits / n, max_mae=mae, rop_mae=mae)
    return row


def test_pool_weights_rows_instead_of_averaging_month_percentages():
    result = aggregate_scores(pd.DataFrame([
        score("2026-01", 10, 9, 2), score("2026-03", 90, 9, 4),
    ])).iloc[0]
    assert result.n == 100
    assert result.matches == 18
    assert result.match_pct == 18
    assert result.equal_cycle_match_pct == 50
    assert result.max_mae == pytest.approx(3.8)


def test_duplicate_month_score_is_rejected_instead_of_double_counted():
    row = score("2026-01", 10, 9, 2)
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_scores(pd.DataFrame([row, row]))
