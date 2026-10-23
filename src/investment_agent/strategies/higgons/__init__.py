"""Higgons strategy: small and mid caps that are cheap (low P/E), profitable, growing,
with a sound balance sheet and in an uptrend.

Interface used by the engine (engine/runner.py): location of the configuration, required
keys, TradingView fields, rules (`filter_tv`), score and presentation in the report.
No network call in this folder: the engine downloads, the strategy computes.
"""

from pathlib import Path

from investment_agent.engine.statuses import MAPPING_COLUMN, MAPPING_LABEL
from investment_agent.strategies.higgons.filters import (
    CATEGORY_ISIN, CATEGORY_PE_FALLBACK, CATEGORY_ROE_FALLBACK, CATEGORY_ROE_NEGATIVE, PE_FALLBACK,
    ROE_FALLBACK, filter_tv)
from investment_agent.strategies.higgons.scoring import COMPONENT_COLUMNS, pct_column, rank

TITLE = "Higgons"

CONFIG_PATH = Path(__file__).with_name("config.yaml")

# Required keys of config.yaml, checked by engine.config.validate_config.
REQUIRED_KEYS: dict[str, list[str]] = {
    "universe": [
        "markets", "hq_countries", "excluded_exchanges", "allowed_types", "excluded_typespecs",
        "typespec_exempt_markets", "excluded_fund_industries", "excluded_bank_insurance_industries",
        "market_cap_min_eur", "market_cap_max_eur", "liquidity_min_eur_per_day",
        "price_history_months",
    ],
    "filters": [
        "pe_max", "revenue_cagr_5y_min_pct", "net_debt_ebitda_max", "ema_period", "ema_entry_pct",
        "perf_6m_min_pct",
    ],
    "score": ["weights", "direction"],
    "tradingview_fields": [
        "type", "subtype", "primary", "exchange", "country", "isin", "name", "sector", "industry",
        "currency", "reporting_currency", "market_cap", "price", "avg_volume_3m", "pe_ttm",
        "eps_ttm", "roe_ttm", "shareholders_equity", "revenue_cagr_5y", "net_debt", "ebitda_ttm",
        "net_income_ttm", "ema", "perf_6m", "first_bar_time",
    ],
    "technical": [
        "comparison_tolerance", "history_start_tolerance_days", "tradingview_limit",
        "eu_eea_isin_prefixes",
    ],
}


# --- presentation in the report (engine/report.py) ------------------------------------

# Funnel, in evaluation order: (group label, [(status column, label)]). Every rule is evaluated
# for every stock; the report counts a stock at the first step it fails. The data guards come
# last, so that their rows count the good candidates lost because of the data. The Yahoo
# ticker guard is an engine step (no download).
FUNNEL: list[tuple[str, list[tuple[str, str]]]] = [
    ("Universe", [
        ("R3.1", "R3.1 PEA universe: market, headquarters, primary listing"),
        ("R3.2", "R3.2 market cap"),
        ("R3.3", "R3.3 liquidity (approximate)"),
        ("R3.4", "R3.4 stocks only (preferred shares excluded outside Germany)"),
        ("R3.5", "R3.5 banks and insurers excluded"),
    ]),
    ("Selection criteria", [
        ("R4.1", "R4.1 retained P/E"),
        ("R4.3", "R4.3 5-year revenue growth"),
        ("R4.4", "R4.4 net debt / EBITDA"),
        ("R4.5", "R4.5 price above the EMA"),
        ("R4.6", "R4.6 6-month performance"),
    ]),
    ("Data guards", [
        ("R3.6", "R3.6 price history (first TradingView bar)"),
        ("R4.11", "R4.11 data needed by the score"),
        ("R4.12", "R4.12 EBITDA and EPS denominators"),
        (MAPPING_COLUMN, MAPPING_LABEL),
    ]),
]
FUNNEL_NOTE = ("Data guards are not strategy rules: they exclude stocks whose data is missing or "
               "inconsistent.")
DESCRIPTION_COLUMNS = [("Name", "name"), ("Country", "country"), ("Sector", "sector")]
# Columns read by the engine: exchange rates to download, country of the eligible stocks.
CURRENCY_COLUMNS = ["currency", "reporting_currency"]
COUNTRY_COLUMN = "country"
# Strategy warning categories (column `warnings` filled by filter_tv), in report order.
WARNING_CATEGORIES = [CATEGORY_PE_FALLBACK, CATEGORY_ISIN, CATEGORY_ROE_FALLBACK, CATEGORY_ROE_NEGATIVE]
_COMPONENT_DISPLAY = {
    "momentum": ("Perf 6M", "signed_pct"),
    "value": ("P/E", "number"),
    "quality": ("ROE", "pct"),
    "growth": ("Rev. CAGR 5y", "pct"),
    "balance_sheet": ("ND/EBITDA", "number"),
}
# Fallback values flagged in the ranking and counted under the funnel.
_COMPONENT_MARKERS = {
    "value": {
        "marker_column": "pe_source", "marker_value": PE_FALLBACK, "marker": "\u2020",
        "marker_note": "\u2020 fallback: market cap / TTM net income (TradingView P/E missing)",
        "marker_count": "Retained P/E: {n} stocks of the universe use the fallback (TradingView P/E missing).",
    },
    "quality": {
        "marker_column": "roe_source", "marker_value": ROE_FALLBACK, "marker": "\u2021",
        "marker_note": ("\u2021 fallback: TTM net income / shareholders' equity of the last quarter "
                        "(TradingView ROE missing)"),
        "marker_count": "Retained ROE: {n} stocks of the universe use the fallback (TradingView ROE missing).",
    },
}


def components(cfg: dict) -> list[dict]:
    """Fields of engine.report.Component for each score component: label, value column,
    percentile column, format, and the optional fallback marker."""
    result = []
    for component in cfg["score"]["weights"]:
        label, format_ = _COMPONENT_DISPLAY[component]
        result.append({"label": label, "column": COMPONENT_COLUMNS[component],
                       "pct_column": pct_column(component), "format": format_,
                       **_COMPONENT_MARKERS.get(component, {})})
    return result


def tv_fields(cfg: dict) -> dict[str, str]:
    """{internal name: TradingView field} to download (config.yaml, tradingview_fields)."""
    return dict(cfg["tradingview_fields"])


__all__ = [
    "WARNING_CATEGORIES", "CONFIG_PATH", "REQUIRED_KEYS", "COUNTRY_COLUMN", "DESCRIPTION_COLUMNS",
    "CURRENCY_COLUMNS", "FUNNEL", "FUNNEL_NOTE", "TITLE", "tv_fields", "rank", "components",
    "filter_tv",
]
