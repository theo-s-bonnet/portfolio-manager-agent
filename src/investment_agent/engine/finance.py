"""Deterministic financial functions, free of any strategy rule.

- safe_ratio: ratio whose denominator is checked (a denominator <= 0 gives no ratio);
- adjust_for_splits: makes two share counts separated by a split comparable;
- percentile_rank: 0-100 percentile rank among the values provided.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

DIRECTIONS = ("high", "low")


def is_number(x: Any) -> bool:
    """True for a finite real number (not None, NaN, a boolean or infinite)."""
    if x is None or isinstance(x, bool):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def safe_ratio(num: Any, den: Any) -> float | None:
    """num / den, or None if either is missing or the denominator is <= 0 (never a 0 assumed)."""
    if not is_number(num) or not is_number(den):
        return None
    if float(den) <= 0:
        return None
    return float(num) / float(den)


def _naive_timestamp(d: Any) -> pd.Timestamp:
    ts = pd.Timestamp(d)
    return ts.tz_localize(None) if ts.tzinfo is not None else ts


def split_ratio(splits: pd.Series | None, d_old: Any, d_recent: Any) -> float:
    """Product of the split ratios between d_old (excluded) and d_recent (included); 1 if none."""
    if splits is None or len(splits) == 0:
        return 1.0
    index = pd.DatetimeIndex(splits.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    series = pd.Series(splits.to_numpy(dtype=float), index=index)
    start, end = _naive_timestamp(d_old), _naive_timestamp(d_recent)
    in_window = series[(series.index > start) & (series.index <= end)]
    in_window = in_window[in_window > 0]
    return float(in_window.prod()) if len(in_window) else 1.0


def adjust_for_splits(
    n_old: Any, d_old: Any, n_recent: Any, d_recent: Any, splits: pd.Series | None
) -> tuple[float | None, str | None]:
    """Adjusts n_old for the splits between d_old (excluded) and d_recent (included).

    Rule: r = product of the splits in the period, q = n_recent / n_old.
    If |ln q - ln r| < |ln q|, Yahoo has not adjusted the series: n_old * r.
    Otherwise the series is already adjusted: n_old unchanged.
    Returns (adjusted n_old, warning); the warning is None when there is no split;
    (None, None) if a share count is missing or not strictly positive.
    "adjusted: yes" means the script corrected n_old. A reverse split (r < 1) follows the
    same formula.
    """
    if not (is_number(n_old) and is_number(n_recent)):
        return None, None
    n_old, n_recent = float(n_old), float(n_recent)
    if n_old <= 0 or n_recent <= 0:
        return None, None
    r = split_ratio(splits, d_old, d_recent)
    if r == 1:
        return n_old, None
    ln_q = math.log(n_recent / n_old)
    adjusted = abs(ln_q - math.log(r)) < abs(ln_q)
    warning = f"split detected (ratio {r:g}, adjusted: {'yes' if adjusted else 'no'})"
    return (n_old * r if adjusted else n_old), warning


def percentile_rank(series: pd.Series, direction: str) -> pd.Series:
    """Percentile rank from 0 (worst) to 100 (best) among the non-missing values.

    rank(method="average"), pct = (rank - 1) / (n - 1) * 100; direction "low": 100 - pct;
    a single value: 100. Missing values stay NaN.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown direction: {direction!r} (expected: {', '.join(DIRECTIONS)})")
    series = pd.to_numeric(series, errors="coerce")
    ranks = series.rank(method="average")
    n = int(series.notna().sum())
    if n == 1:
        pct = ranks.where(ranks.isna(), 100.0)
        return pct.astype(float)
    pct = (ranks - 1) / (n - 1) * 100
    return (100 - pct) if direction == "low" else pct
