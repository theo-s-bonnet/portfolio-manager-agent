"""Complete run (run command) with fake sources: no intervention, no network."""

import pandas as pd
import pytest

from helpers import DATE, RATES, config, tv_stock, yahoo_data
from investment_agent.__main__ import parse_args
from investment_agent.engine.runner import CAT_MARKETS, CAT_MATCH, CAT_NO_MAPPING, Options, run

FIELDS = config()["tradingview_fields"]


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr("investment_agent.engine.data.yahoo.time.sleep", lambda s: None)

STOCKS = {
    "america": [
        tv_stock(tv_ticker="NYSE:AAA", price=100.0, ema=90.0, perf_6m=30.0),
        tv_stock(tv_ticker="NASDAQ:DDD", exchange="NASDAQ", price=100.0, ema=90.0),
        tv_stock(tv_ticker="OTC:EEE", exchange="OTC", price=100.0, ema=90.0),
    ],
    "norway": [
        tv_stock(tv_ticker="OSL:BBB", market="norway", exchange="OSL", country="Norway", currency="NOK",
                 reporting_currency="NOK", market_cap=5e9, price=100.0, ema=90.0, perf_6m=10.0),
    ],
    "sweden": [
        tv_stock(tv_ticker="NGM:FFF", market="sweden", exchange="NGM", country="Sweden", currency="SEK",
                 reporting_currency="SEK", market_cap=5e9, price=100.0, ema=90.0),
    ],
}
YAHOO = {
    "AAA": yahoo_data(ticker="AAA"),
    "DDD": yahoo_data(ticker="DDD", currency="CAD"),
    "BBB.OL": yahoo_data(ticker="BBB.OL", currency="NOK"),
}


class FakeQuery:
    def set_markets(self, market):
        self.market = market
        return self

    def select(self, *columns):
        self.columns = columns
        return self

    def limit(self, n):
        return self

    def get_scanner_data(self):
        if self.market not in STOCKS:
            raise RuntimeError("unknown market")
        rows = [{"ticker": v["tv_ticker"], **{FIELDS[k]: x for k, x in v.items() if k in FIELDS}}
                for v in STOCKS[self.market]]
        df = pd.DataFrame(rows)
        return len(df), df[["ticker", *self.columns]]


def fake_rates(symbols):
    rates = {**RATES, "SEK": 0.09}
    return {s: rates[s.removesuffix("EUR=X")] for s in symbols if s.removesuffix("EUR=X") in rates}


class FakeYahooSource:
    def __init__(self):
        self.calls = []

    def __call__(self, ticker, start_date):
        self.calls.append(ticker)
        return YAHOO[ticker]


def options(tmp_path, **changes):
    base = dict(date=DATE, root=tmp_path, markets=["america", "norway", "sweden", "atlantis"],
                source_tv=FakeQuery, source_fx=fake_rates, source_yahoo=FakeYahooSource())
    base.update(changes)
    return Options(**base)


def test_complete_run_without_intervention(tmp_path):
    output = run(options(tmp_path))
    assert output.report == tmp_path / "data" / "reports" / "2026-10-24_higgons.md"
    assert output.detail.exists()
    assert output.n_eligible == 2
    assert output.result.ranking["tv_ticker"].tolist() == ["NYSE:AAA", "OSL:BBB"]

    df = output.result.universe.set_index("tv_ticker")
    assert df.loc["OTC:EEE", "first_failed_rule"] == "R3.1"
    assert df.loc["NASDAQ:DDD", "yahoo_match"] == "fail"
    assert df.loc["NGM:FFF", "yahoo_match"] == "fail"
    assert pd.isna(df.loc["OTC:EEE", "yahoo_match"])        # not evaluated

    w = output.result.warnings
    assert any(l.startswith("atlantis: error") for l in w[CAT_MARKETS])
    assert w[CAT_MATCH] == ["NASDAQ:DDD -> DDD: different currency (Yahoo: CAD, TradingView: USD)"]
    assert w[CAT_NO_MAPPING] == [
        "NGM: 1 in the universe after R3.1, 1 among the survivors of the TradingView stage"]

    text = output.report.read_text(encoding="utf-8")
    assert "**Partial run**" in text
    assert "| **Eligible** | **2** |" in text
    assert "commit unknown" in text          # tmp_path is not a Git repository


def test_todays_cache_reused(tmp_path):
    source = FakeYahooSource()
    run(options(tmp_path, source_yahoo=source))
    run(options(tmp_path, source_yahoo=source))
    assert sorted(source.calls) == ["AAA", "BBB.OL", "DDD"]   # second pass: everything from the cache


def test_output_and_no_cache(tmp_path):
    source = FakeYahooSource()
    output = run(options(tmp_path, output=tmp_path / "elsewhere", no_cache=True, source_yahoo=source))
    assert output.report.parent == tmp_path / "elsewhere"
    assert not (tmp_path / "data" / "cache").exists()


def test_command_arguments():
    args = parse_args(["run", "--compare", "list.csv", "--no-cache", "--markets", "america, uk"])
    assert args.command == "run" and args.no_cache and args.strategy == "higgons"
    assert str(args.compare) == "list.csv" and args.markets == "america, uk"


def test_run_with_comparison(tmp_path):
    exported = tmp_path / "tickers.csv"
    exported.write_text("Ticker,Exchange\nAAA,NYSE\nDDD,NASDAQ\nZZZ,OTC\n", encoding="utf-8")
    output = run(options(tmp_path, compare=exported))
    comp = output.result.comparison
    assert comp["both_lists"] == ["NYSE:AAA"]
    assert [(e["tv_ticker"], e["rule"]) for e in comp["list_only"]] == [
        ("NASDAQ:DDD", "yahoo_match"), (None, None)]
    assert comp["script_only"][0]["tv_ticker"] == "OSL:BBB"
    assert "## Comparison with the TradingView list" in output.report.read_text(encoding="utf-8")


def test_missing_list(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(options(tmp_path, compare=tmp_path / "missing.csv"))


def test_every_market_failing(tmp_path):
    output = run(options(tmp_path, markets=["atlantis"]))
    assert output.n_eligible == 0
    text = output.report.read_text(encoding="utf-8")
    assert "No eligible stock." in text and "atlantis: error" in text
