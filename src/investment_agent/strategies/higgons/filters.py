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
from investment_agent.engine.finance import is_number, least_squares_slope, safe_ratio
from investment_agent.engine.statuses import FAIL, MISSING, PASS, WARNINGS_COLUMN, reason_column
from investment_agent.strategies.higgons.scoring import COMPONENT_COLUMNS, GROWTH_COLUMN, ROE_COLUMN

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


# Source of the retained ROE (column roe_source).
ROE_TV, ROE_FALLBACK = "tv", "fallback"


def retained_roe(roe_ttm, net_income_ttm, shareholders_equity):
    """(roe_calc, roe_retained, roe_source) of one stock, in %; pure function.

    roe_calc = TTM net income / shareholders' equity of the last quarter x 100, not computed if
    the equity is <= 0 or missing (denominator rule). The TradingView ROE has priority, even
    <= 0 (`tv`); roe_calc is used only when it is missing (`fallback`).
    """
    ratio = safe_ratio(net_income_ttm, shareholders_equity)
    roe_calc = None if ratio is None else ratio * 100
    roe = _number(roe_ttm)
    if roe is not None:
        return roe_calc, roe, ROE_TV
    if roe_calc is not None:
        return roe_calc, roe_calc, ROE_FALLBACK
    return None, None, None


# Annual histories (TradingView `*_fy_h` fields: one value per closed fiscal year, the last
# closed fiscal year first). Element 0 is year N, element k is year N-k.
REVENUE_HISTORY, EPS_HISTORY = "revenue_fy_h", "eps_fy_h"
REVENUE_CAGR_3Y, EPS_GROWTH_3Y = "revenue_cagr_3y", GROWTH_COLUMN
# Why a 3-year growth measure is not computed.
HISTORY_MISSING = "history_missing"            # fewer than growth_years + 1 values, or one missing (R4.11)
DENOMINATOR = "denominator"                    # revenue of N-3 <= 0, or mean |EPS| = 0 (R4.12)
REVENUE_N_NOT_POSITIVE = "revenue_n_not_positive"   # revenue of N <= 0, revenue of N-3 > 0 (R4.3 fails)


def growth_years(cfg: dict) -> int:
    """Number of fiscal years between N-3 and N (filters.growth_years), a positive integer."""
    years = cfg["filters"]["growth_years"]
    if isinstance(years, bool) or not isinstance(years, int) or years < 1:
        raise ValueError(f"filters.growth_years must be a positive integer, not {years!r}")
    return years


def history_columns(history: str, years: int) -> list[str]:
    """Columns of the values N, N-1, ..., N-years of an annual history: revenue_fy_0 ... _3."""
    prefix = history.removesuffix("_h")
    return [f"{prefix}_{k}" for k in range(years + 1)]


def year_label(k: int) -> str:
    return f"N-{k}" if k else "N"


def history_values(x, years: int) -> list[float | None]:
    """Values N, N-1, ..., N-years of a TradingView annual history (list, last closed fiscal year
    first); None for an absent value, every value None if the field is not a list."""
    values = list(x) if isinstance(x, (list, tuple, np.ndarray)) else []
    values = [_number(e) for e in values[:years + 1]]
    return values + [None] * (years + 1 - len(values))


def revenue_cagr(history, years: int, tol: float) -> tuple[float | None, str | None]:
    """(CAGR in % per year, issue) from the annual revenue N, N-1, ..., N-years; pure function.

    CAGR = (revenue of N / revenue of N-years) ^ (1 / years) - 1. Not computed (None, issue) if a
    value is missing (HISTORY_MISSING), if the revenue of N-years is <= 0 (DENOMINATOR) or if
    the revenue of N is <= 0 while N-years is > 0 (REVENUE_N_NOT_POSITIVE).
    """
    values = history_values(history, years)
    if any(v is None for v in values):
        return None, HISTORY_MISSING
    last, first = values[0], values[years]
    if _le(first, 0, tol):
        return None, DENOMINATOR
    if _le(last, 0, tol):
        return None, REVENUE_N_NOT_POSITIVE
    return ((last / first) ** (1 / years) - 1) * 100, None


def eps_growth(history, years: int, tol: float) -> tuple[float | None, str | None]:
    """(growth in % per year, issue) from the annual diluted EPS N, N-1, ..., N-years; pure function.

    Least-squares slope of the EPS against the times 0, 1, ..., years (oldest year first),
    divided by the mean of the absolute EPS values, x 100. Negative EPS are allowed (a
    turnaround counts as growth). Not computed if a value is missing (HISTORY_MISSING) or if
    the mean of the absolute values is 0 (DENOMINATOR).
    """
    values = history_values(history, years)
    if any(v is None for v in values):
        return None, HISTORY_MISSING
    oldest_first = values[::-1]
    mean_abs = sum(abs(v) for v in oldest_first) / len(oldest_first)
    if _le(mean_abs, 0, tol):
        return None, DENOMINATOR
    return least_squares_slope(oldest_first) / mean_abs * 100, None


def add_derived_columns(df: pd.DataFrame, rates: dict[str, float], tol: float, years: int) -> pd.DataFrame:
    """Adds market_cap_eur (R3.2), liquidity_eur (R3.3), net_debt_ebitda (R4.4),
    first_bar_date (R3.6), the retained P/E (R4.1: pe_calc, pe_retained, pe_source), the
    retained ROE (score: roe_calc, roe_retained, roe_source), the annual values N to N-years
    (revenue_fy_0..., eps_fy_0...), the 3-year revenue CAGR (R4.3) and the 3-year EPS growth
    (score). The raw history lists are replaced by their values N to N-years.

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
    roe = [retained_roe(*row) for row in
           zip(df["roe_ttm"], df["net_income_ttm"], df["shareholders_equity"])]
    df["roe_calc"] = pd.Series([calc for calc, _, _ in roe], index=df.index, dtype="float64")
    df["roe_retained"] = pd.Series([roe_ for _, roe_, _ in roe], index=df.index, dtype="float64")
    df["roe_source"] = pd.Series([source for _, _, source in roe], index=df.index, dtype=object)
    for history, measure, function in ((REVENUE_HISTORY, REVENUE_CAGR_3Y, revenue_cagr),
                                       (EPS_HISTORY, EPS_GROWTH_3Y, eps_growth)):
        values = [history_values(x, years) for x in df[history]]
        for k, column in enumerate(history_columns(history, years)):
            df[column] = pd.Series([v[k] for v in values], index=df.index, dtype="float64")
        df[measure] = pd.Series([function(v, years, tol)[0] for v in values], index=df.index,
                                dtype="float64")
    return df.drop(columns=[REVENUE_HISTORY, EPS_HISTORY])


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
    listing, headquarters country (TradingView `country`) and ISIN prefix both in the EU/EEA.

    The `country` field is the declared, often operational, headquarters; the ISIN prefix usually
    gives the country of incorporation. Neither is reliable on its own: both are required.
    """
    u = cfg["universe"]
    markets, excluded = set(u["markets"]), set(u["excluded_exchanges"])
    countries = set(u["hq_countries"])
    prefixes = tuple(str(p).upper() for p in u["eu_eea_isin_prefixes"])

    def rule(v):
        market, exchange = _text(v.get("market")), _text(v.get("exchange"))
        primary, country = _boolean(v.get("primary")), _text(v.get("country"))
        isin = _text(v.get("isin"))
        if market is not None and market not in markets:
            return FAIL, f"market {market} outside the universe"
        if exchange is not None and exchange in excluded:
            return FAIL, f"exchange {exchange} excluded"
        if primary is False:
            return FAIL, "secondary listing"
        if country is not None and country not in countries:
            return FAIL, f"headquarters country {country} outside the EU/EEA"
        if isin is not None and not isin.upper().startswith(prefixes):
            return FAIL, f"ISIN {isin}: prefix outside the EU/EEA"
        if market is None or exchange is None or primary is None or country is None or isin is None:
            return MISSING, _missing(("market", market), ("exchange", exchange),
                                     ("primary listing", primary), ("headquarters country", country),
                                     ("ISIN", isin))
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


r4_6 = _simple_threshold("perf_6m", "6-month performance", ">=", "perf_6m_min_pct", "%")
r4_6.__doc__ = "Raw 6-month performance >= perf_6m_min_pct (in %)."


def _row_history(v: dict, history: str, years: int) -> list[float | None]:
    return [_number(v.get(c)) for c in history_columns(history, years)]


def r4_3(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Revenue growth, two conditions: 5-year CAGR (TradingView) >= revenue_cagr_5y_min_pct and
    3-year CAGR (computed from the annual revenue) >= revenue_cagr_3y_min_pct, in % per year.

    A revenue of N <= 0 (with N-3 > 0) fails. A 3-year CAGR that cannot be computed (history
    incomplete: R4.11; revenue of N-3 <= 0: R4.12) does not block this rule: the data guard
    excludes the stock. The failure reason shows both measures.
    """
    f, tol, years = cfg["filters"], _tol(cfg), growth_years(cfg)
    min_5y, min_3y = f["revenue_cagr_5y_min_pct"], f["revenue_cagr_3y_min_pct"]
    label_3y = f"{years}-year revenue CAGR"

    def rule(v):
        cagr_5y = _number(v.get("revenue_cagr_5y"))
        history = _row_history(v, REVENUE_HISTORY, years)
        cagr_3y, issue = revenue_cagr(history, years, tol)
        fail_5y = cagr_5y is not None and not _ge(cagr_5y, min_5y, tol)
        fail_3y = (cagr_3y is not None and not _ge(cagr_3y, min_3y, tol)) or issue == REVENUE_N_NOT_POSITIVE
        if cagr_5y is None:
            part_5y = "5-year revenue CAGR missing"
        else:
            part_5y = f"5-year revenue CAGR {_v(cagr_5y)}% {'<' if fail_5y else '≥'} {_v(min_5y)}%"
        if cagr_3y is not None:
            part_3y = f"{label_3y} {_v(cagr_3y)}% {'<' if fail_3y else '≥'} {_v(min_3y)}%"
        elif issue == REVENUE_N_NOT_POSITIVE:
            part_3y = f"{label_3y} undefined: revenue N {_v(history[0])} ≤ 0"
        elif issue == DENOMINATOR:
            part_3y = f"{label_3y} not computed: revenue {year_label(years)} ≤ 0 (R4.12)"
        else:
            part_3y = f"{label_3y} not computed: annual revenue missing (R4.11)"
        if fail_5y or fail_3y:
            return FAIL, f"{part_5y}; {part_3y}"
        if cagr_5y is None:
            return MISSING, part_5y
        if issue is not None:
            return PASS, part_3y
        return PASS, ""

    return _apply(df, rule)


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


def _roe_missing_reason(v: dict) -> str:
    equity = _number(v.get("shareholders_equity"))
    if equity is not None and equity <= 0:
        return f"ROE missing; fallback not computed: shareholders' equity {_v(equity)} ≤ 0"
    return "ROE missing; fallback not computed: shareholders' equity / TTM net income missing"


def _history_missing_reason(v: dict, history: str, years: int, label: str) -> str | None:
    values = _row_history(v, history, years)
    missing = [year_label(k) for k, x in enumerate(values) if x is None]
    return f"annual {label} missing: {', '.join(missing)}" if missing else None


def r4_11(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Data guard: the measures of the score and the annual histories are available.

    The retained ROE is the only score measure that no criterion checks: when it is missing,
    the reason explains why the fallback was not computed. The annual revenue and diluted EPS
    of N to N-3 must all be present (3-year revenue CAGR of R4.3, EPS growth of the score). The
    other score measures are already reported by their criterion. A ROE <= 0 is a value; an EPS
    growth not computed because the mean |EPS| is 0 is left to R4.12.
    """
    years = growth_years(cfg)
    columns = [COMPONENT_COLUMNS[c] for c in cfg["score"]["weights"] if c in COMPONENT_COLUMNS]
    others = [c for c in columns if c not in (ROE_COLUMN, EPS_GROWTH_3Y)]

    def rule(v):
        reasons = [_roe_missing_reason(v)] if _number(v.get(ROE_COLUMN)) is None else []
        for history, label in ((REVENUE_HISTORY, "revenue"), (EPS_HISTORY, "diluted EPS")):
            reason = _history_missing_reason(v, history, years, label)
            if reason:
                reasons.append(reason)
        if reasons:
            return MISSING, " ; ".join(reasons)
        missing = [c for c in others if _number(v.get(c)) is None]
        if missing:
            return MISSING, f"score data missing: {', '.join(missing)}"
        if v.get("roe_source") == ROE_FALLBACK:
            return PASS, (f"fallback: TradingView ROE missing, TTM net income / shareholders' equity "
                          f"= {_v(v[ROE_COLUMN])}%")
        return PASS, ""

    return _apply(df, rule)


def r4_12(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Data guard on the denominators: EBITDA TTM > 0, EPS TTM > 0, revenue of N-3 > 0 (3-year
    revenue CAGR) and mean |EPS| of N-3 to N > 0 (EPS growth of the score).

    The two historical denominators are checked only when the history is complete (otherwise
    R4.11 reports the missing values).
    """
    tol, years = _tol(cfg), growth_years(cfg)

    def rule(v):
        ebitda, eps = _number(v.get("ebitda_ttm")), _number(v.get("eps_ttm"))
        negatives = [f"{name} {_v(x)} ≤ 0" for name, x in (("EBITDA", ebitda), ("EPS", eps))
                     if x is not None and _le(x, 0, tol)]
        revenue = _row_history(v, REVENUE_HISTORY, years)
        if revenue_cagr(revenue, years, tol)[1] == DENOMINATOR:
            negatives.append(f"revenue {year_label(years)} {_v(revenue[years])} ≤ 0: "
                             f"{years}-year revenue CAGR not computed")
        eps_values = _row_history(v, EPS_HISTORY, years)
        if eps_growth(eps_values, years, tol)[1] == DENOMINATOR:
            negatives.append(f"mean absolute EPS of {year_label(years)} to N is 0: EPS growth not computed")
        if negatives:
            return FAIL, " ; ".join(negatives)
        if ebitda is None or eps is None:
            return MISSING, _missing(("EBITDA", ebitda), ("EPS", eps))
        return PASS, ""

    return _apply(df, rule)


# --- warnings (no effect on eligibility) ---------------------------------------------------

CATEGORY_PE_FALLBACK = "Retained P/E: fallback to market cap / TTM net income (TradingView P/E missing)"
CATEGORY_ROE_FALLBACK = ("Retained ROE: fallback to TTM net income / shareholders' equity "
                         "(TradingView ROE missing)")
CATEGORY_ROE_NEGATIVE = "ROE ≤ 0, ranked last on quality"


def warnings_column(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """List of (category, text) per stock.

    For the stocks that pass the universe rules: retained P/E or retained ROE from the fallback.
    For the stocks that also pass the selection criteria: ROE <= 0, ranked last on quality.
    """
    tol = _tol(cfg)
    in_universe = df[list(UNIVERSE_RULES)].eq(PASS).all(axis=1)
    selected = in_universe & df[list(CRITERIA)].eq(PASS).all(axis=1)
    result = []
    for ok, sel, v in zip(in_universe, selected, df.to_dict("records")):
        items: list[tuple[str, str]] = []
        if ok and v.get("pe_source") == PE_FALLBACK:
            items.append((CATEGORY_PE_FALLBACK, f"retained P/E {_v(v['pe_retained'])}"))
        if ok and v.get("roe_source") == ROE_FALLBACK:
            items.append((CATEGORY_ROE_FALLBACK, f"retained ROE {_v(v[ROE_COLUMN])}%"))
        roe = _number(v.get(ROE_COLUMN))
        if sel and roe is not None and _le(roe, 0, tol):
            items.append((CATEGORY_ROE_NEGATIVE, f"ROE {_v(roe)}%"))
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
    "R4.11": r4_11,
    "R4.12": r4_12,
}
RULE_COLUMNS = [*UNIVERSE_RULES, *CRITERIA, "R3.6", *DATA_GUARDS]


def filter_tv(df: pd.DataFrame, cfg: dict, rates: dict[str, float], run_date: dt.date) -> pd.DataFrame:
    """Derived columns, then one status column and one reason column per rule, and the
    `warnings` column.

    Every rule is evaluated for every stock. `rates`: exchange rates to the euro (R3.2, R3.3);
    `run_date`: date of the run (R3.6), so that the result is deterministic.
    """
    df = add_derived_columns(df, rates, _tol(cfg), growth_years(cfg))
    for rule, function in {**UNIVERSE_RULES, **CRITERIA}.items():
        df[rule], df[reason_column(rule)] = function(df, cfg)
    df["R3.6"], df[reason_column("R3.6")] = r3_6(df, cfg, run_date)
    for rule, function in DATA_GUARDS.items():
        df[rule], df[reason_column(rule)] = function(df, cfg)
    df[WARNINGS_COLUMN] = warnings_column(df, cfg)
    return df
