"""Composite score."""

import math

import pandas as pd
import pytest

from helpers import config
from investment_agent.strategies.higgons import rank

CFG = config()
WEIGHTS = CFG["score"]["weights"]


def stocks(rows):
    """rows: (tv_ticker, perf, pe, roe, cagr, nd/ebitda, eligible)."""
    columns = ["tv_ticker", "perf_6m", "pe_ttm", "roe_ttm", "revenue_cagr_5y", "net_debt_ebitda", "eligible"]
    return pd.DataFrame(rows, columns=columns)


def test_single_eligible_stock_scores_100():
    res = rank(stocks([("NYSE:A", 5, 8, 15, 7, 1.0, True)]), CFG)
    assert res["score"].iloc[0] == pytest.approx(100)
    assert res["rank"].tolist() == [1]


def test_score_is_the_exact_weighted_sum():
    df = stocks([
        ("NYSE:A", 30, 5, 10, 5, 2.0, True),    # momentum 100, value 100, quality 0, growth 0, balance sheet 0
        ("NYSE:B", 20, 8, 20, 10, 1.0, True),   # 50, 50, 50, 50, 50
        ("NYSE:C", 10, 11, 30, 15, 0.0, True),  # 0, 0, 100, 100, 100
    ])
    res = rank(df, CFG).set_index("tv_ticker")
    expected_a = 100 * (WEIGHTS["momentum"] + WEIGHTS["value"])
    expected_c = 100 * (WEIGHTS["quality"] + WEIGHTS["growth"] + WEIGHTS["balance_sheet"])
    assert res.loc["NYSE:A", "score"] == pytest.approx(expected_a)      # 55
    assert res.loc["NYSE:B", "score"] == pytest.approx(50)
    assert res.loc["NYSE:C", "score"] == pytest.approx(expected_c)      # 45
    assert res.loc["NYSE:A", "pct_value"] == 100 and res.loc["NYSE:C", "pct_balance_sheet"] == 100
    assert list(res.index) == ["NYSE:A", "NYSE:B", "NYSE:C"]
    assert res["rank"].tolist() == [1, 2, 3]


def test_equal_scores_ordered_by_identifier():
    df = stocks([
        ("NYSE:ZZZ", 10, 8, 15, 7, 1.0, True),
        ("NASDAQ:AAA", 10, 8, 15, 7, 1.0, True),
        ("NYSE:MMM", 10, 8, 15, 7, 1.0, True),
    ])
    res = rank(df, CFG)
    assert res["score"].nunique() == 1
    assert res["tv_ticker"].tolist() == ["NASDAQ:AAA", "NYSE:MMM", "NYSE:ZZZ"]


def test_non_eligible_stock_ignored():
    df = stocks([
        ("NYSE:A", 30, 5, 10, 5, 2.0, True),
        ("NYSE:B", 20, 8, 20, 10, 1.0, True),
        ("NYSE:X", 99, 1, 99, 99, -9.0, False),  # best everywhere, but not eligible
    ])
    res = rank(df, CFG).set_index("tv_ticker")
    assert "NYSE:X" not in res.index
    assert res.loc["NYSE:A", "pct_momentum"] == 100 and res.loc["NYSE:B", "pct_momentum"] == 0


def test_no_eligible_stock():
    res = rank(stocks([("NYSE:X", 1, 1, 1, 1, 1.0, False)]), CFG)
    assert res.empty and "score" in res.columns


def test_component_without_a_measure():
    cfg = config()
    cfg["score"]["weights"]["unknown"] = 0.0
    cfg["score"]["direction"]["unknown"] = "high"
    with pytest.raises(ValueError, match="unknown"):
        rank(stocks([("NYSE:A", 1, 1, 1, 1, 1.0, True)]), cfg)


def test_config_weights_sum_to_1():
    assert math.isclose(math.fsum(WEIGHTS.values()), 1)
