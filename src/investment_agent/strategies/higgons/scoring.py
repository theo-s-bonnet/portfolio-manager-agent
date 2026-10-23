"""Composite score.

For each component, percentile rank among the eligible stocks only
(engine.finance.percentile_rank); score = sum of weight x percentile (weights and direction
in config.yaml, score section). Final order: score descending, then TradingView identifier
ascending. No numeric constant other than 0, 1 and 100 (checked by an AST test).
"""

from __future__ import annotations

import pandas as pd

from investment_agent.engine.finance import is_number, percentile_rank
from investment_agent.engine.statuses import ELIGIBLE_COLUMN

ROE_COLUMN = "roe_retained"                # TradingView ROE, or the fallback (filters.retained_roe)
# Measure of each component: internal column of the universe DataFrame.
COMPONENT_COLUMNS = {
    "momentum": "perf_6m",
    "value": "pe_retained",                # retained P/E (R4.1)
    "quality": ROE_COLUMN,
    "growth": "revenue_cagr_5y",
    "balance_sheet": "net_debt_ebitda",   # computed in code (R4.4)
}
PCT_PREFIX = "pct_"


def pct_column(component: str) -> str:
    return f"{PCT_PREFIX}{component}"


def rank(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Eligible stocks with one percentile per component, the score and the rank (1 = best).

    Every measure of an eligible stock must be a number (data guard R4.11): otherwise
    ValueError, the run stops rather than ranking with a missing value.
    """
    weights, direction = cfg["score"]["weights"], cfg["score"]["direction"]
    unknown = sorted(set(weights) - set(COMPONENT_COLUMNS))
    if unknown:
        raise ValueError(f"score components without a defined measure: {', '.join(unknown)}")

    eligible = df[df[ELIGIBLE_COLUMN].eq(True)].copy()
    for component in weights:
        column = COMPONENT_COLUMNS[component]
        invalid = eligible.loc[~eligible[column].map(is_number), "tv_ticker"].tolist()
        if invalid:
            raise ValueError(f"eligible stocks without a {component} measure ({column}): {', '.join(invalid)}")
    score = pd.Series(0.0, index=eligible.index)
    for component, w in weights.items():
        pct = percentile_rank(eligible[COMPONENT_COLUMNS[component]], direction[component])
        eligible[pct_column(component)] = pct
        score = score + w * pct
    eligible["score"] = score
    eligible = eligible.sort_values(["score", "tv_ticker"], ascending=[False, True], kind="mergesort")
    eligible["rank"] = range(1, len(eligible) + 1)
    return eligible
