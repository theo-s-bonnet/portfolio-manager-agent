"""Factories of fake data for the tests of the filters, the score and the report."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from investment_agent.engine.config import load_config
from investment_agent.engine.data.yahoo import YahooData
from investment_agent.strategies.higgons import CONFIG_PATH, REQUIRED_KEYS

DATE = dt.date(2026, 10, 24)    # a Saturday
RATES = {"EUR": 1.0, "USD": 0.9, "GBP": 1.15, "GBX": 0.0115, "NOK": 0.085, "CAD": 0.65}


def config() -> dict:
    return load_config(CONFIG_PATH, REQUIRED_KEYS)


def tv_stock(**changes) -> dict:
    """A US stock that passes every TradingView filter; `changes` replaces fields."""
    v = {
        "tv_ticker": "NYSE:TEST", "market": "america", "type": "stock", "subtype": ["common"],
        "primary": True, "exchange": "NYSE", "country": "United States", "name": "Test Corp",
        "sector": "Producer Manufacturing", "industry": "Industrial Machinery",
        "currency": "USD", "reporting_currency": "USD", "market_cap": 1.0e9, "price": 50.0,
        "avg_volume_3m": 100_000.0, "pe_ttm": 8.0, "eps_ttm": 6.25, "roe_ttm": 20.0,
        "revenue_cagr_5y": 10.0, "net_debt": 100.0, "ebitda_ttm": 200.0,
        "net_debt_ebitda_tv": 0.5, "net_income_ttm": 1.0e8, "ema": 45.0, "perf_6m": 15.0,
    }
    v.update(changes)
    return v


def universe(*stocks: dict) -> pd.DataFrame:
    return pd.DataFrame(list(stocks))


def statement(columns: dict, rows: dict | None = None) -> pd.DataFrame:
    """Annual balance sheet or income statement in yfinance format (dates as columns, latest first).

    `columns`: {date: {row: value}}; `rows`: values shared by every column.
    """
    data = {pd.Timestamp(d): {**(rows or {}), **vals} for d, vals in columns.items()}
    df = pd.DataFrame(data)
    return df[sorted(df.columns, reverse=True)]


def prices_since(start: str, end: str = "2026-10-23") -> pd.Series:
    index = pd.bdate_range(start, end, tz="America/New_York")
    return pd.Series(100.0, index=index)


def yahoo_data(**changes) -> YahooData:
    """Complete Yahoo data of a stock that passes R3.6, R4.7, R4.9 and R4.10."""
    d = YahooData(
        ticker="TEST",
        prices=prices_since("2026-01-02"),
        balance_sheet=statement({
            "2025-12-31": {"Total Debt": 50.0, "Cash And Cash Equivalents": 10.0,
                           "Common Stock Equity": 200.0, "Ordinary Shares Number": 100.0},
            "2024-12-31": {"Total Debt": 60.0, "Cash And Cash Equivalents": 10.0,
                           "Common Stock Equity": 180.0, "Ordinary Shares Number": 100.0},
            "2023-12-31": {"Total Debt": 80.0, "Cash And Cash Equivalents": 10.0,
                           "Common Stock Equity": 150.0, "Ordinary Shares Number": 100.0},
        }),
        income_statement=statement({
            "2025-12-31": {"Diluted Average Shares": 100.0},
            "2024-12-31": {"Diluted Average Shares": 100.0},
            "2023-12-31": {"Diluted Average Shares": 100.0},
        }),
        splits=pd.Series(dtype=float),
        shares_outstanding=100.0,
        currency="USD",
    )
    for key, val in changes.items():
        setattr(d, key, val)
    return d


def fake_result(stocks: list[dict], data: dict, cfg: dict | None = None, comparison=None):
    """Chains both stages without network access (as engine/runner.py does); returns a RunResult.

    `data`: {tv_ticker: YahooData} for the stocks that pass the TradingView stage.
    """
    import investment_agent.strategies.higgons as strategy
    from investment_agent.engine.report import RunResult, strategy_presentation
    from investment_agent.engine.statuses import (
        ELIGIBLE_COLUMN, FIRST_FAILED_COLUMN, MATCH_COLUMN, PASS, eligibility, first_failed_rule)

    cfg = cfg or config()
    df = strategy.filter_tv(universe(*stocks), cfg, RATES)
    df["yahoo_ticker"] = [v.get("yahoo_ticker") for v in stocks]
    survivors = eligibility(df, [c for c, _ in strategy.TV_STEPS])
    df[MATCH_COLUMN] = pd.Series(None, index=df.index, dtype=object)
    rows = {}
    for i in df.index[survivors]:
        df.loc[i, MATCH_COLUMN] = PASS
        rows[i] = strategy.extract_yahoo(data[df.loc[i, "tv_ticker"]], DATE, cfg)
    if rows:
        yahoo = strategy.filter_yahoo(pd.DataFrame(list(rows.values()), index=list(rows)), cfg, DATE)
        for column in yahoo.columns:
            df[column] = yahoo[column].reindex(df.index)
    presentation = strategy_presentation(strategy, cfg)
    for column, _ in presentation.steps:
        if column not in df:
            df[column] = pd.Series(None, index=df.index, dtype=object)
    columns = [c for c, _ in presentation.steps]
    df[ELIGIBLE_COLUMN] = eligibility(df, columns)
    df[FIRST_FAILED_COLUMN] = first_failed_rule(df, columns)
    return RunResult(
        run_date=DATE, universe=df, ranking=strategy.rank(df, cfg),
        presentation=presentation, comparison=comparison, n_markets=2,
        warnings={"TradingView markets with errors or truncated": [],
                  "Failed Yahoo matches": []},
    )


def three_stocks():
    """Fake result of the reference report: two eligible stocks (one with a split), one
    excluded because its ROE is missing."""
    stocks = [
        tv_stock(tv_ticker="NYSE:AAA", yahoo_ticker="AAA", name="Alpha Inc", perf_6m=25.0, pe_ttm=6.0,
                 roe_ttm=18.0, revenue_cagr_5y=8.0, net_debt=50.0, ebitda_ttm=100.0),
        tv_stock(tv_ticker="OSL:BBB", yahoo_ticker="BBB.OL", market="norway", exchange="OSL", country="Norway",
                 name="Bravo ASA", sector="Transportation", industry="Marine Shipping", currency="NOK",
                 reporting_currency="NOK", market_cap=5.0e9, price=100.0, avg_volume_3m=50_000.0,
                 ema=90.0, perf_6m=40.0, pe_ttm=9.0, roe_ttm=25.0, revenue_cagr_5y=12.0,
                 net_debt=-20.0, ebitda_ttm=40.0),
        tv_stock(tv_ticker="XETR:CCC", yahoo_ticker="CCC.DE", market="germany", exchange="XETR",
                 country="Germany", name="Charlie AG", currency="EUR", reporting_currency="EUR",
                 roe_ttm=float("nan")),
    ]
    splits = pd.Series([2.0], index=pd.DatetimeIndex(["2024-06-15"]))
    income = statement({"2025-12-31": {"Diluted Average Shares": 202.0},
                        "2024-12-31": {"Diluted Average Shares": 200.0},
                        "2023-12-31": {"Diluted Average Shares": 100.0}})
    balance_a = yahoo_data().balance_sheet.copy()
    balance_a.loc["Ordinary Shares Number"] = 202.0
    balance_a.loc["Common Stock Equity"] = [300.0, 250.0, 150.0]
    data = {
        "NYSE:AAA": yahoo_data(ticker="AAA", income_statement=income, splits=splits, balance_sheet=balance_a,
                               shares_outstanding=202.0),
        "OSL:BBB": yahoo_data(ticker="BBB.OL", currency="NOK"),
    }
    return stocks, data
