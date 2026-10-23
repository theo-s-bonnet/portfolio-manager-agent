"""Selection criteria and data guards computed on TradingView data."""

import pandas as pd

from helpers import DATE, RATES, config, tv_stock, universe
from investment_agent.strategies.higgons import filter_tv
from investment_agent.strategies.higgons.filters import RULE_COLUMNS

CFG = config()
F = CFG["filters"]
nan = float("nan")


def tv(**changes):
    df = filter_tv(universe(tv_stock(**changes)), CFG, RATES, DATE)
    return df.iloc[0]


def warnings(row, start):
    return [text for category, text in row["warnings"] if category.startswith(start)]


def test_reference_stock_passes_every_rule():
    row = tv()
    assert {r: row[r] for r in RULE_COLUMNS} == {r: "pass" for r in RULE_COLUMNS}
    assert (row["pe_retained"], row["pe_source"]) == (10.0, "calc")    # 1e9 / 1e8 > P/E 8


def test_first_bar_date_from_unix_time():
    row = tv(first_bar_time=1_746_057_600)                # 2025-05-01 00:00 UTC
    assert row["first_bar_date"] == pd.Timestamp("2025-05-01")


# --- R4.1 retained P/E -----------------------------------------------------------------------

def test_pe_equal_to_the_maximum():
    assert tv(pe_ttm=12.0, market_cap=1.2e9)["R4.1"] == "pass"            # pe_calc 12
    assert tv(pe_ttm=F["pe_max"] + 0.01, market_cap=1.2e9)["R4.1"] == "fail"


def test_negative_pe():
    row = tv(pe_ttm=-5.0, eps_ttm=-1.0)
    assert row["R4.1"] == "fail"
    assert row["reason_R4.1"] == "EPS -1 ≤ 0: P/E undefined"


def test_pe_missing_and_negative_eps():
    row = tv(pe_ttm=nan, eps_ttm=-0.5)
    assert row["R4.1"] == "fail"
    assert row["R4.12"] == "fail"
    assert "EPS" in row["reason_R4.12"]


def test_zero_net_income():
    row = tv(net_income_ttm=0.0, eps_ttm=0.1)
    assert row["R4.1"] == "fail"
    assert row["reason_R4.1"] == "TTM net income 0 ≤ 0: P/E undefined"


def test_pe_fallback_when_tradingview_pe_missing():
    row = tv(pe_ttm=nan, market_cap=1e9, net_income_ttm=1e8)
    assert row["R4.1"] == "pass"
    assert (row["pe_retained"], row["pe_source"]) == (10.0, "fallback")
    assert row["reason_R4.1"] == "fallback: TradingView P/E missing, market cap / TTM net income = 10"
    assert warnings(row, "Retained P/E") == ["retained P/E 10"]


def test_pe_missing_and_zero_eps():
    row = tv(pe_ttm=nan, eps_ttm=0.0)
    assert row["R4.1"] == "fail" and row["reason_R4.1"].startswith("EPS 0 ≤ 0")
    assert row["R4.12"] == "fail"
    assert pd.isna(row["pe_retained"]) and pd.isna(row["pe_source"])


def test_calc_higher_than_tradingview_pe():
    row = tv(pe_ttm=8.0, market_cap=1.1e9)                                # pe_calc 11
    assert (row["pe_retained"], row["pe_source"], row["R4.1"]) == (11.0, "calc", "pass")
    assert row["reason_R4.1"] == "market cap / TTM net income 11 > TradingView P/E 8"


def test_tradingview_pe_higher_than_calc():
    row = tv(pe_ttm=11.0, market_cap=8e8)                                 # pe_calc 8
    assert (row["pe_retained"], row["pe_source"], row["R4.1"]) == (11.0, "tv", "pass")


def test_the_higher_pe_wins():
    row = tv(pe_ttm=10.0, market_cap=1.25e9)                              # pe_calc 12.5
    assert row["R4.1"] == "fail"
    assert row["reason_R4.1"] == "retained P/E 12.5 > 12 (calc)"


def test_pe_and_net_income_missing():
    row = tv(pe_ttm=nan, net_income_ttm=nan)
    assert row["R4.1"] == "missing"
    assert row["reason_R4.1"] == "TradingView P/E and market cap / TTM net income missing"


def test_no_reverse_fallback_when_net_income_missing():
    row = tv(pe_ttm=9.0, net_income_ttm=nan)
    assert row["R4.1"] == "missing"
    assert row["reason_R4.1"] == "market cap / TTM net income missing: the retained P/E needs both"
    assert pd.isna(row["pe_retained"])


def test_negative_tradingview_pe_with_positive_eps():
    row = tv(pe_ttm=-3.0, eps_ttm=0.5)
    assert row["R4.1"] == "fail"
    assert row["reason_R4.1"] == "TradingView P/E -3 ≤ 0"


def test_equal_pe_within_the_tolerance():
    row = tv(pe_ttm=8.0, market_cap=(8.0 + 1e-12) * 1e8)
    assert row["pe_source"] == "tv" and row["pe_retained"] == 8.0


def test_pe_calc_in_the_reporting_currency():
    row = tv(tv_ticker="OSL:TEST", market="norway", exchange="OSL", country="Norway", currency="NOK",
             reporting_currency="NOK", market_cap=5e9, net_income_ttm=5e8, pe_ttm=9.0)
    assert row["pe_calc"] == 10.0                     # NOK / NOK, no conversion
    assert row["market_cap_eur"] == 5e9 * RATES["NOK"]


def test_failure_reason_with_measured_value():
    assert tv(pe_ttm=14.25)["reason_R4.1"] == "retained P/E 14.25 > 12 (tv)"


# --- other criteria -------------------------------------------------------------------------

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
    assert row["R4.12"] == "fail"
    assert pd.isna(row["net_debt_ebitda"])
    assert row["R4.4"] == "fail" and "not computed" in row["reason_R4.4"]


def test_negative_ebitda_misleading_positive_ratio():
    row = tv(net_debt=-50.0, ebitda_ttm=-10.0)
    assert row["R4.12"] == "fail"
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


def test_roe_nan_never_replaced_by_zero():
    row = tv(roe_ttm=nan)
    assert pd.isna(row["roe_ttm"])


def test_net_debt_missing():
    row = tv(net_debt=None)
    assert row["R4.4"] == "missing" and row["reason_R4.4"] == "missing: net debt"
