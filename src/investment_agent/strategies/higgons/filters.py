"""Rules of the Higgons strategy (universe and eligibility filters): one pure function per rule.

Each function `rX_Y(df, cfg)` receives the universe (columns named after the internal names
of `tradingview_fields`, plus the derived columns and the `yf_` columns of fundamentals.py)
and returns two Series aligned on the index of `df`:
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
from investment_agent.engine.statuses import FAIL, MISSING, PASS, reason_column

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


def add_derived_columns(df: pd.DataFrame, rates: dict[str, float]) -> pd.DataFrame:
    """Adds market_cap_eur (R3.2), liquidity_eur (R3.3) and net_debt_ebitda (R4.4).

    The market cap is converted with the currency of the amounts (`reporting_currency`), the
    liquidity (average volume x last price) with the price currency (`currency`): in London
    prices are in GBX while amounts are in GBP.
    The net debt / EBITDA ratio is not computed (NaN) if EBITDA is <= 0 or missing.
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
    """Market (country) in the universe, exchange other than OTC, primary listing."""
    u = cfg["universe"]
    markets, excluded = set(u["markets"]), set(u["excluded_exchanges"])

    def rule(v):
        market, exchange = _text(v.get("market")), _text(v.get("exchange"))
        primary = _boolean(v.get("primary"))
        if market is not None and market not in markets:
            return FAIL, f"market {market} outside the universe"
        if exchange is not None and exchange in excluded:
            return FAIL, f"exchange {exchange} excluded"
        if primary is False:
            return FAIL, "secondary listing"
        if market is None or exchange is None or primary is None:
            return MISSING, _missing(("market", market), ("exchange", exchange),
                                     ("primary listing", primary))
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
    """Stocks only: allowed type, subtype not excluded, industry other than funds."""
    u = cfg["universe"]
    types, excluded = set(u["allowed_types"]), set(u["excluded_typespecs"])
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
            forbidden = sorted(set(specs) & excluded)
            if forbidden:
                return FAIL, f"subtype {', '.join(forbidden)}"
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
    """At least price_history_months of prices and balance_sheet_history_years balance sheets,
    including Y and Y-2.

    The first valid price must be no later than (date - price_history_months months
    + history_start_tolerance_days). Missing data -> `fail`.
    """
    u, t = cfg["universe"], cfg["technical"]
    limit = (pd.Timestamp(run_date) - pd.DateOffset(months=u["price_history_months"])
             + pd.Timedelta(days=t["history_start_tolerance_days"]))
    n_min = u["balance_sheet_history_years"]

    def rule(v):
        first = _date(v.get("yf_first_price_date"))
        if first is None:
            return FAIL, "empty Yahoo price history"
        if first > limit:
            return FAIL, (f"first price on {first:%Y-%m-%d}, less than "
                          f"{u['price_history_months']} months of history")
        n = _number(v.get("yf_balance_sheet_count"))
        if n is None or n < n_min:
            return FAIL, f"{_v(n or 0)} annual balance sheets at Yahoo (minimum {n_min})"
        if _date(v.get("yf_date_y2")) is None:
            return FAIL, "Y-2 balance sheet not found at the expected date"
        return PASS, ""

    return _apply(df, rule)


# --- eligibility filters computed on TradingView data -----------------------------------


def _simple_threshold(column: str, label: str, operator: str, threshold_key: str, unit: str = ""):
    """Rule "value operator threshold" on a single column (R4.2, R4.3, R4.6)."""
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
    """0 < P/E TTM <= pe_ttm_max."""
    threshold, tol = cfg["filters"]["pe_ttm_max"], _tol(cfg)

    def rule(v):
        pe = _number(v.get("pe_ttm"))
        if pe is None:
            return MISSING, "P/E missing"
        if _le(pe, 0, tol):
            return FAIL, f"P/E {_v(pe)} ≤ 0"
        if _le(pe, threshold, tol):
            return PASS, ""
        return FAIL, f"P/E {_v(pe)} > {_v(threshold)}"

    return _apply(df, rule)


r4_2 = _simple_threshold("roe_ttm", "ROE", ">=", "roe_ttm_min_pct", "%")
r4_2.__doc__ = "ROE TTM >= roe_ttm_min_pct (in %)."
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


def r4_8(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Trailing twelve-month net income > 0."""
    tol = _tol(cfg)

    def rule(v):
        ni = _number(v.get("net_income_ttm"))
        if ni is None:
            return MISSING, "TTM net income missing"
        if _gt(ni, 0, tol):
            return PASS, ""
        return FAIL, f"TTM net income {_v(ni)} ≤ 0"

    return _apply(df, rule)


def r4_12_tv(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """TradingView denominators: EBITDA TTM > 0 and EPS TTM > 0."""
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


# --- eligibility filters computed on Yahoo data (yf_ columns of fundamentals.py) ----------


def r4_7(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Net cash in Y and Y-2, or a decrease of net debt / equity of at least N points.

    Net debt = Total Debt - cash and cash equivalents (short-term investments excluded).
    The ratio is computed only if equity is > 0 in Y and Y-2; otherwise `fail` (R4.12).
    Low but positive equity: the ratio is computed as is, even if it becomes very large.
    """
    threshold, tol = cfg["filters"]["debt_to_equity_decrease_min_points"], _tol(cfg)

    def rule(v):
        nd_y, nd_y2 = _number(v.get("yf_net_debt_y")), _number(v.get("yf_net_debt_y2"))
        if nd_y is None or nd_y2 is None:
            return MISSING, _missing(
                ("total debt Y", _number(v.get("yf_debt_y"))),
                ("cash Y", _number(v.get("yf_cash_y"))),
                ("total debt Y-2", _number(v.get("yf_debt_y2"))),
                ("cash Y-2", _number(v.get("yf_cash_y2"))))
        if _lt(nd_y, 0, tol) and _lt(nd_y2, 0, tol):
            return PASS, "net cash in Y and Y-2"
        eq_y, eq_y2 = _number(v.get("yf_equity_y")), _number(v.get("yf_equity_y2"))
        if (eq_y is not None and _le(eq_y, 0, tol)) or (eq_y2 is not None and _le(eq_y2, 0, tol)):
            return FAIL, "equity ≤ 0: ratio not computed (R4.12)"
        if eq_y is None or eq_y2 is None:
            return MISSING, _missing(("equity Y", eq_y), ("equity Y-2", eq_y2))
        ratio_y, ratio_y2 = nd_y / eq_y * 100, nd_y2 / eq_y2 * 100
        change = ratio_y - ratio_y2
        if _le(change, -threshold, tol):
            return PASS, f"net debt/equity {_v(ratio_y2)}% → {_v(ratio_y)}%"
        sign = "+" if change > 0 else ""
        return FAIL, (f"net debt/equity {_v(ratio_y2)}% → {_v(ratio_y)}% "
                      f"({sign}{_v(change)} points, minimum decrease {_v(threshold)})")

    return _apply(df, rule)


def r4_9(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Book value per share (split-adjusted diluted shares) strictly up from Y-2 to Y."""
    tol = _tol(cfg)

    def rule(v):
        eq_y, eq_y2 = _number(v.get("yf_equity_y")), _number(v.get("yf_equity_y2"))
        if (eq_y is not None and _le(eq_y, 0, tol)) or (eq_y2 is not None and _le(eq_y2, 0, tol)):
            return FAIL, "equity ≤ 0"
        n_y, n_y2 = _number(v.get("yf_diluted_shares_y")), _number(v.get("yf_diluted_shares_y2"))
        if eq_y is None or eq_y2 is None or n_y is None or n_y2 is None:
            return MISSING, _missing(("equity Y", eq_y), ("equity Y-2", eq_y2),
                                     ("diluted shares Y", n_y), ("diluted shares Y-2", n_y2))
        bvps_y, bvps_y2 = safe_ratio(eq_y, n_y), safe_ratio(eq_y2, n_y2)
        if bvps_y is None or bvps_y2 is None:
            return MISSING, "share count ≤ 0"
        if _gt(bvps_y, bvps_y2, tol):
            return PASS, ""
        return FAIL, f"book value per share {_v(bvps_y2)} → {_v(bvps_y)} (strict increase required)"

    return _apply(df, rule)


def r4_10a(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """(N_Y / N_Y-2)^(1/dilution_years) - 1 <= dilution_annual_max_pct (diluted, split-adjusted)."""
    f, tol = cfg["filters"], _tol(cfg)
    threshold, years = f["dilution_annual_max_pct"], f["dilution_years"]

    def rule(v):
        n_y, n_y2 = _number(v.get("yf_diluted_shares_y")), _number(v.get("yf_diluted_shares_y2"))
        if n_y is None or n_y2 is None:
            return MISSING, _missing(("diluted shares Y", n_y), ("diluted shares Y-2", n_y2))
        q = safe_ratio(n_y, n_y2)
        if q is None or q <= 0:
            return MISSING, "share count ≤ 0"
        rate = (q ** (1 / years) - 1) * 100
        if _le(rate, threshold, tol):
            return PASS, ""
        return FAIL, f"dilution {_v(rate)}%/yr > {_v(threshold)}%"

    return _apply(df, rule)


def r4_10b(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Current shares / shares at the close of Y (split-adjusted) - 1 <= dilution_recent_max_pct.

    Ratio outside [suspect_share_ratio_min, suspect_share_ratio_max]: `missing` (multiple share
    classes likely), reported in the warnings.
    """
    t, tol = cfg["technical"], _tol(cfg)
    threshold = cfg["filters"]["dilution_recent_max_pct"]

    def rule(v):
        current, at_close = _number(v.get("yf_shares_current")), _number(v.get("yf_shares_y"))
        if current is None or at_close is None:
            return MISSING, _missing(("current shares", current), ("shares at the close of Y", at_close))
        ratio = safe_ratio(current, at_close)
        if ratio is None:
            return MISSING, "share count at the close ≤ 0"
        if not t["suspect_share_ratio_min"] <= ratio <= t["suspect_share_ratio_max"]:
            return MISSING, f"suspect share ratio ({_v(ratio)}): multiple share classes?"
        change = (ratio - 1) * 100
        if _le(change, threshold, tol):
            return PASS, ""
        return FAIL, f"shares +{_v(change)}% since the close of Y > {_v(threshold)}%"

    return _apply(df, rule)


def r4_12_yf(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.Series]:
    """Equity > 0 in Y and in Y-2."""
    tol = _tol(cfg)

    def rule(v):
        eq_y, eq_y2 = _number(v.get("yf_equity_y")), _number(v.get("yf_equity_y2"))
        negatives = [f"equity {name} {_v(x)} ≤ 0" for name, x in (("Y", eq_y), ("Y-2", eq_y2))
                     if x is not None and _le(x, 0, tol)]
        if negatives:
            return FAIL, " ; ".join(negatives)
        if eq_y is None or eq_y2 is None:
            return MISSING, _missing(("equity Y", eq_y), ("equity Y-2", eq_y2))
        return PASS, ""

    return _apply(df, rule)


# --- sequence ----------------------------------------------------------------------------

TV_RULES: dict[str, Rule] = {
    "R3.1": r3_1,
    "R3.2": r3_2,
    "R3.3": r3_3,
    "R3.4": r3_4,
    "R3.5": r3_5,
    "R4.1": r4_1,
    "R4.2": r4_2,
    "R4.3": r4_3,
    "R4.4": r4_4,
    "R4.5": r4_5,
    "R4.6": r4_6,
    "R4.8": r4_8,
    "R4.12_tv": r4_12_tv,
}


def filter_tv(df: pd.DataFrame, cfg: dict, rates: dict[str, float]) -> pd.DataFrame:
    """Derived columns, then one status column and one reason column per TradingView rule."""
    df = add_derived_columns(df, rates)
    for rule, function in TV_RULES.items():
        df[rule], df[reason_column(rule)] = function(df, cfg)
    return df


YAHOO_RULES: dict[str, Rule] = {
    "R4.7": r4_7,
    "R4.9": r4_9,
    "R4.10a": r4_10a,
    "R4.10b": r4_10b,
    "R4.12_yf": r4_12_yf,
}


def filter_yahoo(df: pd.DataFrame, cfg: dict, run_date: dt.date) -> pd.DataFrame:
    """Statuses of the Yahoo-stage rules (R3.6 first), from the `yf_` columns.

    To be called only on the stocks whose Yahoo data has been extracted.
    """
    df = df.copy()
    df["R3.6"], df[reason_column("R3.6")] = r3_6(df, cfg, run_date)
    for rule, function in YAHOO_RULES.items():
        df[rule], df[reason_column(rule)] = function(df, cfg)
    return df
