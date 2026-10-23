"""Orchestration of a screener run.

1. Configuration loaded and validated.
2. TradingView (one call per market), Yahoo tickers from the mapping table (no download),
   exchange rates (Yahoo), then every strategy rule, evaluated for every stock.
3. Engine data guard `yahoo_mapping`: a verified Yahoo ticker exists for the exchange.
4. Score of the eligible stocks.
5. Markdown report and detail CSV.

The engine knows no rule: it calls the interface of the strategy module.
Sources are injectable (Options.source_*) for tests without network access.
"""

from __future__ import annotations

import datetime as dt
import importlib
import logging
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from investment_agent.engine.config import load_config
from investment_agent.engine.data.fx import eur_rates
from investment_agent.engine.data.tickers import exchange_of, load_mapping, tv_to_yahoo
from investment_agent.engine.data.tradingview import download_universe
from investment_agent.engine.report import (
    RunResult, render_report, strategy_presentation, write_detail, write_report)
from investment_agent.engine.statuses import (
    ELIGIBLE_COLUMN, FAIL, FIRST_FAILED_COLUMN, MAPPING_COLUMN, PASS, eligibility, first_failed_rule,
    reason_column)

log = logging.getLogger(__name__)

CAT_MARKETS = "TradingView markets with errors or truncated"
CAT_RATES = "Missing exchange rates (amounts in these currencies marked missing)"
CAT_NO_MAPPING = "Stocks without a Yahoo mapping, by exchange"


@dataclass
class Options:
    strategy: str = "higgons"
    output: Path | None = None           # report folder (default: data/reports)
    markets: list[str] | None = None     # subset of markets (partial run, for testing)
    date: dt.date | None = None          # run date (default: today)
    root: Path | None = None             # data root (default: repository root)
    source_tv: Callable | None = None    # TradingView query factory
    source_fx: Callable | None = None    # exchange-rate downloader


@dataclass
class RunOutput:
    report: Path
    detail: Path
    n_eligible: int
    result: RunResult = field(repr=False)


def project_root() -> Path:
    """Repository root (editable install), otherwise the current folder."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").exists() else Path.cwd()


def current_commit(root: Path) -> str | None:
    """Short commit id, followed by " (modified)" if tracked files have changed."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
    try:
        commit = git("rev-parse", "--short", "HEAD")
        modified = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{commit} (modified)" if modified else commit


def _tradingview_warnings(universe: pd.DataFrame, limit: int) -> list[str]:
    rows = [f"{m}: error ({e})" for m, e in sorted(universe.attrs.get("market_errors", {}).items())]
    rows += [f"{m}: truncated ({n} rows for a limit of {limit})"
             for m, n in sorted(universe.attrs.get("truncated_markets", {}).items())]
    return rows


def add_mapping_status(df: pd.DataFrame) -> pd.DataFrame:
    """Engine data guard: `pass` if the stock has a Yahoo ticker (column yahoo_ticker), else `fail`.

    Exchanges without a verified Yahoo suffix stay excluded; nothing is downloaded here.
    """
    statuses, reasons = [], []
    for tv, yt in zip(df["tv_ticker"], df["yahoo_ticker"]):
        if isinstance(yt, str) and yt:
            statuses.append(PASS)
            reasons.append("")
        else:
            statuses.append(FAIL)
            reasons.append(f"no verified Yahoo mapping for exchange {exchange_of(tv)}")
    df[MAPPING_COLUMN] = pd.Series(statuses, index=df.index, dtype=object)
    df[reason_column(MAPPING_COLUMN)] = pd.Series(reasons, index=df.index, dtype=object)
    return df


def _no_mapping(df: pd.DataFrame, first_step: str) -> list[str]:
    """Count by exchange of the stocks without a Yahoo ticker that pass the first step."""
    without = df[MAPPING_COLUMN].ne(PASS) & df[first_step].eq(PASS)
    counts = Counter(df.loc[without, "tv_ticker"].map(exchange_of))
    return [f"{exchange}: {counts[exchange]} after {first_step}"
            for exchange in sorted(counts, key=lambda b: (-counts[b], str(b)))]


def run(options: Options | None = None) -> RunOutput:
    options = options or Options()
    start, clock = dt.datetime.now(), time.monotonic()
    date = options.date or start.date()
    strategy = importlib.import_module(f"investment_agent.strategies.{options.strategy}")
    cfg = load_config(strategy.CONFIG_PATH, strategy.REQUIRED_KEYS)
    tech = cfg["technical"]
    root = Path(options.root) if options.root else project_root()
    folder = Path(options.output) if options.output else root / "data" / "reports"

    # 2. TradingView, Yahoo tickers, exchange rates, strategy rules
    markets = list(options.markets or cfg["universe"]["markets"])
    log.info("TradingView: downloading %d markets", len(markets))
    universe = download_universe(markets, strategy.tv_fields(cfg), tech["tradingview_limit"],
                                 query_factory=options.source_tv)
    warnings = {CAT_MARKETS: _tradingview_warnings(universe, tech["tradingview_limit"])}
    log.info("TradingView: %d stocks", len(universe))

    mapping = load_mapping()
    universe["yahoo_ticker"] = pd.Series(
        [tv_to_yahoo(t, m, mapping) for t, m in zip(universe["tv_ticker"], universe["market"])],
        index=universe.index, dtype=object)
    currencies = {c for col in strategy.CURRENCY_COLUMNS for c in universe[col].dropna() if isinstance(c, str)}
    rates = eur_rates(currencies, download=options.source_fx)
    warnings[CAT_RATES] = sorted(c for c in currencies if c not in rates)

    df = strategy.filter_tv(universe, cfg, rates, date)

    # 3. Engine data guard
    df = add_mapping_status(df)
    presentation = strategy_presentation(strategy, cfg)
    columns = [c for c, *_ in presentation.steps]
    for column in columns:
        if column not in df:
            df[column] = pd.Series(None, index=df.index, dtype=object)
    warnings[CAT_NO_MAPPING] = _no_mapping(df, columns[0])
    df[ELIGIBLE_COLUMN] = eligibility(df, columns)
    df[FIRST_FAILED_COLUMN] = first_failed_rule(df, columns)

    # 4. Score
    ranking = strategy.rank(df, cfg)
    log.info("Eligible: %d out of %d", len(ranking), len(df))

    # 5. Report and CSV
    result = RunResult(run_date=date, universe=df, ranking=ranking, presentation=presentation,
                       warnings=warnings, n_markets=len(markets))
    meta = {
        "commit": current_commit(root),
        "start": start,
        "duration_s": time.monotonic() - clock,
        "partial_markets": markets if options.markets else None,
    }
    text = render_report(result, cfg, meta)
    report = write_report(text, folder, date, options.strategy)
    detail = write_detail(df, folder)
    log.info("Report: %s", report)
    return RunOutput(report=report, detail=detail, n_eligible=len(ranking), result=result)
