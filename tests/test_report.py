"""Markdown report and detail CSV."""

import datetime as dt
from pathlib import Path

import pandas as pd

from helpers import config, fake_result, three_stocks, tv_stock
from investment_agent.engine.report import number, render_report, write_detail, write_report

EXPECTED = Path(__file__).parent / "expected"
META = {"commit": "a1b2c3d", "start": dt.datetime(2026, 10, 24, 8, 14), "duration_s": 372}


def test_report_identical_to_the_reference():
    stocks = three_stocks()
    text = render_report(fake_result(stocks), config(), META)
    expected = (EXPECTED / "report_min.md").read_text(encoding="utf-8")
    assert text == expected


def test_rule_not_implemented_listed():
    stocks = three_stocks()
    cfg = config()
    assert cfg["rules"]["R6"]["status"] == "not_implemented"
    text = render_report(fake_result(stocks), cfg, META)
    section = text.split("## Rules not applied")[1]
    assert "| R6 | V5 | portfolio construction |" in section
    assert "| R4.1 |" not in section          # rule implemented without a limitation: not listed


def test_removed_rule_not_listed():
    cfg = config()
    assert cfg["rules"]["R4.7"]["status"] == "removed"
    text = render_report(fake_result(three_stocks()), cfg, META)
    section = text.split("## Rules not applied")[1]
    assert "R4.7" not in section and "R4.2" not in section


def test_funnel_groups_in_order():
    text = render_report(fake_result(three_stocks()), config(), META)
    summary = text.split("## Summary")[1].split("## Ranking")[0]
    headers = [l for l in summary.splitlines() if l.startswith("| **") and "Eligible" not in l]
    assert headers == ["| **Universe** |  |  |  |", "| **Selection criteria** |  |  |  |",
                       "| **Data guards** |  |  |  |"]
    assert summary.index("R3.5 banks") < summary.index("**Selection criteria**") < summary.index("R4.1")
    assert summary.index("R4.6") < summary.index("**Data guards**") < summary.index("R3.6")


def test_pe_fallback_marked_in_the_ranking():
    nan = float("nan")
    res = fake_result([tv_stock(tv_ticker="XETR:FBK", yahoo_ticker="FBK.DE", pe_ttm=nan), tv_stock()])
    text = render_report(res, config(), META)
    assert "| 10.0\u2020 (" in text                       # retained P/E 1e9 / 1e8, fallback
    assert "\u2020 fallback: market cap / TTM net income (TradingView P/E missing)" in text
    assert "Retained P/E: 1 stocks of the universe use the fallback (TradingView P/E missing)." in text
    assert "\u2021 fallback" not in text


def test_roe_fallback_marked_in_the_ranking():
    nan = float("nan")
    res = fake_result([tv_stock(tv_ticker="XETR:FBK", yahoo_ticker="FBK.DE", roe_ttm=nan), tv_stock()])
    text = render_report(res, config(), META)
    assert "| 20.0%\u2021 (" in text                      # 1e8 / 5e8
    assert ("\u2021 fallback: TTM net income / shareholders' equity of the last quarter "
            "(TradingView ROE missing)") in text
    assert "Retained ROE: 1 stocks of the universe use the fallback (TradingView ROE missing)." in text
    plain = render_report(fake_result([tv_stock()]), config(), META)
    assert "\u2021 fallback" not in plain and "\u2020 fallback" not in plain
    assert "Retained ROE: 0 stocks" in plain


def test_no_eligible_stock():
    res = fake_result([tv_stock(pe_ttm=30.0)])
    text = render_report(res, config(), META)
    ranking = text.split("## Ranking")[1].split("##")[0]
    assert "No eligible stock." in ranking
    assert "| **Eligible** | **0** |" in text
    assert "| R4.1 retained P/E | 0 | 1 | 0 |" in text


def test_partial_run_reported():
    stocks = three_stocks()
    text = render_report(fake_result(stocks), config(),
                         {**META, "partial_markets": ["america"]})
    assert "**Partial run**: markets america only." in text


def test_report_and_csv_written(tmp_path):
    stocks = three_stocks()
    res = fake_result(stocks)
    path = write_report("# test\n", tmp_path / "reports", dt.date(2026, 10, 24), "higgons")
    assert path.name == "2026-10-24_higgons.md"
    csv = write_detail(res.universe, tmp_path / "reports")
    reread = pd.read_csv(csv, encoding="utf-8")
    assert list(reread.columns[:4]) == ["tv_ticker", "yahoo_ticker", "eligible", "first_failed_rule"]
    assert len(reread) == 3
    assert reread.loc[0, "pe_source"] == "tv" and reread.loc[0, "pe_retained"] == 6.0
    assert reread.loc[2, "R4.11"] == "missing" and pd.isna(reread.loc[2, "roe_retained"])
    assert reread.loc[0, "first_bar_date"] == "2015-01-02"
    assert not [c for c in reread.columns if c.startswith("yf_")]


def test_number_formatting():
    assert number(1234.56) == "1,234.6"
    assert number(-0.04) == "0.0"
    assert number(-3.25, 2) == "−3.25"
    assert number(34.0, signed=True) == "+34.0"
    assert number(None) == "—"


def test_missing_data_grouped_by_stock():
    nan = float("nan")
    res = fake_result([tv_stock(tv_ticker="XETR:EMPTY", yahoo_ticker="EMPTY.DE", pe_ttm=nan, roe_ttm=nan,
                                shareholders_equity=nan, revenue_cagr_5y=nan)])
    text = render_report(res, config(), META)
    assert ("| XETR:EMPTY (EMPTY.DE) | R4.3, R4.11 | 5-year revenue CAGR missing ; ROE missing; fallback not "
            "computed: shareholders' equity / TTM net income missing |") in text
    assert "Stocks concerned: 1." in text
