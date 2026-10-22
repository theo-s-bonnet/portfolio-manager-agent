"""Tests of engine/finance.py: denominators, splits and percentile ranks."""

import math

import pandas as pd
import pytest

from investment_agent.engine.finance import adjust_for_splits, percentile_rank, safe_ratio

D_Y2 = "2023-12-31"
D_Y = "2025-12-31"


def splits(*pairs):
    """Series in yfinance format (date index with a time zone)."""
    index = pd.DatetimeIndex([d for d, _ in pairs]).tz_localize("America/New_York")
    return pd.Series([r for _, r in pairs], index=index, dtype=float)


# --- safe_ratio -----------------------------------------------------------------------

@pytest.mark.parametrize("num, den, expected", [
    (30, 10, 3.0),
    (-50, 10, -5.0),
    (50, 0, None),
    (-50, -10, None),          # misleading positive ratio: negative denominator
    (50, float("nan"), None),
    (None, 10, None),
])
def test_safe_ratio(num, den, expected):
    assert safe_ratio(num, den) == expected


# --- adjust_for_splits ----------------------------------------------------------------

def test_2_for_1_split_not_adjusted_by_yahoo():
    n, warning = adjust_for_splits(100, D_Y2, 202, D_Y, splits(("2024-06-10", 2.0)))
    assert n == pytest.approx(200)
    assert warning == "split detected (ratio 2, adjusted: yes)"
    assert (202 / n) ** (1 / 2) - 1 == pytest.approx(0.004988, abs=1e-6)   # +0.5%/yr


def test_2_for_1_split_already_adjusted():
    n, warning = adjust_for_splits(200, D_Y2, 202, D_Y, splits(("2024-06-10", 2.0)))
    assert n == 200
    assert warning == "split detected (ratio 2, adjusted: no)"


def test_1_for_10_reverse_split_not_adjusted():
    n, warning = adjust_for_splits(1000, D_Y2, 101, D_Y, splits(("2024-06-10", 0.1)))
    assert n == pytest.approx(100)
    assert "adjusted: yes" in warning


def test_no_split_in_the_period():
    s = splits(("2023-12-31", 2.0), ("2026-03-01", 3.0))    # bounds: start excluded, after the end
    assert adjust_for_splits(100, D_Y2, 110, D_Y, s) == (100, None)
    assert adjust_for_splits(100, D_Y2, 110, D_Y, pd.Series(dtype=float)) == (100, None)
    assert adjust_for_splits(100, D_Y2, 110, D_Y, None) == (100, None)


def test_split_on_the_recent_date_included():
    n, _ = adjust_for_splits(100, D_Y2, 200, D_Y, splits((D_Y, 2.0)))
    assert n == pytest.approx(200)


def test_successive_splits_multiplied():
    n, warning = adjust_for_splits(100, D_Y2, 600, D_Y, splits(("2024-03-01", 2.0), ("2025-03-01", 3.0)))
    assert n == pytest.approx(600)
    assert "ratio 6" in warning


@pytest.mark.parametrize("n_old, n_recent", [(None, 100), (100, float("nan")), (0, 100)])
def test_invalid_share_count(n_old, n_recent):
    assert adjust_for_splits(n_old, D_Y2, n_recent, D_Y, splits(("2024-06-10", 2.0))) == (None, None)


# --- percentile_rank ------------------------------------------------------------------

def test_single_eligible_stock():
    weights = {"momentum": 0.30, "value": 0.25, "quality": 0.20, "growth": 0.15, "balance_sheet": 0.10}
    direction = {"momentum": "high", "value": "low", "quality": "high", "growth": "high", "balance_sheet": "low"}
    pcts = {c: percentile_rank(pd.Series([7.0]), direction[c]).iloc[0] for c in weights}
    assert all(p == 100 for p in pcts.values())
    assert math.fsum(weights[c] * pcts[c] for c in weights) == pytest.approx(100)


def test_pe_low_is_better():
    assert percentile_rank(pd.Series([5.0, 8.0, 11.0]), "low").tolist() == [100, 50, 0]


def test_ties_high_is_better():
    assert percentile_rank(pd.Series([10.0, 10.0, 30.0]), "high").tolist() == [25, 25, 100]


def test_missing_value_ignored():
    pct = percentile_rank(pd.Series([1.0, None, 3.0]), "high")
    assert pct.iloc[0] == 0 and pct.iloc[2] == 100 and math.isnan(pct.iloc[1])


def test_unknown_direction():
    with pytest.raises(ValueError):
        percentile_rank(pd.Series([1.0]), "middle")
