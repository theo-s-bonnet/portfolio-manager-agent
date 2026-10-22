"""Checks the TradingView field names used by the screener and compares them with Yahoo.

Usage (network required):
    python scripts/check_tv_fields.py

The script:
1. tests each candidate field on the `america` market (limit 5) and reports those that exist;
   also searches the scanner metadata for "revenue" + "5y" / "cagr" fields;
2. prints, for 5 reference stocks, the values of the useful fields, queried on their own
   market (as the screener does), and compares them with Yahoo;
3. shows the actual currency of `market_cap_basic` by comparing it with info["marketCap"];
4. lists the distinct values of `typespecs` on `america` and the number of stocks
   (type stock, primary listing) per exchange for each market of the universe.

This script is a one-off verification tool: it contains candidate field names, which is
forbidden in the rest of the code (the retained names go into config.yaml).
"""

from __future__ import annotations

import math
from collections import Counter

import requests
import yfinance as yf
from tradingview_screener import Query

METAINFO_URL = "https://scanner.tradingview.com/{market}/metainfo"

CONFIRMED_FIELDS = [
    "type", "is_primary", "market_cap_basic", "close", "price_earnings_ttm",
    "earnings_per_share_diluted_ttm", "ebitda", "net_debt", "EMA100", "Perf.6M",
    "average_volume_90d_calc", "sector", "industry", "country", "exchange",
]

CANDIDATES = {
    "ROE TTM (R4.2)": ["return_on_equity", "return_on_equity_fq"],
    "5-year revenue CAGR (R4.3)": ["total_revenue_cagr_5y", "total_revenue_5y_growth_fy"],
    "TradingView net debt/EBITDA": ["net_debt_to_ebitda_fq", "net_debt_to_ebitda_fy"],
    "TTM net income (R4.8)": ["net_income_ttm"],
    "Price currency": ["currency"],
    "Reporting currency": ["fundamental_currency_code"],
    "Subtype": ["typespecs", "subtype"],
}

REFERENCE_STOCKS = [
    # (TV ticker, TV market, Yahoo ticker)
    ("NYSE:MHO", "america", "MHO"),
    ("TSX:AC", "canada", "AC.TO"),
    ("OSL:BWLPG", "norway", "BWLPG.OL"),
    ("LSE:SRB", "uk", "SRB.L"),
    ("OMXCOP:HLUN_A", "denmark", "HLUN-A.CO"),
]

UNIVERSE_MARKETS = [
    "america", "canada", "austria", "belgium", "bulgaria", "croatia", "cyprus", "czech",
    "denmark", "estonia", "finland", "france", "germany", "greece", "hungary", "iceland",
    "ireland", "italy", "latvia", "lithuania", "luxembourg", "netherlands", "norway",
    "poland", "portugal", "romania", "slovakia", "slovenia", "spain", "sweden",
    "switzerland", "uk",
]


def field_exists(field: str) -> bool:
    try:
        Query().set_markets("america").select("name", field).limit(5).get_scanner_data()
        return True
    except Exception:  # the scanner returns an HTTP error for an unknown field
        return False


def metainfo_fields(market: str = "america") -> list[str]:
    response = requests.post(METAINFO_URL.format(market=market), json={}, timeout=30)
    response.raise_for_status()
    return [f["n"] for f in response.json()["fields"]]


def step_1() -> list[str]:
    print("\n=== 1. Fields ===")
    for field in CONFIRMED_FIELDS:
        print(f"  {'OK    ' if field_exists(field) else 'ABSENT'}  {field}")
    found: list[str] = []
    for need, candidates in CANDIDATES.items():
        ok = [c for c in candidates if field_exists(c)]
        found.extend(ok)
        print(f"  {need:32s} -> {ok or 'NONE'}")
    names = metainfo_fields()
    revenue_5y = [n for n in names if "revenue" in n and ("5y" in n or "cagr" in n)]
    print(f"  Metadata: 'revenue' + '5y'/'cagr' fields: {revenue_5y}")
    return found


def step_2_3(fields: list[str]) -> None:
    print("\n=== 2-3. Reference stocks (own market) and comparison with Yahoo ===")
    columns = list(dict.fromkeys(["name"] + CONFIRMED_FIELDS + fields))
    for tv, market, yahoo in REFERENCE_STOCKS:
        # set_tickers switches the query to the "global" endpoint (amounts converted to USD):
        # set_markets must come after it to query the stock's own market.
        _, df = Query().set_tickers(tv).set_markets(market).select(*columns).get_scanner_data()
        if df.empty:
            print(f"\n{tv}: not found on market {market}")
            continue
        row = df.iloc[0]
        print(f"\n{tv} ({market})")
        for c in columns:
            print(f"    {c:32s} {row.get(c)}")
        info = yf.Ticker(yahoo).info
        cap_y = info.get("marketCap")
        print(f"    Yahoo {yahoo}: currency={info.get('currency')} "
              f"financialCurrency={info.get('financialCurrency')} marketCap={cap_y} "
              f"sharesOutstanding={info.get('sharesOutstanding')}")
        cap_tv = row.get("market_cap_basic")
        if cap_y and cap_tv and not math.isnan(cap_tv):
            print(f"    market_cap_basic / Yahoo marketCap = {cap_tv / cap_y:.3f} "
                  f"(~1: same currency; ~100 or ~0.01: GBX/GBP)")


def step_4() -> None:
    print("\n=== 4. typespecs (america) and exchanges per market ===")
    _, df = (Query().set_markets("america").select("type", "typespecs", "is_primary")
             .limit(100000).get_scanner_data())
    counts = Counter()
    for t, specs in zip(df["type"], df["typespecs"]):
        for s in (specs or [""]):
            counts[f"{t}:{s}"] += 1
    for key, n in sorted(counts.items()):
        print(f"  {key:24s} {n}")
    for market in UNIVERSE_MARKETS:
        _, df = (Query().set_markets(market).select("type", "is_primary", "exchange")
                 .limit(100000).get_scanner_data())
        stocks = df[(df["type"] == "stock") & (df["is_primary"] == True)]  # noqa: E712
        print(f"  {market:12s} {dict(Counter(stocks['exchange']))}")


if __name__ == "__main__":
    found = step_1()
    step_2_3(found)
    step_4()
