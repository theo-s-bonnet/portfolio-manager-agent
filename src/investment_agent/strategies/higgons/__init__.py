"""Higgons strategy: small and mid caps that are cheap (low P/E), profitable, growing,
with a sound balance sheet and in an uptrend.

Interface used by the engine (engine/runner.py): location of the configuration, required
keys, TradingView fields, filters of both stages, extraction of the Yahoo fundamentals.
No network call in this folder: the engine downloads, the strategy computes.
"""

from pathlib import Path

from investment_agent.strategies.higgons.filters import filter_tv, filter_yahoo
from investment_agent.strategies.higgons.fundamentals import (
    CATEGORY_SHARE_RATIO, CATEGORY_SPLIT, CATEGORY_TOTAL_DEBT, extract_yahoo, price_start_date)
from investment_agent.strategies.higgons.scoring import COMPONENT_COLUMNS, pct_column, rank

TITLE = "Higgons"

CONFIG_PATH = Path(__file__).with_name("config.yaml")

# Required keys of config.yaml, checked by engine.config.validate_config.
REQUIRED_KEYS: dict[str, list[str]] = {
    "universe": [
        "markets", "excluded_exchanges", "allowed_types", "excluded_typespecs",
        "excluded_fund_industries", "excluded_bank_insurance_industries",
        "market_cap_min_eur", "market_cap_max_eur", "liquidity_min_eur_per_day",
        "price_history_months", "balance_sheet_history_years",
    ],
    "filters": [
        "pe_ttm_max", "roe_ttm_min_pct", "revenue_cagr_5y_min_pct", "net_debt_ebitda_max",
        "ema_period", "ema_entry_pct", "perf_6m_min_pct", "debt_to_equity_decrease_min_points",
        "dilution_annual_max_pct", "dilution_years", "dilution_recent_max_pct",
    ],
    "score": ["weights", "direction"],
    "tradingview_fields": [
        "type", "subtype", "primary", "exchange", "country", "name", "sector", "industry",
        "currency", "reporting_currency", "market_cap", "price", "avg_volume_3m", "pe_ttm",
        "eps_ttm", "roe_ttm", "revenue_cagr_5y", "net_debt", "ebitda_ttm",
        "net_debt_ebitda_tv", "net_income_ttm", "ema", "perf_6m",
    ],
    "technical": [
        "comparison_tolerance", "balance_sheet_date_tolerance_days",
        "history_start_tolerance_days", "tv_yahoo_price_gap_max_pct",
        "suspect_share_ratio_min", "suspect_share_ratio_max", "yahoo_pause_s",
        "tradingview_limit",
    ],
}


# --- presentation in the report (engine/report.py) ------------------------------------

# Funnel steps, in evaluation order: TradingView stage, then (after the Yahoo match, an
# engine step) Yahoo stage. Cheap filters come first: Yahoo is queried for survivors only.
TV_STEPS = [
    ("R3.1", "R3.1 market, exchange and primary listing"),
    ("R3.2", "R3.2 market cap"),
    ("R3.3", "R3.3 liquidity (approximate)"),
    ("R3.4", "R3.4 stocks only"),
    ("R3.5", "R3.5 banks and insurers excluded"),
    ("R4.1", "R4.1 P/E TTM"),
    ("R4.2", "R4.2 ROE"),
    ("R4.3", "R4.3 5-year revenue growth"),
    ("R4.4", "R4.4 net debt / EBITDA"),
    ("R4.5", "R4.5 price above the EMA"),
    ("R4.6", "R4.6 6-month performance"),
    ("R4.8", "R4.8 TTM net income"),
    ("R4.12_tv", "R4.12 EBITDA and EPS denominators"),
]
YAHOO_STEPS = [
    ("R3.6", "R3.6 history (prices and balance sheets)"),
    ("R4.7", "R4.7 balance sheet (net debt)"),
    ("R4.9", "R4.9 book value per share"),
    ("R4.10a", "R4.10a structural dilution"),
    ("R4.10b", "R4.10b recent dilution"),
    ("R4.12_yf", "R4.12 equity in Y and Y-2"),
]
DESCRIPTION_COLUMNS = [("Name", "name"), ("Country", "country"), ("Sector", "sector")]
# Columns read by the engine: Yahoo match check and exchange rates to download.
PRICE_COLUMN = "price"
PRICE_CURRENCY_COLUMN = "currency"
CURRENCY_COLUMNS = ["currency", "reporting_currency"]
COUNTRY_COLUMN = "country"
WARNING_CATEGORIES = [CATEGORY_SPLIT, CATEGORY_SHARE_RATIO, CATEGORY_TOTAL_DEBT]
_COMPONENT_DISPLAY = {
    "momentum": ("Perf 6M", "signed_pct"),
    "value": ("P/E", "number"),
    "quality": ("ROE", "pct"),
    "growth": ("Rev. CAGR 5y", "pct"),
    "balance_sheet": ("ND/EBITDA", "number"),
}


def components(cfg: dict) -> list[tuple[str, str, str, str]]:
    """(label, value column, percentile column, format) of each score component."""
    result = []
    for component in cfg["score"]["weights"]:
        label, format_ = _COMPONENT_DISPLAY[component]
        result.append((label, COMPONENT_COLUMNS[component], pct_column(component), format_))
    return result


def tv_fields(cfg: dict) -> dict[str, str]:
    """{internal name: TradingView field} to download (config.yaml, tradingview_fields)."""
    return dict(cfg["tradingview_fields"])


__all__ = [
    "WARNING_CATEGORIES", "CONFIG_PATH", "REQUIRED_KEYS", "PRICE_COLUMN",
    "PRICE_CURRENCY_COLUMN", "COUNTRY_COLUMN", "DESCRIPTION_COLUMNS", "CURRENCY_COLUMNS",
    "TV_STEPS", "YAHOO_STEPS", "TITLE", "tv_fields", "rank", "components",
    "price_start_date", "extract_yahoo", "filter_tv", "filter_yahoo",
]
