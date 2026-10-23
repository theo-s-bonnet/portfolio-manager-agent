"""Universe filters R3.1 to R3.5 and data guard R3.6."""

import pandas as pd
import pytest

from helpers import DATE, RATES, config, tv_stock, universe, unix_time
from investment_agent.strategies.higgons import filter_tv

CFG = config()
U = CFG["universe"]


def tv(*stocks, rates=RATES):
    return filter_tv(universe(*stocks), CFG, rates, DATE)


def status(df, rule, i=0):
    return df[rule].iloc[i]


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
    df = tv(tv_stock(tv_ticker="OMXSTO:TEST_PREF", market="sweden", exchange="OMXSTO", country="Sweden",
                     subtype=["preferred"]))
    assert status(df, "R3.4") == "fail"


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
    df = tv(tv_stock(tv_ticker="OTC:TEST", market="germany", exchange="OTC"))
    assert status(df, "R3.1") == "fail"
    assert "OTC" in df["reason_R3.1"].iloc[0]


def test_market_outside_the_universe_and_missing_data():
    df = tv(tv_stock(market="japan"), tv_stock(primary=None))
    assert df["R3.1"].tolist() == ["fail", "missing"]


def test_nok_rate_missing():
    rates = {k: v for k, v in RATES.items() if k != "NOK"}
    df = tv(tv_stock(tv_ticker="OSL:TEST", market="norway", exchange="OSL", country="Norway",
                     currency="NOK", reporting_currency="NOK"), rates=rates)
    assert status(df, "R3.2") == "missing" and status(df, "R3.3") == "missing"
    assert df["reason_R3.2"].iloc[0] == "exchange rate NOK missing"
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


# --- PEA universe (R3.1 headquarters, ISIN warning) and preferred shares (R3.4) -------------

def isin_warnings(df, i=0):
    return [text for category, text in df["warnings"].iloc[i] if category.startswith("Statutory country")]


def test_headquarters_outside_the_eu_eea():
    df = tv(tv_stock(tv_ticker="OSL:TEST", market="norway", exchange="OSL", currency="NOK",
                     reporting_currency="NOK", country="Bermuda"))
    assert status(df, "R3.1") == "fail"
    assert df["reason_R3.1"].iloc[0] == "headquarters country Bermuda outside the EU/EEA"


def test_headquarters_country_missing():
    df = tv(tv_stock(country=None))
    assert status(df, "R3.1") == "missing"
    assert df["reason_R3.1"].iloc[0] == "missing: headquarters country"


def test_aland_islands_kept():
    df = tv(tv_stock(tv_ticker="OMXHEX:TEST", market="finland", exchange="OMXHEX", country="Aland Islands"))
    assert status(df, "R3.1") == "pass"


def test_faroe_islands_excluded():
    df = tv(tv_stock(tv_ticker="OMXCOP:TEST", market="denmark", exchange="OMXCOP", country="Faroe Islands"))
    assert status(df, "R3.1") == "fail"


def test_market_outside_the_pea_universe():
    df = tv(tv_stock(tv_ticker="LSE:TEST", market="uk", exchange="LSE", country="United Kingdom"))
    assert status(df, "R3.1") == "fail"
    assert df["reason_R3.1"].iloc[0] == "market uk outside the universe"


def test_isin_prefix_outside_the_eu_eea_warns_only():
    df = tv(tv_stock(tv_ticker="OSL:TEST", market="norway", exchange="OSL", currency="NOK",
                     reporting_currency="NOK", market_cap=5e9, country="Norway", isin="BMG0670A1099"))
    assert [status(df, r) for r in ("R3.1", "R3.2", "R3.3", "R3.4", "R3.5")] == ["pass"] * 5
    assert isin_warnings(df) == ["ISIN BMG0670A1099, headquarters Norway"]


def test_legacy_czech_isin_prefix_accepted():
    df = tv(tv_stock(tv_ticker="PSECZ:TEST", market="czech", exchange="PSECZ", currency="CZK",
                     reporting_currency="CZK", country="Czech Republic", isin="CS0008418869"))
    assert isin_warnings(df) == []
    assert "NO" in CFG["technical"]["eu_eea_isin_prefixes"]       # not read as a YAML boolean


def test_german_preference_shares_kept():
    df = tv(tv_stock(tv_ticker="XETR:TEST3", subtype=["preferred"]))
    assert status(df, "R3.4") == "pass"


def test_preference_shares_excluded_outside_germany():
    df = tv(tv_stock(tv_ticker="OMXSTO:TEST_PREF", market="sweden", exchange="OMXSTO", country="Sweden",
                     subtype=["preferred"]))
    assert status(df, "R3.4") == "fail"
    assert df["reason_R3.4"].iloc[0] == "subtype preferred (market sweden)"


def test_common_shares_kept_outside_germany():
    df = tv(tv_stock(tv_ticker="OMXSTO:TEST", market="sweden", exchange="OMXSTO", country="Sweden",
                     subtype=["common"]))
    assert status(df, "R3.4") == "pass"


# --- R3.6 (data guard: first TradingView bar) ----------------------------------------
# DATE = 2026-10-24: limit = 2026-04-24 + 7 days of tolerance = 2026-05-01.

def test_r3_6_long_history():
    df = tv(tv_stock())                                   # first bar on 2015-01-02
    assert status(df, "R3.6") == "pass"


def test_recent_ipo():
    df = tv(tv_stock(first_bar_time=unix_time("2026-06-24")))   # 4 months
    assert status(df, "R3.6") == "fail"
    assert df["reason_R3.6"].iloc[0] == "first price on 2026-06-24, less than 6 months of history"


def test_history_starting_two_days_after_the_limit():
    df = tv(tv_stock(first_bar_time=unix_time("2026-04-26")))   # DATE - 6 months + 2 days
    assert status(df, "R3.6") == "pass"


def test_first_bar_missing():
    df = tv(tv_stock(first_bar_time=float("nan")))
    assert status(df, "R3.6") == "missing"
    assert pd.isna(df["first_bar_date"].iloc[0])


def test_first_bar_one_day_after_the_tolerance():
    df = tv(tv_stock(first_bar_time=unix_time("2026-05-02")))   # DATE - 6 months + 8 days
    assert status(df, "R3.6") == "fail"


def test_first_bar_exactly_at_the_limit():
    df = tv(tv_stock(first_bar_time=unix_time("2026-05-01")))
    assert status(df, "R3.6") == "pass"


def test_ticker_change_resets_the_history():
    # new symbol one month ago: TradingView still gives a 6-month performance (since the first bar)
    df = tv(tv_stock(first_bar_time=unix_time("2026-09-24"), perf_6m=12.0))
    assert status(df, "R4.6") == "pass"
    assert status(df, "R3.6") == "fail"
