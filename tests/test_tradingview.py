"""TradingView source: column renaming (no network) and one real call (network)."""

import pandas as pd
import pytest

from investment_agent.engine.data.tradingview import download_universe

FIELDS = {"price": "close", "pe_ttm": "price_earnings_ttm"}


class FakeQuery:
    """Mimics tradingview_screener.Query for two markets."""

    DATA = {
        "america": (2, pd.DataFrame({"ticker": ["NYSE:MHO", "NASDAQ:AUPH"],
                                     "close": [123.3, 12.0], "price_earnings_ttm": [10.4, None]})),
        "uk": (3, pd.DataFrame({"ticker": ["LSE:SRB"], "close": [225.0], "price_earnings_ttm": [3.5]})),
    }

    def set_markets(self, market):
        self.market = market
        return self

    def select(self, *columns):
        self.columns = columns
        return self

    def limit(self, n):
        self.n = n
        return self

    def get_scanner_data(self):
        if self.market not in self.DATA:
            raise RuntimeError("unknown market")
        total, df = self.DATA[self.market]
        return total, df[["ticker", *self.columns]]


def test_columns_renamed_and_markets_reported():
    df = download_universe(["america", "uk", "atlantis"], FIELDS, limit=2, query_factory=FakeQuery)
    assert list(df.columns) == ["tv_ticker", "market", "price", "pe_ttm"]
    assert df["tv_ticker"].tolist() == ["NYSE:MHO", "NASDAQ:AUPH", "LSE:SRB"]
    assert df["market"].tolist() == ["america", "america", "uk"]
    assert pd.isna(df.loc[1, "pe_ttm"])                       # never replaced by 0
    assert set(df.attrs["market_errors"]) == {"atlantis"}
    assert df.attrs["truncated_markets"] == {"uk": 3}


def test_no_market_available():
    df = download_universe(["atlantis"], FIELDS, limit=10, query_factory=FakeQuery)
    assert df.empty and list(df.columns) == ["tv_ticker", "market", "price", "pe_ttm"]


@pytest.mark.network
def test_network_every_field_of_the_config():
    from investment_agent.engine.config import load_config
    from investment_agent.strategies.higgons import CONFIG_PATH, REQUIRED_KEYS

    cfg = load_config(CONFIG_PATH, REQUIRED_KEYS)
    df = download_universe(["america", "uk"], cfg["tradingview_fields"], limit=5)
    assert df.attrs["market_errors"] == {}
    assert len(df) == 10
    assert df["tv_ticker"].str.contains(":").all()
    assert set(df.loc[df["market"] == "uk", "reporting_currency"]) == {"GBP"}
