"""Conversion rates to the euro (Yahoo closing price of {CUR}EUR=X).

EUR = 1; GBX (pence) = GBP rate / 100. A missing or invalid rate is never replaced: the
currency is left out of the result and the amounts concerned become `missing`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from investment_agent.engine.finance import is_number

log = logging.getLogger(__name__)

BASE_CURRENCY = "EUR"
# Subunits: currency -> (main currency, divisor). Yahoo writes "GBp", TradingView "GBX".
SUBUNITS = {"GBX": ("GBP", 100), "GBp": ("GBP", 100)}

Downloader = Callable[[list[str]], dict[str, float]]


def rate_symbol(currency: str) -> str:
    return f"{currency}{BASE_CURRENCY}=X"


def download_yahoo_rates(symbols: list[str]) -> dict[str, float]:
    """Last valid closing price of each symbol ({CUR}EUR=X); left out on error."""
    import yfinance as yf  # local import: unit tests do not need yfinance

    if not symbols:
        return {}
    try:
        prices = yf.download(symbols, period="5d", progress=False, auto_adjust=False)["Close"]
    except Exception as e:  # network, unexpected format
        log.warning("exchange rates unavailable (%s): %s", ", ".join(symbols), e)
        return {}
    if not hasattr(prices, "columns"):  # a single series
        prices = prices.to_frame(name=symbols[0])
    result = {}
    for symbol in symbols:
        if symbol in prices.columns:
            values = prices[symbol].dropna()
            if len(values):
                result[symbol] = float(values.iloc[-1])
    return result


def eur_rates(currencies: Iterable[str], download: Downloader | None = None) -> dict[str, float]:
    """{currency: value in euros of one unit}, for the currencies whose rate is available."""
    download = download or download_yahoo_rates
    currencies = {c for c in currencies if isinstance(c, str) and c}
    mains = {SUBUNITS.get(c, (c, 1))[0] for c in currencies} - {BASE_CURRENCY}
    symbols = sorted(rate_symbol(c) for c in mains)
    raw = download(symbols) if symbols else {}

    main_rates = {BASE_CURRENCY: 1.0}
    for currency in mains:
        value = raw.get(rate_symbol(currency))
        if is_number(value) and value > 0:
            main_rates[currency] = float(value)
        else:
            log.warning("rate %s missing: amounts in %s marked missing", rate_symbol(currency), currency)

    result = {}
    for currency in currencies:
        main, divisor = SUBUNITS.get(currency, (currency, 1))
        if main in main_rates:
            result[currency] = main_rates[main] / divisor
    return result


def to_eur(amount, currency, rates: dict[str, float]) -> float | None:
    """Amount in euros, or None if the amount or the rate is missing (never a 0 assumed)."""
    if not is_number(amount) or currency not in rates:
        return None
    return float(amount) * rates[currency]
