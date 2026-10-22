"""Markdown report, detail CSV and comparison with an exported TradingView list."""

import datetime as dt
from pathlib import Path

import pandas as pd

from helpers import config, fake_result, three_stocks, tv_stock
from investment_agent.engine.report import number, render_report, write_detail, write_report

EXPECTED = Path(__file__).parent / "expected"
META = {"commit": "a1b2c3d", "start": dt.datetime(2026, 10, 24, 8, 14), "duration_s": 372}


def test_report_identical_to_the_reference():
    stocks, data = three_stocks()
    text = render_report(fake_result(stocks, data), config(), META)
    expected = (EXPECTED / "report_min.md").read_text(encoding="utf-8")
    assert text == expected


def test_rule_not_implemented_listed():
    stocks, data = three_stocks()
    cfg = config()
    assert cfg["rules"]["R6"]["status"] == "not_implemented"
    text = render_report(fake_result(stocks, data), cfg, META)
    section = text.split("## Rules not applied")[1]
    assert "| R6 | V5 | portfolio construction |" in section
    assert "| R4.1 |" not in section          # rule implemented without a limitation: not listed


def test_no_eligible_stock():
    res = fake_result([tv_stock(pe_ttm=30.0)], {})
    text = render_report(res, config(), META)
    ranking = text.split("## Ranking")[1].split("##")[0]
    assert "No eligible stock." in ranking
    assert "| **Eligible** | **0** |" in text
    assert "| R4.1 P/E TTM | 0 | 1 | 0 |" in text


def test_partial_run_reported():
    stocks, data = three_stocks()
    text = render_report(fake_result(stocks, data), config(),
                         {**META, "partial_markets": ["america"]})
    assert "**Partial run**: markets america only." in text


def test_report_and_csv_written(tmp_path):
    stocks, data = three_stocks()
    res = fake_result(stocks, data)
    path = write_report("# test\n", tmp_path / "reports", dt.date(2026, 10, 24), "higgons")
    assert path.name == "2026-10-24_higgons.md"
    csv = write_detail(res.universe, tmp_path / "reports")
    reread = pd.read_csv(csv, encoding="utf-8")
    assert list(reread.columns[:4]) == ["tv_ticker", "yahoo_ticker", "eligible", "first_failed_rule"]
    assert len(reread) == 3
    assert reread.loc[0, "warnings"].startswith("Splits detected")
    assert reread.loc[2, "R4.2"] == "missing"


def test_number_formatting():
    assert number(1234.56) == "1,234.6"
    assert number(-0.04) == "0.0"
    assert number(-3.25, 2) == "−3.25"
    assert number(34.0, signed=True) == "+34.0"
    assert number(None) == "—"


# --- comparison ----------------------------------------------------------------------------

from helpers import RATES, yahoo_data  # noqa: E402
from investment_agent.engine.comparison import (  # noqa: E402
    compare, load_screen, match_list, read_list)
from investment_agent.strategies.higgons import SCREEN_PATH  # noqa: E402

SCREEN = load_screen(SCREEN_PATH)


def comparison(stocks, csv_text, tmp_path):
    path = tmp_path / "list.csv"
    path.write_text(csv_text, encoding="utf-8")
    data = {v["tv_ticker"]: yahoo_data() for v in stocks}
    res = fake_result(stocks, data)
    return res, compare(res.universe, path, SCREEN, RATES, 1e-9)


def test_ktn_xetr_and_cef_nyse_arca(tmp_path):
    stocks = [
        tv_stock(tv_ticker="XETR:KTN", yahoo_ticker="KTN.DE", market="germany", exchange="XETR",
                 currency="EUR", reporting_currency="EUR"),
        tv_stock(tv_ticker="AMEX:CEF", yahoo_ticker="CEF", exchange="AMEX", type="fund",
                 industry="Investment Trusts/Mutual Funds"),
    ]
    _, comp = comparison(stocks, "KTN;XETR\nCEF;NYSE Arca\n", tmp_path)
    assert comp["both_lists"] == ["XETR:KTN"]
    assert comp["list_only"] == [
        {"export": "NYSE Arca;CEF", "tv_ticker": "AMEX:CEF", "rule": "R3.4", "reason": "type fund"}]
    assert comp["n_list"] == 2 and comp["n_common"] == 1


def test_price_at_101_pct_of_the_ema(tmp_path):
    stocks = [tv_stock(tv_ticker="NYSE:EMA", yahoo_ticker="EMA", price=50.5, ema=50.0)]
    _, comp = comparison(stocks, "Ticker,Exchange\nEMA,NYSE\n", tmp_path)
    (only,) = comp["list_only"]
    assert only["rule"] == "R4.5" and "101" in only["reason"]
    assert comp["not_reproduced"] == []          # the recomputed screen keeps it
    assert comp["n_screen"] == 1


def test_roe_equal_to_10_only_in_the_script(tmp_path):
    stocks = [tv_stock(tv_ticker="NYSE:ROE", yahoo_ticker="ROE", roe_ttm=10.0),
              tv_stock(tv_ticker="NYSE:OTHER", yahoo_ticker="OTHER")]
    res, comp = comparison(stocks, "Ticker,Exchange\nOTHER,NYSE\n", tmp_path)
    assert res.universe.set_index("tv_ticker").loc["NYSE:ROE", "eligible"]
    assert comp["script_only"] == [{"tv_ticker": "NYSE:ROE", "criterion": "ROE > 10% (10)"}]


def test_only_in_the_script_without_criterion_at_fault(tmp_path):
    _, comp = comparison([tv_stock()], "Ticker,Exchange\nOTHER,NYSE\n", tmp_path)
    assert comp["script_only"][0]["criterion"].startswith("none: different data")
    assert comp["list_only"][0]["tv_ticker"] is None      # not in the universe


def test_comparison_section_in_the_report(tmp_path):
    res, comp = comparison([tv_stock(tv_ticker="NYSE:EMA", yahoo_ticker="EMA", price=50.5, ema=50.0)],
                           "EMA,NYSE\n", tmp_path)
    res.comparison = comp
    text = render_report(res, config(), META)
    assert "## Comparison with the TradingView list" in text
    assert "| NYSE;EMA | NYSE:EMA | R4.5 |" in text


def test_list_formats(tmp_path):
    path = tmp_path / "l.csv"
    path.write_text("\ufeffTicker,Exchange\nHLUN_A,OMXCOP\nREPX,NYSE Arca\n", encoding="utf-8")
    assert read_list(path) == [("HLUN_A", "OMXCOP"), ("REPX", "NYSE Arca")]
    path.write_text("Symbol;Description\nNASDAQ:AUPH;Aurinia\n", encoding="utf-8")
    assert read_list(path) == [("AUPH", "NASDAQ")]


def test_matching():
    universe = pd.DataFrame({
        "tv_ticker": ["OMXCOP:HLUN_A", "AMEX:REPX", "TSX:LAS.A", "NYSE:X", "TSX:X", "TSX:BTO", "XETR:EVT", "NASDAQ:Y", "TSXV:Q"],
        "market": ["denmark", "america", "canada", "america", "canada", "canada", "germany", "america", "canada"],
    })
    entries = [("HLUN_A", "OMXCOP"), ("REPX", "NYSE Arca"), ("LAS.A", None), ("X", None), ("Z", "NYSE"),
               ("BTO", "TSXV"), ("EVT", "TSX"), ("X", "NASDAQ")]
    pairs = match_list(entries, universe)
    assert [p["tv_ticker"] for p in pairs] == [
        "OMXCOP:HLUN_A", "AMEX:REPX", "TSX:LAS.A",
        None,             # ambiguous symbol, no exchange
        None,             # not found
        "TSX:BTO",        # mistyped exchange, same country
        None,             # EVT in Toronto is not EVT on Xetra (another country)
        "NYSE:X",         # NASDAQ:X not found, X unique in the United States
    ]


def test_missing_data_grouped_by_stock():
    nan = float("nan")
    res = fake_result([tv_stock(tv_ticker="NYSE:EMPTY", yahoo_ticker="EMPTY", pe_ttm=nan, roe_ttm=nan,
                                revenue_cagr_5y=nan)], {})
    text = render_report(res, config(), META)
    assert ("| NYSE:EMPTY (EMPTY) | R4.1, R4.2, R4.3 | P/E missing ; ROE missing ; 5-year revenue CAGR missing "
            "(later steps not evaluated) |") in text
    assert "Stocks concerned: 1." in text
