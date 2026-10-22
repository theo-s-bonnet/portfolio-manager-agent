"""Extraction of a stock's Yahoo data for rule R3.6 (and R4.7, R4.9, R4.10, R4.12).

Pure function: receives an already downloaded YahooData and returns a dictionary of columns
prefixed with `yf_`. No missing data is filled in (None); no threshold is written here
(checked by an AST test): tolerances and numbers of years come from config.yaml.

Definitions:
- Y = most recent column of the annual balance sheet that has at least one value;
- Y-2 = column dated (balance_sheet_history_years - 1) years before Y, within
  `balance_sheet_date_tolerance_days` (never the third column without a date check, so that a
  year missing at Yahoo does not shift Y-2);
- the income statement is matched to the same dates, with the same tolerance.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from investment_agent.engine.data.yahoo import YahooData
from investment_agent.engine.finance import adjust_for_splits, is_number
from investment_agent.engine.statuses import WARNINGS_COLUMN

# Rows of Yahoo's annual financial statements.
LINE_DEBT = "Total Debt"
LINE_CASH = "Cash And Cash Equivalents"          # cash only, short-term investments excluded
LINE_EQUITY = "Common Stock Equity"
LINE_DILUTED_SHARES = "Diluted Average Shares"   # income statement
LINE_SHARES = "Ordinary Shares Number"           # balance sheet

# Warning categories of the report.
CATEGORY_SPLIT = "Splits detected (ratio, series adjusted by the script or not)"
CATEGORY_SHARE_RATIO = "Suspect share ratios (multiple share classes likely)"
CATEGORY_TOTAL_DEBT = "Total Debt missing at Yahoo"


def price_start_date(run_date: dt.date, cfg: dict) -> dt.date:
    """Start of the price history to download: enough to check R3.6 with its tolerance."""
    start = (pd.Timestamp(run_date) - pd.DateOffset(months=cfg["universe"]["price_history_months"])
             - pd.Timedelta(days=cfg["technical"]["history_start_tolerance_days"]))
    return start.date()


def naive_timestamp(d: Any) -> pd.Timestamp | None:
    """Timestamp without time zone, normalized to the day; None if the date is invalid."""
    try:
        ts = pd.Timestamp(d)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def first_valid_price_date(prices: pd.Series | None) -> pd.Timestamp | None:
    """Date of the first non-missing price (without time zone), or None if the history is empty."""
    if prices is None or not len(prices):
        return None
    valid = pd.to_numeric(prices, errors="coerce").dropna()
    if not len(valid):
        return None
    return naive_timestamp(valid.index.min())


def dated_columns(table: pd.DataFrame | None) -> dict[pd.Timestamp, Any]:
    """{date without time zone: original column} of the columns with at least one value, most recent first."""
    if table is None or not isinstance(table, pd.DataFrame) or table.empty:
        return {}
    result: dict[pd.Timestamp, Any] = {}
    for column in table.columns:
        date = naive_timestamp(column)
        if date is None or date in result:
            continue
        if pd.to_numeric(table[column], errors="coerce").notna().any():
            result[date] = column
    return dict(sorted(result.items(), key=lambda item: item[0], reverse=True))


def find_date(
    dates: list[pd.Timestamp], target: pd.Timestamp, tolerance_days: float
) -> pd.Timestamp | None:
    """Date closest to `target`, at most `tolerance_days` days away; None otherwise."""
    candidates = [(abs((d - target).days), d) for d in dates]
    candidates = [(gap, d) for gap, d in candidates if gap <= tolerance_days]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[1]


def value(table: pd.DataFrame | None, line: str, column: Any) -> float | None:
    """table.loc[line, column] if it is a finite number; None if the row or column is missing."""
    if table is None or column is None or line not in table.index or column not in table.columns:
        return None
    x = table.loc[line, column]
    if isinstance(x, pd.Series):  # duplicated row: refuse to choose
        return None
    return float(x) if is_number(x) else None


def dates_y_y2(balance_sheet: pd.DataFrame | None, cfg: dict) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    """(date of Y, date of Y-2, number of annual balance sheets with data)."""
    columns = dated_columns(balance_sheet)
    dates = list(columns)
    if not dates:
        return None, None, 0
    date_y = dates[0]
    years_apart = cfg["universe"]["balance_sheet_history_years"] - 1
    target = date_y - pd.DateOffset(years=years_apart)
    tolerance = cfg["technical"]["balance_sheet_date_tolerance_days"]
    date_y2 = find_date(dates[1:], target, tolerance)
    return date_y, date_y2, len(dates)


def _difference(a: float | None, b: float | None) -> float | None:
    return a - b if a is not None and b is not None else None


def extract_yahoo(data: YahooData, run_date: dt.date, cfg: dict) -> dict:
    """`yf_` columns of a stock, from its Yahoo data, and its warnings.

    - net debt = Total Debt - Cash And Cash Equivalents; Yahoo's Net Debt row is never read
      (it is missing when the company has net cash);
    - diluted share counts of Y-2 adjusted for the splits between Y-2 and Y;
      share count at the close of Y adjusted for the splits between Y and today;
    - `warnings`: list of (category, text) shown in the report.
    """
    tolerance = cfg["technical"]["balance_sheet_date_tolerance_days"]
    balance, income = data.balance_sheet, data.income_statement
    date_y, date_y2, n_balance_sheets = dates_y_y2(balance, cfg)
    balance_columns = dated_columns(balance)
    col_y, col_y2 = balance_columns.get(date_y), balance_columns.get(date_y2)

    income_columns = dated_columns(income)
    income_dates = list(income_columns)
    col_iy = col_iy2 = None
    if date_y is not None:
        col_iy = income_columns.get(find_date(income_dates, date_y, tolerance))
    if date_y2 is not None:
        col_iy2 = income_columns.get(find_date(income_dates, date_y2, tolerance))

    debt_y, debt_y2 = value(balance, LINE_DEBT, col_y), value(balance, LINE_DEBT, col_y2)
    cash_y, cash_y2 = value(balance, LINE_CASH, col_y), value(balance, LINE_CASH, col_y2)
    equity_y, equity_y2 = value(balance, LINE_EQUITY, col_y), value(balance, LINE_EQUITY, col_y2)
    n_y = value(income, LINE_DILUTED_SHARES, col_iy)
    n_y2_raw = value(income, LINE_DILUTED_SHARES, col_iy2)
    shares_y_raw = value(balance, LINE_SHARES, col_y)
    current = data.shares_outstanding if is_number(data.shares_outstanding) else None

    warnings: list[tuple[str, str]] = []
    if date_y2 is not None and (debt_y is None or debt_y2 is None):
        years = ", ".join(name for name, x in (("Y", debt_y), ("Y-2", debt_y2)) if x is None)
        warnings.append((CATEGORY_TOTAL_DEBT, f"{LINE_DEBT} missing ({years})"))

    n_y2, alert = (None, None)
    if date_y2 is not None:
        n_y2, alert = adjust_for_splits(n_y2_raw, date_y2, n_y, date_y, data.splits)
        if alert:
            warnings.append((CATEGORY_SPLIT, f"{alert}, between Y-2 and Y"))

    shares_y, alert = (None, None)
    if date_y is not None:
        today = pd.Timestamp(run_date)
        shares_y, alert = adjust_for_splits(shares_y_raw, date_y, current, today, data.splits)
        if alert:
            warnings.append((CATEGORY_SPLIT, f"{alert}, between Y and today"))

    share_ratio = current / shares_y if current is not None and shares_y else None
    t = cfg["technical"]
    if share_ratio is not None and not (
        t["suspect_share_ratio_min"] <= share_ratio <= t["suspect_share_ratio_max"]
    ):
        warnings.append((CATEGORY_SHARE_RATIO,
                         f"current shares / shares at the close of Y = {share_ratio:.2f}"))

    return {
        "yf_first_price_date": first_valid_price_date(data.prices),
        "yf_balance_sheet_count": n_balance_sheets,
        "yf_date_y": date_y,
        "yf_date_y2": date_y2,
        "yf_debt_y": debt_y,
        "yf_debt_y2": debt_y2,
        "yf_cash_y": cash_y,
        "yf_cash_y2": cash_y2,
        "yf_net_debt_y": _difference(debt_y, cash_y),
        "yf_net_debt_y2": _difference(debt_y2, cash_y2),
        "yf_equity_y": equity_y,
        "yf_equity_y2": equity_y2,
        "yf_diluted_shares_y": n_y,
        "yf_diluted_shares_y2_raw": n_y2_raw,
        "yf_diluted_shares_y2": n_y2,
        "yf_shares_y_raw": shares_y_raw,
        "yf_shares_y": shares_y,
        "yf_shares_current": current,
        "yf_share_ratio": share_ratio,
        WARNINGS_COLUMN: warnings,
    }
