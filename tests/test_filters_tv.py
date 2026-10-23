"""Eligibility filters computed on TradingView data."""

import pandas as pd

from helpers import DATE, RATES, config, tv_stock, universe
from investment_agent.strategies.higgons import filter_tv
from investment_agent.strategies.higgons.filters import TV_RULES

CFG = config()
F = CFG["filters"]


def tv(**changes):
    df = filter_tv(universe(tv_stock(**changes)), CFG, RATES, DATE)
    return df.iloc[0]


def test_reference_stock_passes_every_rule():
    row = tv()
    rules = [*TV_RULES, "R3.6"]
    assert {r: row[r] for r in rules} == {r: "pass" for r in rules}


def test_first_bar_date_from_unix_time():
    row = tv(first_bar_time=1_746_057_600)                # 2025-05-01 00:00 UTC
    assert row["first_bar_date"] == pd.Timestamp("2025-05-01")


def test_pe_equal_to_the_maximum():
    assert tv(pe_ttm=float(F["pe_ttm_max"]))["R4.1"] == "pass"
    assert tv(pe_ttm=F["pe_ttm_max"] + 0.01)["R4.1"] == "fail"


def test_negative_pe():
    assert tv(pe_ttm=-3.0)["R4.1"] == "fail"


def test_pe_missing_and_negative_eps():
    row = tv(pe_ttm=float("nan"), eps_ttm=-0.5)
    assert row["R4.12_tv"] == "fail"
    assert row["R4.1"] == "missing"
    assert "EPS" in row["reason_R4.12_tv"]


def test_roe_equal_to_the_minimum():
    assert tv(roe_ttm=float(F["roe_ttm_min_pct"]))["R4.2"] == "pass"


def test_cagr_equal_to_the_minimum():
    assert tv(revenue_cagr_5y=float(F["revenue_cagr_5y_min_pct"]))["R4.3"] == "pass"


def test_ratio_equal_to_the_strict_maximum():
    row = tv(net_debt=30.0, ebitda_ttm=10.0)
    assert row["net_debt_ebitda"] == 3.0 == F["net_debt_ebitda_max"]
    assert row["R4.4"] == "fail"


def test_net_cash():
    row = tv(net_debt=-50.0, ebitda_ttm=10.0)
    assert row["R4.4"] == "pass" and row["net_debt_ebitda"] == -5.0


def test_zero_ebitda():
    row = tv(net_debt=50.0, ebitda_ttm=0.0)
    assert row["R4.12_tv"] == "fail"
    assert pd.isna(row["net_debt_ebitda"])
    assert row["R4.4"] == "fail" and "not computed" in row["reason_R4.4"]


def test_negative_ebitda_misleading_positive_ratio():
    row = tv(net_debt=-50.0, ebitda_ttm=-10.0)
    assert row["R4.12_tv"] == "fail"
    assert row["R4.4"] == "fail"
    assert pd.isna(row["net_debt_ebitda"])


def test_price_equal_to_102_pct_of_the_ema_strict():
    ema = 50.0
    row = tv(ema=ema, price=F["ema_entry_pct"] / 100 * ema)
    assert row["R4.5"] == "fail"
    assert tv(ema=ema, price=F["ema_entry_pct"] / 100 * ema + 0.01)["R4.5"] == "pass"


def test_zero_6_month_performance():
    assert tv(perf_6m=0.0)["R4.6"] == "pass"
    assert tv(perf_6m=-0.1)["R4.6"] == "fail"


def test_zero_net_income():
    assert tv(net_income_ttm=0.0)["R4.8"] == "fail"


def test_roe_nan_never_replaced_by_zero():
    row = tv(roe_ttm=float("nan"))
    assert row["R4.2"] == "missing"
    assert row["reason_R4.2"] == "ROE missing"
    assert pd.isna(row["roe_ttm"])


def test_net_debt_missing():
    row = tv(net_debt=None)
    assert row["R4.4"] == "missing" and row["reason_R4.4"] == "missing: net debt"


def test_failure_reason_with_measured_value():
    assert tv(pe_ttm=14.25)["reason_R4.1"] == "P/E 14.25 > 12"
