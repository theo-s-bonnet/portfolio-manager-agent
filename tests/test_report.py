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


def test_no_eligible_stock():
    res = fake_result([tv_stock(pe_ttm=30.0)])
    text = render_report(res, config(), META)
    ranking = text.split("## Ranking")[1].split("##")[0]
    assert "No eligible stock." in ranking
    assert "| **Eligible** | **0** |" in text
    assert "| R4.1 P/E TTM | 0 | 1 | 0 |" in text


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
    assert reread.loc[2, "R4.2"] == "missing"
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
                                revenue_cagr_5y=nan)])
    text = render_report(res, config(), META)
    assert ("| XETR:EMPTY (EMPTY.DE) | R4.1, R4.2, R4.3 | P/E missing ; ROE missing ; 5-year revenue CAGR missing |"
            ) in text
    assert "Stocks concerned: 1." in text
