"""Rule statuses and conventional columns shared by the engine and the strategies.

Every rule produces, for every stock, a status `pass`, `fail` or `missing`, in a column named
after the rule (e.g. "R4.1"), and a text reason in the column `reason_<rule>` (measured value
and threshold, or the missing data; empty or explanatory on `pass`). Every rule is evaluated
for every stock. A stock is eligible if and only if all its rules are `pass`.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

PASS = "pass"
FAIL = "fail"
MISSING = "missing"
STATUSES = (PASS, FAIL, MISSING)

REASON_PREFIX = "reason_"
ELIGIBLE_COLUMN = "eligible"
FIRST_FAILED_COLUMN = "first_failed_rule"
WARNINGS_COLUMN = "warnings"   # list of (category, text) per stock
# Engine data guard: a verified Yahoo ticker exists for the stock's exchange (no download).
MAPPING_COLUMN = "yahoo_mapping"
MAPPING_LABEL = "Yahoo ticker available"


def reason_column(rule: str) -> str:
    return f"{REASON_PREFIX}{rule}"


def eligibility(df: pd.DataFrame, rules: Sequence[str]) -> pd.Series:
    """True if every rule is `pass` (a rule that was not evaluated is not `pass`)."""
    if not len(rules):
        return pd.Series(True, index=df.index, dtype=bool)
    return df[list(rules)].eq(PASS).all(axis=1).astype(bool)


def first_failed_rule(df: pd.DataFrame, rules: Sequence[str]) -> pd.Series:
    """First rule (in the given order) whose status is evaluated and not `pass`."""
    result = pd.Series(None, index=df.index, dtype=object)
    for rule in reversed(list(rules)):
        status = df[rule]
        failed = status.notna() & status.ne(PASS)
        result = result.mask(failed, rule)
    return result
