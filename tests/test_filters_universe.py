"""Universe filters R3.1 to R3.6."""

import pandas as pd
import pytest

from helpers import DATE, RATES, config, prices_since, statement, tv_stock, universe, yahoo_data
from investment_agent.strategies.higgons import extract_yahoo, filter_tv, filter_yahoo

CFG = config()
U = CFG["universe"]


def tv(*stocks, rates=RATES):
    return filter_tv(universe(*stocks), CFG, rates)


def status(df, rule, i=0):
    return df[rule].iloc[i]


def yahoo(data):
    row = extract_yahoo(data, DATE, CFG)
    return filter_yahoo(pd.DataFrame([row]), CFG, DATE)


def test_reference_stock_passes_r3_1_to_r3_5():
    df = tv(tv_stock())
    assert [status(df, r) for r in ("R3.1", "R3.2", "R3.3", "R3.4", "R3.5")] == ["pass"] * 5
    assert df["reason_R3.1"].iloc[0] == ""


def test_market_cap_equal_to_the_minimum():
    df = tv(tv_stock(market_cap=U["market_cap_min_eur"], reporting_currency="EUR"))
    assert status(df, "R3.2") == "pass"


def test_market_cap_one_euro_above_the_maximum():
    df = tv(tv_stock(market_cap=U["market_cap_max_eur"] + 1, reporting_currency="EUR"))
    assert status(df, "R3.2") == "fail"
    assert "outside" in df["reason_R3.2"].iloc[0]


def test_liquidity_equal_to_the_minimum():
    # 4,000 shares x 25 EUR = 100,000 EUR/day
    df = tv(tv_stock(avg_volume_3m=U["liquidity_min_eur_per_day"] / 25, price=25.0, currency="EUR"))
    assert status(df, "R3.3") == "pass"
    df = tv(tv_stock(avg_volume_3m=U["liquidity_min_eur_per_day"] / 25 - 1, price=25.0, currency="EUR"))
    assert status(df, "R3.3") == "fail"


def test_closed_end_fund():
    df = tv(tv_stock(tv_ticker="AMEX:CEF", exchange="AMEX", type="fund", subtype=["closedend"]))
    assert status(df, "R3.4") == "fail"
    assert df["reason_R3.4"].iloc[0] == "type fund"


def test_fund_industry():
    df = tv(tv_stock(industry="Investment Trusts/Mutual Funds"))
    assert status(df, "R3.4") == "fail"


def test_type_missing():
    assert status(tv(tv_stock(type=None)), "R3.4") == "missing"


def test_excluded_typespecs():
    cfg = config()
    cfg["universe"]["excluded_typespecs"] = ["preferred"]
    df = filter_tv(universe(tv_stock(subtype=["preferred"])), cfg, RATES)
    assert df["R3.4"].iloc[0] == "fail"


def test_regional_bank():
    df = tv(tv_stock(industry="Regional Banks"))
    assert status(df, "R3.5") == "fail"


def test_insurance_broker_kept():
    df = tv(tv_stock(industry="Insurance Brokers/Services"))
    assert status(df, "R3.5") == "pass"


def test_industry_missing():
    df = tv(tv_stock(industry=float("nan")))
    assert status(df, "R3.5") == "missing" and status(df, "R3.4") == "missing"


def test_secondary_listing():
    df = tv(tv_stock(primary=False))
    assert status(df, "R3.1") == "fail"


def test_fwb_in_germany_kept():
    df = tv(tv_stock(tv_ticker="FWB:XYZ", market="germany", exchange="FWB", currency="EUR",
                     reporting_currency="EUR"))
    assert status(df, "R3.1") == "pass"


def test_otc_excluded():
    df = tv(tv_stock(tv_ticker="OTC:VXRT", exchange="OTC"))
    assert status(df, "R3.1") == "fail"
    assert "OTC" in df["reason_R3.1"].iloc[0]


def test_market_outside_the_universe_and_missing_data():
    df = tv(tv_stock(market="japan"), tv_stock(primary=None))
    assert df["R3.1"].tolist() == ["fail", "missing"]


def test_cad_rate_missing():
    rates = {k: v for k, v in RATES.items() if k != "CAD"}
    df = tv(tv_stock(tv_ticker="TSX:AC", market="canada", exchange="TSX", currency="CAD",
                     reporting_currency="CAD"), rates=rates)
    assert status(df, "R3.2") == "missing" and status(df, "R3.3") == "missing"
    assert df["reason_R3.2"].iloc[0] == "exchange rate CAD missing"
    assert pd.isna(df["market_cap_eur"].iloc[0])        # never 0


def test_market_cap_missing():
    df = tv(tv_stock(market_cap=float("nan")))
    assert status(df, "R3.2") == "missing"
    assert df["reason_R3.2"].iloc[0] == "market cap missing"


def test_london_gbp_and_gbx():
    # market cap 500 M GBP -> 575 M EUR (GBP rate); price 225 GBX x 100,000 -> 258,750 EUR (GBX rate)
    df = tv(tv_stock(tv_ticker="LSE:SRB", market="uk", exchange="LSE", currency="GBX",
                     reporting_currency="GBP", market_cap=500e6, price=225.0, avg_volume_3m=100_000.0))
    assert df["market_cap_eur"].iloc[0] == pytest.approx(575e6)
    assert df["liquidity_eur"].iloc[0] == pytest.approx(258_750.0)
    assert status(df, "R3.2") == "pass" and status(df, "R3.3") == "pass"
    # GBX/GBP confusion: the same amount read in GBX gives 5.75 M EUR, below the minimum
    df = tv(tv_stock(reporting_currency="GBX", market_cap=500e6))
    assert status(df, "R3.2") == "fail"


# --- R3.6 (Yahoo stage) ---------------------------------------------------------------

def test_r3_6_complete_data():
    df = yahoo(yahoo_data())
    assert df["R3.6"].iloc[0] == "pass"


def test_recent_ipo():
    df = yahoo(yahoo_data(prices=prices_since("2026-06-24")))   # 4 months
    assert df["R3.6"].iloc[0] == "fail"
    assert "first price" in df["reason_R3.6"].iloc[0]


def test_history_starting_two_days_after_the_limit():
    # date - 6 months = 2026-04-24 (Friday); first price on Sunday 04-26 -> Monday 04-27
    series = pd.Series(100.0, index=pd.bdate_range("2026-04-26", "2026-10-23", tz="America/New_York"))
    df = yahoo(yahoo_data(prices=series))
    assert df["R3.6"].iloc[0] == "pass"


def test_empty_history():
    df = yahoo(yahoo_data(prices=pd.Series(dtype=float)))
    assert df["R3.6"].iloc[0] == "fail"


def test_missing_year_y2_not_found():
    b = statement({"2025-12-31": {"Total Debt": 1.0}, "2024-12-31": {"Total Debt": 1.0},
                   "2022-12-31": {"Total Debt": 1.0}})
    df = yahoo(yahoo_data(balance_sheet=b))
    assert df["R3.6"].iloc[0] == "fail"
    assert "Y-2" in df["reason_R3.6"].iloc[0]


def test_only_two_balance_sheets():
    b = statement({"2025-12-31": {"Total Debt": 1.0}, "2023-12-31": {"Total Debt": 1.0}})
    df = yahoo(yahoo_data(balance_sheet=b))
    assert df["R3.6"].iloc[0] == "fail"
    assert "2 annual balance sheets" in df["reason_R3.6"].iloc[0]


def test_empty_yfinance_column_ignored():
    # yfinance sometimes returns an old column without any value: it does not count
    b = statement({"2025-12-31": {"Total Debt": 1.0}, "2024-12-31": {"Total Debt": 1.0},
                   "2023-12-31": {"Total Debt": float("nan")}})
    df = yahoo(yahoo_data(balance_sheet=b))
    assert df["R3.6"].iloc[0] == "fail"


def test_y2_found_with_a_date_offset():
    # shifted fiscal year: Y on 2025-06-30, Y-2 on 2023-07-31 (31 days apart, tolerance 45)
    b = statement({"2025-06-30": {"Total Debt": 1.0}, "2024-06-30": {"Total Debt": 1.0},
                   "2023-07-31": {"Total Debt": 1.0}})
    row = extract_yahoo(yahoo_data(balance_sheet=b), DATE, CFG)
    assert row["yf_date_y2"] == pd.Timestamp("2023-07-31")
