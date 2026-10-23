"""Rules of the Higgons strategy (universe and eligibility filters): one pure function per rule.

Each function `rX_Y(df, cfg)` receives the universe (columns named after the internal names
of `tradingview_fields`, plus the derived columns of `add_derived_columns`) and returns two
Series aligned on the index of `df`:
- the status: `pass`, `fail` or `missing` (R4.11: missing data is never replaced);
- the reason: measured value and threshold, or the missing data (empty or explanatory on `pass`).

Operators are fixed by the rule (<=, <, >=, >); thresholds are always read from the
configuration, with the tolerance `technical.comparison_tolerance` for equalities. No numeric
constant other than 0, 1 and 100 in this file (checked by an AST test).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import numpy as np
import pandas as pd

from investment_agent.engine.data.fx import to_eur
from investment_agent.engine.finance import is_number, safe_ratio
from investment_agent.engine.statuses import FAIL, MISSING, PASS, WARNINGS_COLUMN, reason_column

Rule = Callable[[pd.DataFrame, dict], tuple[pd.Series, pd.Series]]

# --- helpers -----------------------------------------------------------------------------


def _number(x) -> float | None:
    return float(x) if is_number(x) else None


def _text(x) -> str | None:
    return x.strip() if isinstance(x, str) and x.strip() else None


def _boolean(x) -> bool | None:
    return bool(x) if isinstance(x, (bool, np.bool_)) else None


def _list(x) -> list[str] | None:
    if isinstance(x, str):
        return [x]
    if isinstance(x, (list, tuple, np.ndarray)):
        return [str(e) for e in x]
    return None


def _date(x) -> pd.Timestamp | None:
    if x is None or (not isinstance(x, (pd.Timestamp, dt.date)) and pd.isna(x)):
        return None
    ts = pd.Timestamp(x)
    return None if pd.isna(ts) else ts


def _v(x: float) -> str:
    """Readable number in a reason: thousands separators, at most two decimals."""
    text = f"{x:,.2f}"
    text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _tol(cfg: dict) -> float:
    return cfg["technical"]["comparison_tolerance"]


def _ge(a: float, b: float, tol: float) -> bool:
    return a >= b - tol


def _le(a: float, b: float, tol: float) -> bool:
    return a <= b + tol


def _gt(a: float, b: float, tol: float) -> bool:
    return a > b + tol


def _lt(a: float, b: float, tol: float) -> bool:
    return a < b - tol


def _apply(df: pd.DataFrame, rule: Callable[[dict], tuple[str, str]]) -> tuple[pd.Series, pd.Series]:
    statuses, reasons = [], []
    for row in df.to_dict("records"):
        status, reason = rule(row)
        statuses.append(status)
        reasons.append(reason)
    return (pd.Series(statuses, index=df.index, dtype=object),
            pd.Series(reasons, index=df.index, dtype=object))


def _missing(*pairs: tuple[str, object]) -> str:
    return "missing: " + ", ".join(name for name, x in pairs if x is None)


# --- derived columns (deterministic calculations, no rule) -------------------------------


# Source of the retained P/E (column pe_source).
PE_TV, PE_CALC, PE_FALLBACK = "tv", "calc", "fallback"


def retained_pe(pe_ttm, eps_ttm, market_cap, net_income_ttm, tol: float):
    """(pe_calc, pe_retained, pe_source) of one stock; pure function.

    pe_calc = market cap / TTM net income (same currency: `reporting_currency`), not computed if
    the net income is <= 0 or missing. The retained P/E is the higher of the TradingView P/E
    and pe_calc (source `calc` if pe_calc exceeds the TradingView P/E beyond the tolerance,
    otherwise `tv`); pe_calc alone if the TradingView P/E is missing (`fallback`). It is
    undefined (None, None) if EPS, net income or the TradingView P/E is <= 0, or if pe_calc
    cannot be computed: only the fallback from the TradingView P/E to pe_calc is allowed.
    """
    pe_tv, eps, ni = _number(pe_ttm), _number(eps_ttm), _number(net_income_ttm)
    pe_calc = safe_ratio(market_cap, ni)
    undefined = any(x is not None and _le(x, 0, tol) for x in (eps, ni, pe_tv))
    if undefined or pe_calc is None:
        return pe_calc, None, None
    if pe_tv is None:
        return pe_calc, pe_calc, PE_FALLBACK
    if _gt(pe_calc, pe_tv, tol):
        return pe_calc, pe_calc, PE_CALC
    return pe_calc, pe_tv, PE_TV


def add_derived_columns(df: pd.DataFrame, rates: dict[str, float], tol: float) -> pd.DataFrame:
    """Adds market_cap_eur (R3.2), liquidity_eur (R3.3), net_debt_ebitda (R4.4),
    first_bar_date (R3.6) and the retained P/E (R4.1): pe_calc, pe_retained, pe_source.

    The market cap is converted with the currency of the amounts (`reporting_currency`), the
    liquidity (average volume x last price) with the price currency (`currency`): in London
    prices are in GBX while amounts are in GBP.
    The net debt / EBITDA ratio is not computed (NaN) if EBITDA is <= 0 or missing.
    first_bar_date: UTC day of `first_bar_time` (Unix time in seconds), NaT if missing.
    """
    df = df.copy()
    df["market_cap_eur"] = pd.Series(
        [to_eur(c, cur, rates) for c, cur in zip(df["market_cap"], df["reporting_currency"])],
        index=df.index, dtype="float64")
    traded_values = [
        float(v) * float(p) if is_number(v) and is_number(p) else None
        for v, p in zip(df["avg_volume_3m"], df["price"])
    ]
    df["liquidity_eur"] = pd.Series(
        [to_eur(m, cur, rates) for m, cur in zip(traded_values, df["currency"])],
        index=df.index, dtype="float64")
    df["net_debt_ebitda"] = pd.Series(
        [safe_ratio(nd, e) for nd, e in zip(df["net_debt"], df["ebitda_ttm"])],
        index=df.index, dtype="float64")
    seconds = pd.to_numeric(df["first_bar_time"], errors="coerce")
    df["first_bar_date"] = pd.to_datetime(seconds, unit="s").dt.normalize()
    pe = [retained_pe(*row, tol) for row in
          zip(df["pe_ttm"], df["eps_ttm"], df["market_cap"], df["net_income_ttm"])]
    df["pe_calc"] = pd.Series([calc for calc, _, _ in pe], index=df.index, dtype="float64")
    df["pe_retained"] = pd.Series([pe_ for _, pe_, _ in pe], index=df.index, dtype="float64")
    df["pe_source"] = pd.Series([source for _, _, source in pe], index=df.index, dtype=object)
    return df


def _conversion_reason(v: dict, amount: str, currency: str, label: str) -> str:
    if _number(v.get(amount)) is None:
        return f"{label} missing"
    code = _text(v.get(currency))
    if code is None:
        return f"currency of {label} missing"
    return f"exchange rate {code} missing"


# --- universe ----------------------------------------------------------------------------


def r3_1(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """PEA universe: market (listing country) in the universe, exchange other than OTC, primary
    listing, headquarters country (TradingView `country`) in the EU/EEA list."""
    u = cfg["universe"]
    markets, excluded = set(u["markets"]), set(u["excluded_exchanges"])
    countries = set(u["hq_countries"])

    def rule(v):
        market, exchange = _text(v.get("market")), _text(v.get("exchange"))
        primary, country = _boolean(v.get("primary")), _text(v.get("country"))
        if market is not None and market not in markets:
            return FAIL, f"market {market} outside the universe"
        if exchange is not None and exchange in excluded:
            return FAIL, f"exchange {exchange} excluded"
        if primary is False:
            return FAIL, "secondary listing"
        if country is not None and country not in countries:
            return FAIL, f"headquarters country {country} outside the EU/EEA"
        if market is None or exchange is None or primary is None or country is None:
            return MISSING, _missing(("market", market), ("exchange", exchange),
                                     ("primary listing", primary), ("headquarters country", country))
        return PASS, ""

    return _apply(df, rule)


def r3_2(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """market_cap_min_eur <= market cap in euros <= market_cap_max_eur."""
    u, tol = cfg["universe"], _tol(cfg)
    cmin, cmax = u["market_cap_min_eur"], u["market_cap_max_eur"]

    def rule(v):
        cap = _number(v.get("market_cap_eur"))
        if cap is None:
            return MISSING, _conversion_reason(v, "market_cap", "reporting_currency", "market cap")
        if _ge(cap, cmin, tol) and _le(cap, cmax, tol):
            return PASS, ""
        return FAIL, f"market cap {_v(cap)} EUR outside [{_v(cmin)} ; {_v(cmax)}]"

    return _apply(df, rule)


def r3_3(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Average traded value >= liquidity_min_eur_per_day (approximated: 90-day volume x last price)."""
    threshold, tol = cfg["universe"]["liquidity_min_eur_per_day"], _tol(cfg)

    def rule(v):
        liq = _number(v.get("liquidity_eur"))
        if liq is None:
            if _number(v.get("avg_volume_3m")) is None or _number(v.get("price")) is None:
                return MISSING, _missing(("average volume", _number(v.get("avg_volume_3m"))),
                                         ("price", _number(v.get("price"))))
            return MISSING, _conversion_reason(v, "price", "currency", "price")
        if _ge(liq, threshold, tol):
            return PASS, ""
        return FAIL, f"traded value {_v(liq)} EUR/day < {_v(threshold)}"

    return _apply(df, rule)


def r3_4(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Stocks only: allowed type, subtype not excluded, industry other than funds.

    A subtype listed in `typespec_exempt_markets` is allowed on those markets (German
    preference shares).
    """
    u = cfg["universe"]
    types, excluded = set(u["allowed_types"]), set(u["excluded_typespecs"])
    exempt = {spec: set(markets or []) for spec, markets in (u["typespec_exempt_markets"] or {}).items()}
    funds = set(u["excluded_fund_industries"])

    def rule(v):
        type_ = _text(v.get("type"))
        if type_ is None:
            return MISSING, "type missing"
        if type_ not in types:
            return FAIL, f"type {type_}"
        if excluded:
            specs = _list(v.get("subtype"))
            if specs is None:
                return MISSING, "subtype missing"
            market = _text(v.get("market"))
            forbidden = sorted(s for s in set(specs) & excluded if market not in exempt.get(s, set()))
            if forbidden:
                return FAIL, f"subtype {', '.join(forbidden)} (market {market})"
        industry = _text(v.get("industry"))
        if industry is None:
            return MISSING, "industry missing"
        if industry in funds:
            return FAIL, f"industry {industry}"
        return PASS, ""

    return _apply(df, rule)


def r3_5(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Banks and insurers excluded through the TradingView industry."""
    excluded = set(cfg["universe"]["excluded_bank_insurance_industries"])

    def rule(v):
        industry = _text(v.get("industry"))
        if industry is None:
            return MISSING, "industry missing"
        if industry in excluded:
            return FAIL, f"industry {industry}"
        return PASS, ""

    return _apply(df, rule)


def r3_6(df: pd.DataFrame, cfg: dict, run_date: dt.date) -> tuple[pd.Series, pd.Series]:
    """Data guard: at least price_history_months of prices, checked on the date of the symbol's
    first TradingView bar (first_bar_date).

    `pass` if first_bar_date <= run date - price_history_months months
    + history_start_tolerance_days. A ticker change resets the first bar: the 6-month performance
    and the EMA are then computed on the short history, so the stock is excluded as well.
    """
    u, t = cfg["universe"], cfg["technical"]
    limit = (pd.Timestamp(run_date) - pd.DateOffset(months=u["price_history_months"])
             + pd.Timedelta(days=t["history_start_tolerance_days"]))

    def rule(v):
        first = _date(v.get("first_bar_date"))
        if first is None:
            return MISSING, "first TradingView bar missing"
        if first > limit:
            return FAIL, (f"first price on {first:%Y-%m-%d}, less than "
                          f"{u['price_history_months']} months of history")
        return PASS, ""

    return _apply(df, rule)


# --- eligibility filters computed on TradingView data -----------------------------------


def _simple_threshold(column: str, label: str, operator: str, threshold_key: str, unit: str = ""):
    """Rule "value operator threshold" on a single column (R4.3, R4.6)."""
    compare = {">=": _ge, ">": _gt}[operator]
    fail_symbol = {">=": "<", ">": "≤"}[operator]

    def function(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
        threshold, tol = cfg["filters"][threshold_key], _tol(cfg)

        def rule(v):
            x = _number(v.get(column))
            if x is None:
                return MISSING, f"{label} missing"
            if compare(x, threshold, tol):
                return PASS, ""
            return FAIL, f"{label} {_v(x)}{unit} {fail_symbol} {_v(threshold)}{unit}"

        return _apply(df, rule)

    return function


def r4_1(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """0 < retained P/E <= pe_max (columns of retained_pe).

    EPS, TTM net income or TradingView P/E <= 0: P/E undefined, `fail`. TradingView P/E and
    pe_calc both missing, or pe_calc missing alone: `missing`. A missing EPS does not block the
    rule (R4.12 is then `missing`).
    """
    threshold, tol = cfg["filters"]["pe_max"], _tol(cfg)

    def rule(v):
        eps, ni, pe_tv = _number(v.get("eps_ttm")), _number(v.get("net_income_ttm")), _number(v.get("pe_ttm"))
        pe_calc, pe = _number(v.get("pe_calc")), _number(v.get("pe_retained"))
        source = _text(v.get("pe_source"))
        if eps is not None and _le(eps, 0, tol):
            return FAIL, f"EPS {_v(eps)} ≤ 0: P/E undefined"
        if ni is not None and _le(ni, 0, tol):
            return FAIL, f"TTM net income {_v(ni)} ≤ 0: P/E undefined"
        if pe_tv is not None and _le(pe_tv, 0, tol):
            return FAIL, f"TradingView P/E {_v(pe_tv)} ≤ 0"
        if pe is None:
            if pe_tv is None:
                return MISSING, "TradingView P/E and market cap / TTM net income missing"
            return MISSING, "market cap / TTM net income missing: the retained P/E needs both"
        if _le(pe, 0, tol):
            return FAIL, f"retained P/E {_v(pe)} ≤ 0 ({source})"
        if not _le(pe, threshold, tol):
            return FAIL, f"retained P/E {_v(pe)} > {_v(threshold)} ({source})"
        if source == PE_FALLBACK:
            return PASS, f"fallback: TradingView P/E missing, market cap / TTM net income = {_v(pe)}"
        if source == PE_CALC:
            return PASS, f"market cap / TTM net income {_v(pe_calc)} > TradingView P/E {_v(pe_tv)}"
        return PASS, ""

    return _apply(df, rule)


r4_3 = _simple_threshold("revenue_cagr_5y", "5-year revenue CAGR", ">=", "revenue_cagr_5y_min_pct", "%")
r4_3.__doc__ = "5-year revenue CAGR >= revenue_cagr_5y_min_pct (annual rate, in %)."
r4_6 = _simple_threshold("perf_6m", "6-month performance", ">=", "perf_6m_min_pct", "%")
r4_6.__doc__ = "Raw 6-month performance >= perf_6m_min_pct (in %)."


def r4_4(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Net debt / EBITDA TTM < net_debt_ebitda_max, computed in code (not TradingView's ratio).

    EBITDA <= 0: ratio not computed, `fail` (denominator, R4.12). Net cash: negative ratio,
    `pass`.
    """
    threshold, tol = cfg["filters"]["net_debt_ebitda_max"], _tol(cfg)

    def rule(v):
        nd, ebitda = _number(v.get("net_debt")), _number(v.get("ebitda_ttm"))
        if ebitda is not None and _le(ebitda, 0, tol):
            return FAIL, f"EBITDA {_v(ebitda)} ≤ 0: ratio not computed (R4.12)"
        if nd is None or ebitda is None:
            return MISSING, _missing(("net debt", nd), ("EBITDA", ebitda))
        ratio = _number(v.get("net_debt_ebitda"))
        if ratio is None:
            ratio = safe_ratio(nd, ebitda)
        if _lt(ratio, threshold, tol):
            return PASS, ""
        return FAIL, f"net debt/EBITDA {_v(ratio)} ≥ {_v(threshold)}"

    return _apply(df, rule)


def r4_5(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Price > ema_entry_pct % of the EMA, strict inequality."""
    pct, tol = cfg["filters"]["ema_entry_pct"], _tol(cfg)

    def rule(v):
        price, ema = _number(v.get("price")), _number(v.get("ema"))
        if price is None or ema is None:
            return MISSING, _missing(("price", price), ("EMA", ema))
        if _gt(price, pct / 100 * ema, tol):
            return PASS, ""
        ratio = price / ema * 100 if ema else float("nan")
        return FAIL, f"price at {_v(ratio)}% of the EMA (strict minimum {_v(pct)}%)"

    return _apply(df, rule)


def r4_12(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Data guard on the denominators: EBITDA TTM > 0 and EPS TTM > 0."""
    tol = _tol(cfg)

    def rule(v):
        ebitda, eps = _number(v.get("ebitda_ttm")), _number(v.get("eps_ttm"))
        negatives = [f"{name} {_v(x)} ≤ 0" for name, x in (("EBITDA", ebitda), ("EPS", eps))
                     if x is not None and _le(x, 0, tol)]
        if negatives:
            return FAIL, " ; ".join(negatives)
        if ebitda is None or eps is None:
            return MISSING, _missing(("EBITDA", ebitda), ("EPS", eps))
        return PASS, ""

    return _apply(df, rule)


# --- warnings (no effect on eligibility) ---------------------------------------------------

CATEGORY_PE_FALLBACK = "Retained P/E: fallback to market cap / TTM net income (TradingView P/E missing)"
CATEGORY_ISIN = "Statutory country (ISIN prefix) outside the EU/EEA: check PEA eligibility"


def warnings_column(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """List of (category, text) per stock, for the stocks that pass the universe rules.

    Retained P/E from the fallback (TradingView P/E missing). ISIN prefix outside the EU/EEA: the TradingView `country` is the declared (often
    operational) headquarters; the ISIN prefix usually gives the country of incorporation.
    """
    prefixes = tuple(str(p).upper() for p in cfg["technical"]["eu_eea_isin_prefixes"])
    in_universe = df[list(UNIVERSE_RULES)].eq(PASS).all(axis=1)
    result = []
    for ok, v in zip(in_universe, df.to_dict("records")):
        items: list[tuple[str, str]] = []
        if ok and v.get("pe_source") == PE_FALLBACK:
            items.append((CATEGORY_PE_FALLBACK, f"retained P/E {_v(v['pe_retained'])}"))
        isin = _text(v.get("isin"))
        if ok and isin is not None and not isin.upper().startswith(prefixes):
            items.append((CATEGORY_ISIN, f"ISIN {isin}, headquarters {_text(v.get('country'))}"))
        result.append(items)
    return pd.Series(result, index=df.index, dtype=object)


# --- sequence ----------------------------------------------------------------------------
# Evaluation order: universe, selection criteria, data guards. Every rule is evaluated for
# every stock; the report counts a stock at the first rule it fails.

UNIVERSE_RULES: dict[str, Rule] = {
    "R3.1": r3_1,
    "R3.2": r3_2,
    "R3.3": r3_3,
    "R3.4": r3_4,
    "R3.5": r3_5,
}
CRITERIA: dict[str, Rule] = {
    "R4.1": r4_1,
    "R4.3": r4_3,
    "R4.4": r4_4,
    "R4.5": r4_5,
    "R4.6": r4_6,
}
# Data guards: R3.6 (needs the run date, applied first), then these ones.
DATA_GUARDS: dict[str, Rule] = {
    "R4.12": r4_12,
}
RULE_COLUMNS = [*UNIVERSE_RULES, *CRITERIA, "R3.6", *DATA_GUARDS]


def filter_tv(df: pd.DataFrame, cfg: dict, rates: dict[str, float], run_date: dt.date) -> pd.DataFrame:
    """Derived columns, then one status column and one reason column per rule, and the
    `warnings` column.

    Every rule is evaluated for every stock. `rates`: exchange rates to the euro (R3.2, R3.3);
    `run_date`: date of the run (R3.6), so that the result is deterministic.
    """
    df = add_derived_columns(df, rates, _tol(cfg))
    for rule, function in {**UNIVERSE_RULES, **CRITERIA}.items():
        df[rule], df[reason_column(rule)] = function(df, cfg)
    df["R3.6"], df[reason_column("R3.6")] = r3_6(df, cfg, run_date)
    for rule, function in DATA_GUARDS.items():
        df[rule], df[reason_column(rule)] = function(df, cfg)
    df[WARNINGS_COLUMN] = warnings_column(df, cfg)
    return df
