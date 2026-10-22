"""Yahoo source: match check, cache, and one real call (network)."""

import datetime as dt

import pandas as pd
import pytest

from investment_agent.engine.data.yahoo import YahooData, check_match, download

MAX_GAP_PCT = 10    # value of technical.tv_yahoo_price_gap_max_pct in the tests
TOLERANCE = 1e-9    # technical.comparison_tolerance


def data(currency="USD", prices=(100.0, 101.0), error=None):
    series = pd.Series(list(prices), index=pd.date_range("2026-10-15", periods=len(prices)), dtype=float)
    return YahooData(ticker="TEST", prices=series, currency=currency, shares_outstanding=1e6, error=error)


# --- match check ----------------------------------------------------------------------

def test_match_ok():
    assert check_match(data(), "USD", 100.0, MAX_GAP_PCT) is None


def test_yahoo_usd_tradingview_nok():
    reason = check_match(data(currency="USD"), "NOK", 101.0, MAX_GAP_PCT)
    assert reason is not None and "currency" in reason


def test_yahoo_price_30_pct_higher():
    reason = check_match(data(prices=(130.0,)), "USD", 100.0, MAX_GAP_PCT)
    assert reason is not None and "30.0%" in reason


def test_gap_equal_to_the_maximum_accepted():
    assert check_match(data(prices=(110.0,)), "USD", 100.0, MAX_GAP_PCT, TOLERANCE) is None
    assert check_match(data(prices=(110.01,)), "USD", 100.0, MAX_GAP_PCT, TOLERANCE)


def test_gbx_and_lowercase_gbp_equivalent():
    assert check_match(data(currency="GBp", prices=(225.0,)), "GBX", 225.0, MAX_GAP_PCT) is None


def test_gbp_and_gbx_different():
    assert check_match(data(currency="GBP", prices=(2.25,)), "GBX", 225.0, MAX_GAP_PCT)


@pytest.mark.parametrize("prices", [(), (float("nan"),)])
def test_empty_history(prices):
    reason = check_match(data(prices=prices), "USD", 100.0, MAX_GAP_PCT)
    assert reason == "empty Yahoo price history"


def test_last_valid_price_ignores_nan():
    assert data(prices=(100.0, 102.0, float("nan"))).last_price == 102.0


# --- cache ----------------------------------------------------------------------------

class FakeSource:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def __call__(self, ticker, start_date):
        self.calls += 1
        return data(error=self.error)


def test_one_day_cache(tmp_path):
    source, day = FakeSource(), dt.date(2026, 10, 24)
    start = dt.date(2026, 4, 24)
    first = download("MHO", start, tmp_path, today=day, source=source)
    second = download("MHO", start, tmp_path, today=day, source=source)
    assert source.calls == 1
    assert second.last_price == first.last_price
    assert (tmp_path / "2026-10-24" / "MHO.pkl").exists()
    download("MHO", start, tmp_path, today=day + dt.timedelta(days=1), source=source)
    assert source.calls == 2


def test_without_cache(tmp_path):
    source = FakeSource()
    for _ in range(2):
        download("MHO", dt.date(2026, 4, 24), None, source=source)
    assert source.calls == 2


def test_error_never_cached(tmp_path):
    source = FakeSource(error="info: HTTPError")
    for _ in range(2):
        result = download("MHO", dt.date(2026, 4, 24), tmp_path, source=source)
    assert source.calls == 2 and result.error == "info: HTTPError"


# --- network --------------------------------------------------------------------------

@pytest.mark.network
def test_network_yahoo_mho():
    d = download("MHO", dt.date.today() - dt.timedelta(days=200), cache=None)
    assert d.error is None, d.error
    assert d.currency == "USD"
    assert d.last_price and d.shares_outstanding
    assert len(d.prices.dropna()) > 100
    assert {"Total Debt", "Common Stock Equity"} <= set(d.balance_sheet.index)
    assert "Diluted Average Shares" in d.income_statement.index
