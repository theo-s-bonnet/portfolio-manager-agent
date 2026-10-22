"""Yahoo fundamentals (R4.7, R4.9, R4.10a, R4.10b, R4.12)."""

import pandas as pd
import pytest

from helpers import DATE, config, statement, yahoo_data
from investment_agent.strategies.higgons import extract_yahoo, filter_yahoo
from investment_agent.strategies.higgons.fundamentals import (
    CATEGORY_SHARE_RATIO, CATEGORY_SPLIT, CATEGORY_TOTAL_DEBT)

CFG = config()
Y, Y1, Y2 = "2025-12-31", "2024-12-31", "2023-12-31"
ABSENT = object()


def data(debt=(50.0, 50.0), cash=(0.0, 0.0), equity=(100.0, 100.0), n=(100.0, 100.0),
         shares_y=100.0, current=100.0, splits=None, net_debt=None):
    """Fake stock; tuples (Y, Y-2). ABSENT removes the row from the column."""
    def column(i):
        c = {"Total Debt": debt[i], "Cash And Cash Equivalents": cash[i], "Common Stock Equity": equity[i]}
        if i == 0:
            c["Ordinary Shares Number"] = shares_y
        if net_debt is not None:
            c["Net Debt"] = net_debt[i]
        return {k: v for k, v in c.items() if v is not ABSENT}

    b = statement({Y: column(0), Y1: {"Common Stock Equity": 1.0}, Y2: column(1)})
    r = statement({Y: {"Diluted Average Shares": n[0]}, Y1: {"Diluted Average Shares": 1.0},
                   Y2: {"Diluted Average Shares": n[1]}})
    r = r.drop(index=[l for l in r.index if (r.loc[l] != r.loc[l]).all()])
    if n[1] is ABSENT:
        r.loc["Diluted Average Shares", pd.Timestamp(Y2)] = float("nan")
    return yahoo_data(balance_sheet=b, income_statement=r, shares_outstanding=current,
                      splits=splits if splits is not None else pd.Series(dtype=float))


def evaluate(d):
    row = extract_yahoo(d, DATE, CFG)
    return filter_yahoo(pd.DataFrame([row]), CFG, DATE).iloc[0]


def test_complete_data_passes_everything():
    row = evaluate(yahoo_data())
    for rule in ("R3.6", "R4.7", "R4.9", "R4.10a", "R4.10b", "R4.12_yf"):
        assert row[rule] == "pass", (rule, row[f"reason_{rule}"])


def test_net_cash_in_y_and_y2():
    row = evaluate(data(debt=(0.0, 5.0), cash=(10.0, 10.0)))
    assert row["yf_net_debt_y"] == -10 and row["yf_net_debt_y2"] == -5
    assert row["R4.7"] == "pass"


def test_decrease_of_exactly_10_points():
    row = evaluate(data(debt=(40.0, 50.0)))          # 50% -> 40%
    assert row["R4.7"] == "pass"


def test_decrease_of_9_9_points():
    row = evaluate(data(debt=(40.1, 50.0)))
    assert row["R4.7"] == "fail"
    assert "-9.9 points" in row["reason_R4.7"]


def test_negative_equity_in_y2():
    row = evaluate(data(equity=(100.0, -20.0)))
    assert row["R4.12_yf"] == "fail"
    assert row["R4.7"] == "fail" and "not computed" in row["reason_R4.7"]
    assert row["R4.9"] == "fail"


def test_low_but_positive_equity():
    # ratio 5,000% -> 50%: consistent with the written rule (very low equity inflates the ratio)
    row = evaluate(data(debt=(50.0, 50.0), equity=(100.0, 1.0)))
    assert row["R4.7"] == "pass"
    assert "5,000% → 50%" in row["reason_R4.7"]


def test_total_debt_missing():
    row = evaluate(data(debt=(ABSENT, 50.0)))
    assert row["R4.7"] == "missing"
    assert row["reason_R4.7"] == "missing: total debt Y"
    assert (CATEGORY_TOTAL_DEBT, "Total Debt missing (Y)") in row["warnings"]


def test_net_debt_row_ignored():
    row = evaluate(data(debt=(40.0, 50.0), net_debt=(999.0, 1.0)))
    assert row["yf_net_debt_y"] == 40.0 and row["R4.7"] == "pass"


def test_diluted_biotech():
    row = evaluate(data(n=(130.0, 100.0)))
    assert row["R4.10a"] == "fail"
    assert "14.02%/yr" in row["reason_R4.10a"]


def test_dilution_of_exactly_5_pct_per_year():
    assert evaluate(data(n=(110.25, 100.0)))["R4.10a"] == "pass"


def test_split_not_adjusted_by_yahoo():
    splits = pd.Series([2.0], index=pd.DatetimeIndex(["2024-06-15"], tz="America/New_York"))
    row = evaluate(data(n=(202.0, 100.0), equity=(150.0, 100.0), splits=splits))
    assert row["yf_diluted_shares_y2"] == 200.0
    assert row["R4.10a"] == "pass"                 # +0.5%/yr
    assert row["R4.9"] == "pass"                   # book value/share 0.50 -> 0.74 (unadjusted: 1.00 -> 0.74)
    assert (CATEGORY_SPLIT, "split detected (ratio 2, adjusted: yes), between Y-2 and Y") in row["warnings"]


def test_split_already_adjusted():
    splits = pd.Series([2.0], index=pd.DatetimeIndex(["2024-06-15"]))
    row = evaluate(data(n=(202.0, 200.0), splits=splits))
    assert row["yf_diluted_shares_y2"] == 200.0 and row["R4.10a"] == "pass"
    assert (CATEGORY_SPLIT, "split detected (ratio 2, adjusted: no), between Y-2 and Y") in row["warnings"]


def test_reverse_split_not_adjusted():
    splits = pd.Series([0.1], index=pd.DatetimeIndex(["2024-06-15"]))
    row = evaluate(data(n=(101.0, 1000.0), splits=splits))
    assert row["yf_diluted_shares_y2"] == pytest.approx(100.0)
    assert row["R4.10a"] == "pass"


def test_buybacks():
    assert evaluate(data(n=(90.0, 100.0)))["R4.10a"] == "pass"


def test_identical_book_value_per_share():
    assert evaluate(data(equity=(100.0, 100.0)))["R4.9"] == "fail"


def test_equity_growth_through_dilution():
    row = evaluate(data(equity=(110.0, 100.0), n=(120.0, 100.0)))
    assert row["R4.9"] == "fail"


def test_r4_9_real_growth():
    assert evaluate(data(equity=(110.0, 100.0)))["R4.9"] == "pass"


def test_current_shares_up_6_pct():
    assert evaluate(data(current=106.0))["R4.10b"] == "fail"


def test_current_shares_up_5_pct():
    assert evaluate(data(current=105.0))["R4.10b"] == "pass"


def test_suspect_ratio_multiple_share_classes():
    row = evaluate(data(current=40.0))
    assert row["R4.10b"] == "missing"
    assert "suspect" in row["reason_R4.10b"]
    assert any(cat == CATEGORY_SHARE_RATIO for cat, _ in row["warnings"])


def test_split_between_y_and_today():
    splits = pd.Series([2.0], index=pd.DatetimeIndex(["2026-03-02"]))
    row = evaluate(data(shares_y=100.0, current=202.0, splits=splits))
    assert row["yf_shares_y"] == 200.0 and row["R4.10b"] == "pass"


def test_diluted_shares_missing_in_y2():
    row = evaluate(data(n=(100.0, ABSENT)))
    assert row["R4.10a"] == "missing" and row["R4.9"] == "missing"


def test_income_statement_missing():
    row = evaluate(yahoo_data(income_statement=None))
    assert row["R4.10a"] == "missing" and row["R4.9"] == "missing"
    assert row["R3.6"] == "pass"


def test_current_shares_missing():
    row = evaluate(data(current=None))
    assert row["R4.10b"] == "missing"


def test_balance_sheet_missing():
    row = evaluate(yahoo_data(balance_sheet=None))
    assert row["R3.6"] == "fail"
    assert row["R4.7"] == "missing"
