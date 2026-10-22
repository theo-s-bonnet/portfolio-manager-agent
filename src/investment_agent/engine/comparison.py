"""Comparison with a list exported from TradingView (--compare).

The manual screen is described in `screen_tradingview.yaml` (a list of generic criteria) and
recomputed on the downloaded universe. Every stock found in only one of the two lists
(exported list, stocks eligible for the script) gets an explanation:
- only in the TradingView list: first failed strategy rule and measured value;
- only in the script: manual-screen criterion that would have excluded it, or "none:
  different data" when the recomputed screen keeps it;
- exported stock not reproduced by the recomputed screen: criterion at fault (data fidelity).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import yaml

from investment_agent.engine.config import InvalidConfig
from investment_agent.engine.finance import is_number
from investment_agent.engine.statuses import ELIGIBLE_COLUMN, FIRST_FAILED_COLUMN, reason_column

OPERATORS = {
    ">": lambda a, b, tol: a > b + tol,
    ">=": lambda a, b, tol: a >= b - tol,
    "<": lambda a, b, tol: a < b - tol,
    "<=": lambda a, b, tol: a <= b + tol,
}
# Exchange names displayed in TradingView exports -> API exchange (EXCHANGE:SYMBOL prefix).
DISPLAY_TO_EXCHANGE = {
    "XETRA": "XETR",
    "NYSE ARCA": "AMEX",
    "NYSE AMERICAN": "AMEX",
    "NYSE MKT": "AMEX",
    "CBOE BZX": "CBOE",
    "EURONEXT PARIS": "EURONEXT", "PARIS": "EURONEXT",
    "EURONEXT AMSTERDAM": "EURONEXT", "AMSTERDAM": "EURONEXT",
    "EURONEXT BRUSSELS": "EURONEXT", "BRUSSELS": "EURONEXT",
    "EURONEXT LISBON": "EURONEXT", "LISBON": "EURONEXT",
    "EURONEXT DUBLIN": "EURONEXT", "DUBLIN": "EURONEXT",
    "OSLO": "OSL",
    "STOCKHOLM": "OMXSTO",
    "COPENHAGEN": "OMXCOP",
    "HELSINKI": "OMXHEX",
    "MILAN": "MIL",
    "MADRID": "BME",
    "VIENNA": "VIE",
    "LONDON": "LSE",
    "TORONTO": "TSX",
}
# Accepted header names (the French spellings are accepted as aliases).
SYMBOL_HEADERS = {"ticker", "symbol", "symbole"}
EXCHANGE_HEADERS = {"exchange", "bourse", "place"}
NOT_IN_UNIVERSE = ("not in the downloaded TradingView universe (market outside the universe "
                   "or security not returned by the query)")


# --- screen ---------------------------------------------------------------------------


def load_screen(path: str | Path) -> dict:
    """Reads and checks screen_tradingview.yaml; raises InvalidConfig on error."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            screen = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        raise InvalidConfig(f"unreadable TradingView screen ({path}): {e}") from e
    errors = []
    criteria = screen.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        errors.append("criteria missing or empty")
    for i, c in enumerate(criteria or []):
        if not isinstance(c, dict):
            errors.append(f"criterion {i} malformed")
            continue
        for key in ("label", "column", "operator", "threshold"):
            if key not in c:
                errors.append(f"criterion {i}: key {key} missing")
        if c.get("operator") not in OPERATORS:
            errors.append(f"criterion {i}: unknown operator {c.get('operator')!r}")
        if ("currency" in c) != ("convert_to" in c):
            errors.append(f"criterion {i}: currency and convert_to go together")
    if errors:
        raise InvalidConfig(f"invalid TradingView screen ({path}):\n- " + "\n- ".join(errors))
    return screen


def screen_currencies(screen: dict | None) -> set[str]:
    return {c["convert_to"] for c in (screen or {}).get("criteria", []) if "convert_to" in c}


def _number(x):
    return float(x) if is_number(x) else None


def _number_text(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def evaluate_criterion(v: dict, criterion: dict, rates: dict, tol: float) -> tuple[bool, str]:
    """(criterion met, readable measured value). Missing data: criterion not met."""
    x = _number(v.get(criterion["column"]))
    threshold = float(criterion["threshold"])
    if x is not None and "currency" in criterion:
        currency, target = v.get(criterion["currency"]), criterion["convert_to"]
        if currency in rates and target in rates:
            x = x * rates[currency] / rates[target]
            measured = f"{_number_text(x)} {target}"
        else:
            return False, f"exchange rate missing ({currency} or {target})"
    elif x is not None and "reference" in criterion:
        ref = _number(v.get(criterion["reference"]))
        if ref is None or ref <= 0:
            return False, f"{criterion['reference']} missing"
        x = x / ref * 100
        measured = f"{_number_text(x)}% of {criterion['reference']}"
    else:
        measured = _number_text(x) if x is not None else ""
    if x is None:
        return False, f"{criterion['column']} missing"
    return OPERATORS[criterion["operator"]](x, threshold, tol), measured


def recompute_screen(universe: pd.DataFrame, screen: dict, rates: dict, tol: float) -> pd.DataFrame:
    """One row per stock: `kept` (bool) and `failed_criteria` (text, empty if kept)."""
    kept, failures = [], []
    for v in universe.to_dict("records"):
        failed = []
        for criterion in screen["criteria"]:
            ok, measured = evaluate_criterion(v, criterion, rates, tol)
            if not ok:
                failed.append(f"{criterion['label']} ({measured})" if measured else criterion["label"])
        kept.append(not failed)
        failures.append(" ; ".join(failed))
    return pd.DataFrame({"tv_ticker": universe["tv_ticker"].to_numpy(), "kept": kept,
                         "failed_criteria": failures})


# --- exported list --------------------------------------------------------------------


def read_list(path: str | Path) -> list[tuple[str, str | None]]:
    """[(symbol, displayed exchange or None)]; "," or ";" separator, optional header.

    Accepted formats: "Ticker,Exchange" (AUPH,NASDAQ), "KTN;XETR", or "NASDAQ:AUPH".
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    separator = ";" if lines[0].count(";") > lines[0].count(",") else ","
    rows = [[c.strip() for c in r] for r in csv.reader(lines, delimiter=separator)]
    i_symbol, i_exchange = 0, 1
    header = [c.lower() for c in rows[0]]
    if set(header) & (SYMBOL_HEADERS | EXCHANGE_HEADERS):
        i_symbol = next((i for i, c in enumerate(header) if c in SYMBOL_HEADERS), 0)
        i_exchange = next((i for i, c in enumerate(header) if c in EXCHANGE_HEADERS), None)
        rows = rows[1:]
    result = []
    for r in rows:
        if len(r) <= i_symbol or not r[i_symbol]:
            continue
        cell = r[i_symbol]
        if ":" in cell:
            exchange, symbol = cell.split(":", 1)
            result.append((symbol.strip(), exchange.strip()))
        else:
            exchange = r[i_exchange] if i_exchange is not None and len(r) > i_exchange and r[i_exchange] else None
            result.append((cell, exchange))
    return result


def api_exchange(displayed: str | None) -> str | None:
    if not displayed:
        return None
    key = displayed.strip().upper()
    return DISPLAY_TO_EXCHANGE.get(key, key)


def match_list(entries: list[tuple[str, str | None]], universe: pd.DataFrame) -> list[dict]:
    """Matches each list entry to a TradingView identifier of the universe (or None).

    1. exact EXCHANGE:SYMBOL (displayed exchange translated into the API exchange);
    2. otherwise the same symbol in the same country (TradingView market of the displayed
       exchange), if unique: fixes a mistyped exchange (TSXV:BTO -> TSX:BTO) without matching
       another company in another country (TSX:EVT is not XETR:EVT);
    3. no exchange in the list: symbol unique in the whole universe.
    """
    tickers = universe["tv_ticker"]
    known = set(tickers)
    market_of = dict(zip(tickers, universe["market"]))
    markets_by_exchange: dict[str, set] = {}
    by_symbol: dict[str, list[str]] = {}
    for t in tickers:
        if isinstance(t, str) and ":" in t:
            exchange, symbol = t.split(":", 1)
            markets_by_exchange.setdefault(exchange, set()).add(market_of[t])
            by_symbol.setdefault(symbol, []).append(t)
    result = []
    for symbol, exchange in entries:
        export = f"{exchange};{symbol}" if exchange else symbol
        api = api_exchange(exchange)
        candidate = f"{api}:{symbol}" if api else None
        if candidate in known:
            result.append({"export": export, "tv_ticker": candidate})
            continue
        same = by_symbol.get(symbol, [])
        if api:
            markets = markets_by_exchange.get(api, set())
            same = [t for t in same if market_of[t] in markets]
        result.append({"export": export, "tv_ticker": same[0] if len(same) == 1 else None})
    return result


# --- comparison -----------------------------------------------------------------------


def compare(universe: pd.DataFrame, csv_path: str | Path, screen: dict, rates: dict,
            tolerance: float = 0) -> dict:
    """Lists of the "Comparison" section of the report (see engine/report.py)."""
    entries = match_list(read_list(csv_path), universe)
    universe = universe.drop_duplicates("tv_ticker")
    recomputed = recompute_screen(universe, screen, rates, tolerance).set_index("tv_ticker")
    rows = universe.set_index("tv_ticker")
    eligible = set(universe.loc[universe[ELIGIBLE_COLUMN].eq(True), "tv_ticker"])
    kept = set(recomputed.index[recomputed["kept"]])
    in_list = {e["tv_ticker"] for e in entries if e["tv_ticker"]}

    list_only, not_reproduced = [], []
    for e in entries:
        tv = e["tv_ticker"]
        if tv is None:
            list_only.append({**e, "rule": None, "reason": NOT_IN_UNIVERSE})
            not_reproduced.append({**e, "criterion": NOT_IN_UNIVERSE})
            continue
        if tv not in eligible:
            rule = rows.at[tv, FIRST_FAILED_COLUMN]
            rule = rule if isinstance(rule, str) else None
            reason = rows.at[tv, reason_column(rule)] if rule else ""
            list_only.append({**e, "rule": rule, "reason": reason if isinstance(reason, str) else ""})
        if tv not in kept:
            not_reproduced.append({**e, "criterion": recomputed.at[tv, "failed_criteria"]})

    script_only = []
    for tv in sorted(eligible - in_list):
        criterion = recomputed.at[tv, "failed_criteria"] if tv in recomputed.index else ""
        script_only.append({"tv_ticker": tv,
                            "criterion": criterion or "none: different data (the recomputed screen keeps it)"})

    return {
        "file": Path(csv_path).name,
        "n_screen": len(kept),
        "n_list": len(entries),
        "n_script": len(eligible),
        "n_common": len(eligible & in_list),
        "both_lists": sorted(eligible & in_list),
        "list_only": list_only,
        "script_only": script_only,
        "not_reproduced": not_reproduced,
        "screen_not_in_list": sorted(kept - in_list),
    }
