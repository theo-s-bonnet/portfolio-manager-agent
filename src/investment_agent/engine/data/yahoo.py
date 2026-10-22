"""Yahoo data for one stock: daily prices, annual balance sheets and income statements,
splits, current share count and currency. One-day cache on disk.

No missing data is filled in: a missing item stays None (or empty) and the error is
recorded in `error`. The match check (currency and price compared with TradingView)
excludes Yahoo tickers that point to another listing.
"""

from __future__ import annotations

import datetime as dt
import logging
import pickle
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from investment_agent.engine.finance import is_number

log = logging.getLogger(__name__)

# Currencies written differently by TradingView and Yahoo (British pence).
EQUIVALENT_CURRENCIES = {"GBp": "GBX"}


@dataclass
class YahooData:
    ticker: str
    prices: pd.Series | None = None                # daily closes (not adjusted for dividends)
    balance_sheet: pd.DataFrame | None = None      # Ticker.balance_sheet (annual)
    income_statement: pd.DataFrame | None = None   # Ticker.income_stmt (annual)
    splits: pd.Series | None = None                # Ticker.splits
    shares_outstanding: float | None = None        # info["sharesOutstanding"]
    currency: str | None = None                    # info["currency"]
    error: str | None = None                       # download errors, separated by "; "

    @property
    def last_price(self) -> float | None:
        if self.prices is None:
            return None
        valid = self.prices.dropna()
        return float(valid.iloc[-1]) if len(valid) else None


Source = Callable[[str, dt.date], YahooData]


def _download_yfinance(yahoo_ticker: str, start_date: dt.date) -> YahooData:
    import yfinance as yf  # local import: unit tests do not need yfinance

    ticker = yf.Ticker(yahoo_ticker)
    data = YahooData(ticker=yahoo_ticker)
    errors: list[str] = []

    def read(name: str, function):
        try:
            return function()
        except Exception as e:  # each item is independent: record the error and go on
            errors.append(f"{name}: {type(e).__name__}: {e}")
            return None

    history = read("prices", lambda: ticker.history(start=start_date, auto_adjust=False))
    if history is not None and "Close" in history:
        data.prices = history["Close"]
    data.balance_sheet = read("balance sheet", lambda: ticker.balance_sheet)
    data.income_statement = read("income statement", lambda: ticker.income_stmt)
    data.splits = read("splits", lambda: ticker.splits)
    info = read("info", lambda: ticker.info) or {}
    shares = info.get("sharesOutstanding")
    data.shares_outstanding = float(shares) if is_number(shares) else None
    data.currency = info.get("currency")
    data.error = "; ".join(errors) or None
    return data


def cache_path(cache: Path, yahoo_ticker: str, day: dt.date) -> Path:
    name = yahoo_ticker.replace("/", "_").replace("\\", "_")
    return Path(cache) / day.isoformat() / f"{name}.pkl"


def download(
    yahoo_ticker: str,
    start_date: dt.date,
    cache: Path | None = None,
    *,
    today: dt.date | None = None,
    pause_s: float = 0,
    source: Source | None = None,
) -> YahooData:
    """Yahoo data of the stock; read from today's cache when it exists.

    `cache`: root folder (e.g. data/cache/yahoo); None disables the cache (--no-cache).
    The cache is dated: it is valid for one day. An incomplete download (error) is not
    cached. `pause_s` is applied after each network call.
    """
    source = source or _download_yfinance
    day = today or dt.date.today()
    file = cache_path(cache, yahoo_ticker, day) if cache is not None else None

    if file is not None and file.exists():
        try:
            with file.open("rb") as f:
                return pickle.load(f)
        except Exception as e:  # corrupt cache: download again
            log.warning("unreadable cache %s: %s", file, e)

    data = source(yahoo_ticker, start_date)
    if pause_s > 0:
        time.sleep(pause_s)

    if file is not None and data.error is None:
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("wb") as f:
            pickle.dump(data, f)
    return data


def _normalized_currency(currency: str | None) -> str | None:
    return EQUIVALENT_CURRENCIES.get(currency, currency) if currency else None


def check_match(
    data: YahooData,
    tv_currency: str | None,
    tv_price,
    max_gap_pct: float,
    tolerance: float = 0,
) -> str | None:
    """None if the Yahoo ticker matches the TradingView listing, otherwise the reason.

    Checks: non-empty price history; same currency (GBX and GBp are equivalent);
    last Yahoo price within `max_gap_pct` % of the TradingView price.
    `tolerance`: technical.comparison_tolerance (equality at the threshold is accepted).
    """
    last = data.last_price
    if last is None:
        return "empty Yahoo price history"
    yahoo_cur, tv_cur = _normalized_currency(data.currency), _normalized_currency(tv_currency)
    if yahoo_cur is None or tv_cur is None:
        return f"unknown currency (Yahoo: {data.currency}, TradingView: {tv_currency})"
    if yahoo_cur != tv_cur:
        return f"different currency (Yahoo: {data.currency}, TradingView: {tv_currency})"
    if not is_number(tv_price) or tv_price <= 0:
        return "TradingView price missing"
    gap_pct = abs(last / float(tv_price) - 1) * 100
    if gap_pct > max_gap_pct + tolerance:
        return (f"price differs by {gap_pct:.1f}% (Yahoo: {last:g}, "
                f"TradingView: {float(tv_price):g})")
    return None
