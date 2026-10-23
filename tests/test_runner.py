"""Complete run (run command) with fake sources: no intervention, no network."""

import pandas as pd
import pytest

from helpers import DATE, RATES, config, tv_stock
from investment_agent.__main__ import parse_args
from investment_agent.engine.runner import CAT_MARKETS, CAT_NO_MAPPING, Options, run

FIELDS = config()["tradingview_fields"]


def european(tv_ticker, market, exchange, country, currency="EUR", **changes):
    return tv_stock(tv_ticker=tv_ticker, market=market, exchange=exchange, country=country,
                    currency=currency, reporting_currency=currency, price=100.0, ema=90.0, **changes)


STOCKS = {
    "germany": [
        european("XETR:AAA", "germany", "XETR", "Germany", perf_6m=30.0),
        european("OTC:EEE", "germany", "OTC", "Germany"),
    ],
    "norway": [
        european("OSL:BBB", "norway", "OSL", "Norway", currency="NOK", market_cap=5e9, perf_6m=10.0),
    ],
    "sweden": [
        european("NGM:FFF", "sweden", "NGM", "Sweden", currency="SEK", market_cap=5e9),
    ],
    "croatia": [
        european("ZSE:TEST", "croatia", "ZSE", "Croatia"),     # passes every rule, no Yahoo ticker
    ],
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


@pytest.fixture(autouse=True)
def no_yahoo_download(monkeypatch):
    """Only exchange rates may come from Yahoo: any download of a stock fails the test."""
    def forbidden(*args, **kwargs):
        raise AssertionError("Yahoo download of a stock")
    monkeypatch.setattr("investment_agent.engine.data.yahoo.download", forbidden)
    monkeypatch.setattr("investment_agent.engine.data.yahoo._download_yfinance", forbidden)


def options(tmp_path, **changes):
    base = dict(date=DATE, root=tmp_path, markets=["germany", "norway", "sweden", "croatia", "atlantis"],
                source_tv=FakeQuery, source_fx=fake_rates)
    base.update(changes)
    return Options(**base)


def test_complete_run_without_intervention(tmp_path):
    output = run(options(tmp_path))
    assert output.report == tmp_path / "data" / "reports" / "2026-10-24_higgons.md"
    assert output.n_eligible == 2
    assert output.result.ranking["tv_ticker"].tolist() == ["XETR:AAA", "OSL:BBB"]

    df = output.result.universe.set_index("tv_ticker")
    assert df.loc["OTC:EEE", "first_failed_rule"] == "R3.1"
    assert df.loc["NGM:FFF", "yahoo_mapping"] == "fail"
    assert df["R3.6"].notna().all()                       # every rule evaluated for every stock

    w = output.result.warnings
    assert any(l.startswith("atlantis: error") for l in w[CAT_MARKETS])
    assert w[CAT_NO_MAPPING] == ["NGM: 1 after R3.1", "ZSE: 1 after R3.1"]

    detail = pd.read_csv(output.detail)
    assert len(detail) == 5
    assert {"first_bar_time", "first_bar_date", "yahoo_mapping", "reason_yahoo_mapping"} <= set(detail.columns)
    assert not [c for c in detail.columns if c.startswith("yf_") or c == "yahoo_match"]

    text = output.report.read_text(encoding="utf-8")
    assert "**Partial run**" in text
    assert "| **Eligible** | **2** |" in text
    assert "commit unknown" in text          # tmp_path is not a Git repository


def test_stock_without_yahoo_ticker_excluded_by_the_guard(tmp_path):
    output = run(options(tmp_path))
    row = output.result.universe.set_index("tv_ticker").loc["ZSE:TEST"]
    assert row["yahoo_mapping"] == "fail" and not row["eligible"]
    assert row["first_failed_rule"] == "yahoo_mapping"
    assert row["reason_yahoo_mapping"] == "no verified Yahoo mapping for exchange ZSE"
    text = output.report.read_text(encoding="utf-8")
    assert "| Yahoo ticker available | 2 | 2 | 0 |" in text      # NGM:FFF and ZSE:TEST


def test_output_option(tmp_path):
    output = run(options(tmp_path, output=tmp_path / "elsewhere"))
    assert output.report.parent == tmp_path / "elsewhere"
    assert output.detail.parent == tmp_path / "elsewhere"
    assert not (tmp_path / "data").exists()


def test_command_arguments():
    args = parse_args(["run", "--markets", "france, germany", "--output", "out"])
    assert args.command == "run" and args.strategy == "higgons"
    assert args.markets == "france, germany" and str(args.output) == "out"
    for removed in (["--compare", "list.csv"], ["--no-cache"]):
        with pytest.raises(SystemExit):
            parse_args(["run", *removed])


def test_every_market_failing(tmp_path):
    output = run(options(tmp_path, markets=["atlantis"]))
    assert output.n_eligible == 0
    text = output.report.read_text(encoding="utf-8")
    assert "No eligible stock." in text and "atlantis: error" in text
