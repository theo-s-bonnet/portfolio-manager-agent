"""Checks the TradingView fields used by the screener on the EU/EEA markets.

Usage (network required):
    python scripts/check_tv_fields.py

The script:
1. tests each field of config.yaml (tradingview_fields) on the `germany` market, and searches
   the scanner metadata for revenue CAGR fields (a 3-year CAGR would be listed there);
   `isin` and `first_bar_time` are accepted by the scan endpoint without being listed in the
   metadata;
2. prints, for a few reference stocks queried on their own market (as the screener does), the
   values of the fields, the ISIN, the date of the first bar and the market cap compared with
   Yahoo (same currency expected);
3. for each market of the universe (primary listings, type stock): exchanges, `typespecs`
   values, headquarters countries missing from universe.hq_countries, ISIN prefixes missing
   from technical.eu_eea_isin_prefixes, and stocks whose first bar is less than 6 months old.

This script is a one-off verification tool: it may contain candidate field names, which is
forbidden in the rest of the code (the retained names go into config.yaml).
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter

import pandas as pd
import requests
import yfinance as yf
from tradingview_screener import Query

from investment_agent.engine.config import load_config
from investment_agent.strategies.higgons import CONFIG_PATH, REQUIRED_KEYS

METAINFO_URL = "https://scanner.tradingview.com/{market}/metainfo"
CFG = load_config(CONFIG_PATH, REQUIRED_KEYS)
FIELDS = CFG["tradingview_fields"]

REFERENCE_STOCKS = [
    # (TV ticker, TV market, Yahoo ticker)
    ("XETR:SAP", "germany", "SAP.DE"),
    ("EURONEXT:TEP", "france", "TEP.PA"),
    ("OSL:WAWI", "norway", "WAWI.OL"),
    ("GPW:PGE", "poland", "PGE.WA"),
    ("OMXSTO:KAR", "sweden", "KAR.ST"),
]


def field_exists(field: str, market: str = "germany") -> bool:
    try:
        Query().set_markets(market).select("name", field).limit(5).get_scanner_data()
        return True
    except Exception:  # the scanner returns an HTTP error for an unknown field
        return False


def metainfo_fields(market: str = "germany") -> list[str]:
    response = requests.post(METAINFO_URL.format(market=market), json={}, timeout=30)
    response.raise_for_status()
    return [f["n"] for f in response.json()["fields"]]


def first_bar(seconds) -> str:
    try:
        return f"{pd.to_datetime(float(seconds), unit='s'):%Y-%m-%d}"
    except (TypeError, ValueError):
        return "missing"


def step_1() -> None:
    print("\n=== 1. Fields of config.yaml ===")
    for name, field in FIELDS.items():
        print(f"  {'OK    ' if field_exists(field) else 'ABSENT'}  {name:22s} {field}")
    names = metainfo_fields()
    cagr = sorted(n for n in names if "cagr" in n)
    print(f"  Metadata: CAGR fields: {cagr}")
    print(f"  3-year revenue CAGR available: {any('revenue' in n and '3y' in n for n in cagr)}")


def step_2() -> None:
    print("\n=== 2. Reference stocks (own market) and comparison with Yahoo ===")
    columns = list(dict.fromkeys(FIELDS.values()))
    for tv, market, yahoo in REFERENCE_STOCKS:
        # set_tickers switches the query to the "global" endpoint (amounts converted to USD):
        # set_markets must come after it to query the stock's own market.
        _, df = Query().set_tickers(tv).set_markets(market).select(*columns).get_scanner_data()
        if df.empty:
            print(f"\n{tv}: not found on market {market}")
            continue
        row = df.iloc[0]
        print(f"\n{tv} ({market})")
        for name, field in FIELDS.items():
            print(f"    {name:22s} {row.get(field)}")
        print(f"    first bar              {first_bar(row.get(FIELDS['first_bar_time']))}")
        info = yf.Ticker(yahoo).info
        cap_y, cap_tv = info.get("marketCap"), row.get(FIELDS["market_cap"])
        print(f"    Yahoo {yahoo}: currency={info.get('currency')} financialCurrency={info.get('financialCurrency')} "
              f"marketCap={cap_y}")
        if cap_y and cap_tv and not math.isnan(cap_tv):
            print(f"    market cap TradingView / Yahoo = {cap_tv / cap_y:.3f} (~1: same currency)")


def step_3() -> None:
    print("\n=== 3. Markets of the universe ===")
    countries, prefixes = set(CFG["universe"]["hq_countries"]), tuple(CFG["technical"]["eu_eea_isin_prefixes"])
    limit = pd.Timestamp(dt.date.today()) - pd.DateOffset(months=CFG["universe"]["price_history_months"])
    for market in CFG["universe"]["markets"]:
        _, df = (Query().set_markets(market)
                 .select(FIELDS["type"], FIELDS["subtype"], FIELDS["primary"], FIELDS["exchange"],
                         FIELDS["country"], FIELDS["isin"], FIELDS["first_bar_time"])
                 .limit(CFG["technical"]["tradingview_limit"]).get_scanner_data())
        stocks = df[(df[FIELDS["type"]] == "stock") & (df[FIELDS["primary"]] == True)]  # noqa: E712
        specs = Counter(s for v in stocks[FIELDS["subtype"]] for s in (v if isinstance(v, list) else [v]))
        other_countries = Counter(c for c in stocks[FIELDS["country"]] if isinstance(c, str) and c not in countries)
        other_isin = Counter(i[:2] for i in stocks[FIELDS["isin"]]
                             if isinstance(i, str) and not i.upper().startswith(prefixes))
        recent = (pd.to_datetime(pd.to_numeric(stocks[FIELDS["first_bar_time"]], errors="coerce"), unit="s")
                  > limit).sum()
        print(f"\n  {market}: {len(stocks)} stocks; exchanges {dict(Counter(stocks[FIELDS['exchange']]))}")
        print(f"    typespecs {dict(specs)}")
        print(f"    headquarters outside the list {dict(other_countries)}")
        print(f"    ISIN prefixes outside the list {dict(other_isin)}")
        print(f"    first bar less than {CFG['universe']['price_history_months']} months old: {recent}")


if __name__ == "__main__":
    step_1()
    step_2()
    step_3()
