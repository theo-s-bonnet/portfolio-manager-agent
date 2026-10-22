"""Universe and TTM ratios from the TradingView scanner (tradingview-screener package).

One call per market, with set_markets(market): amounts are then expressed in the market
currency (`fundamental_currency_code`). Do not use set_tickers on its own: it switches the
query to the "global" endpoint, where every amount is converted to USD.

The package's default query returns primary listings only (is_primary) and leaves out ETFs,
mutual funds and closed-end funds; the strategy filters check these points again.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping

import pandas as pd

log = logging.getLogger(__name__)

TICKER_COLUMN = "tv_ticker"
MARKET_COLUMN = "market"


def _default_factory():
    from tradingview_screener import Query  # local import: unit tests run without the package

    return Query()


def download_universe(
    markets: Iterable[str],
    fields: Mapping[str, str],
    limit: int,
    query_factory: Callable[[], object] | None = None,
) -> pd.DataFrame:
    """Downloads each market; columns: tv_ticker, market, then one internal name per field.

    `fields`: {internal name: TradingView field} (config.yaml, tradingview_fields).
    Markets with an error, or truncated (more rows than `limit`), are listed in
    df.attrs["market_errors"] and df.attrs["truncated_markets"]: never silently left out.
    """
    factory = query_factory or _default_factory
    tv_columns = list(dict.fromkeys(fields.values()))
    chunks: list[pd.DataFrame] = []
    errors: dict[str, str] = {}
    truncated: dict[str, int] = {}

    for market in markets:
        try:
            total, raw = (
                factory().set_markets(market).select(*tv_columns).limit(limit).get_scanner_data()
            )
        except Exception as e:  # network, unknown field, unknown market
            errors[market] = f"{type(e).__name__}: {e}"
            log.warning("TradingView, market %s failed: %s", market, e)
            continue
        if total > limit:
            truncated[market] = int(total)
            log.warning("TradingView, market %s truncated: %s rows for a limit of %s",
                        market, total, limit)
        df = pd.DataFrame({
            TICKER_COLUMN: raw["ticker"].to_numpy(),
            MARKET_COLUMN: market,
            **{name: raw[field].to_numpy() for name, field in fields.items()},
        })
        chunks.append(df)

    columns = [TICKER_COLUMN, MARKET_COLUMN, *fields]
    result = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=columns)
    result.attrs["market_errors"] = errors
    result.attrs["truncated_markets"] = truncated
    return result
