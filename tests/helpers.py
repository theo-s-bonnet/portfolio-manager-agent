"""Factories of fake data for the tests of the filters, the score and the report."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from investment_agent.engine.config import load_config
from investment_agent.strategies.higgons import CONFIG_PATH, REQUIRED_KEYS

DATE = dt.date(2026, 10, 24)    # a Saturday
RATES = {"EUR": 1.0, "USD": 0.9, "GBP": 1.15, "GBX": 0.0115, "NOK": 0.085, "SEK": 0.09}


def config() -> dict:
    return load_config(CONFIG_PATH, REQUIRED_KEYS)


def unix_time(day: str) -> int:
    """Unix time (seconds) of a day at 00:00 UTC, as in the TradingView field first_bar_time."""
    return int(pd.Timestamp(day, tz="UTC").timestamp())


def tv_stock(**changes) -> dict:
    """A European stock that passes every rule; `changes` replaces fields."""
    v = {
        "tv_ticker": "XETR:TEST", "yahoo_ticker": "TEST.DE", "market": "germany", "type": "stock",
        "subtype": ["common"], "primary": True, "exchange": "XETR", "country": "Germany",
        "isin": "DE000TEST0001", "name": "Test AG", "sector": "Producer Manufacturing",
        "industry": "Industrial Machinery", "currency": "EUR", "reporting_currency": "EUR",
        "market_cap": 1.0e9, "price": 50.0,
        "avg_volume_3m": 100_000.0, "pe_ttm": 8.0, "eps_ttm": 6.25, "roe_ttm": 20.0,
        "revenue_cagr_5y": 10.0, "net_debt": 100.0, "ebitda_ttm": 200.0,
        "net_income_ttm": 1.0e8, "ema": 45.0, "perf_6m": 15.0,
        "first_bar_time": unix_time("2015-01-02"),
    }
    v.update(changes)
    return v


def universe(*stocks: dict) -> pd.DataFrame:
    return pd.DataFrame(list(stocks))


def fake_result(stocks: list[dict], cfg: dict | None = None):
    """Chains the rules and the engine guard without network access (as engine/runner.py does);
    returns a RunResult. The Yahoo ticker of each stock is its `yahoo_ticker` field."""
    import investment_agent.strategies.higgons as strategy
    from investment_agent.engine.report import RunResult, strategy_presentation
    from investment_agent.engine.runner import add_mapping_status
    from investment_agent.engine.statuses import (
        ELIGIBLE_COLUMN, FIRST_FAILED_COLUMN, eligibility, first_failed_rule)

    cfg = cfg or config()
    df = strategy.filter_tv(universe(*stocks), cfg, RATES, DATE)
    df["yahoo_ticker"] = pd.Series([v.get("yahoo_ticker") for v in stocks], index=df.index, dtype=object)
    df = add_mapping_status(df)
    presentation = strategy_presentation(strategy, cfg)
    columns = [c for c, *_ in presentation.steps]
    df[ELIGIBLE_COLUMN] = eligibility(df, columns)
    df[FIRST_FAILED_COLUMN] = first_failed_rule(df, columns)
    return RunResult(
        run_date=DATE, universe=df, ranking=strategy.rank(df, cfg),
        presentation=presentation, n_markets=2,
        warnings={"TradingView markets with errors or truncated": []},
    )


def three_stocks() -> list[dict]:
    """Stocks of the reference report: two eligible stocks, one excluded because its ROE is
    missing."""
    return [
        tv_stock(tv_ticker="EURONEXT:AAA", yahoo_ticker="AAA.PA", market="france", exchange="EURONEXT",
                 country="France", isin="FR000TEST0A1", name="Alpha SA", perf_6m=25.0, pe_ttm=6.0,
                 roe_ttm=18.0, revenue_cagr_5y=8.0, net_debt=50.0, ebitda_ttm=100.0),
        tv_stock(tv_ticker="OSL:BBB", yahoo_ticker="BBB.OL", market="norway", exchange="OSL", country="Norway",
                 isin="NO000TEST0B1", name="Bravo ASA", sector="Transportation", industry="Marine Shipping",
                 currency="NOK", reporting_currency="NOK", market_cap=5.0e9, price=100.0,
                 avg_volume_3m=50_000.0, ema=90.0, perf_6m=40.0, pe_ttm=9.0, roe_ttm=25.0,
                 revenue_cagr_5y=12.0, net_debt=-20.0, ebitda_ttm=40.0),
        tv_stock(tv_ticker="XETR:CCC", yahoo_ticker="CCC.DE", name="Charlie AG", roe_ttm=float("nan")),
    ]
