"""Orchestration of a screener run.

1. Configuration loaded and validated.
2. TradingView (one call per market) -> Yahoo tickers -> exchange rates -> TradingView filters.
3. For each survivor: Yahoo (today's cache), match check, extraction of the fundamentals,
   then Yahoo filters.
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
from investment_agent.engine.data import yahoo
from investment_agent.engine.data.fx import eur_rates
from investment_agent.engine.data.tickers import exchange_of, load_mapping, tv_to_yahoo
from investment_agent.engine.data.tradingview import download_universe
from investment_agent.engine.report import (
    RunResult, render_report, strategy_presentation, write_detail, write_report)
from investment_agent.engine.statuses import (
    ELIGIBLE_COLUMN, FAIL, FIRST_FAILED_COLUMN, MATCH_COLUMN, PASS, eligibility, first_failed_rule,
    reason_column)

log = logging.getLogger(__name__)

CAT_MARKETS = "TradingView markets with errors or truncated"
CAT_RATES = "Missing exchange rates (amounts in these currencies marked missing)"
CAT_MATCH = "Failed Yahoo matches (TV ticker -> Yahoo ticker tried: reason)"
CAT_NO_MAPPING = "Stocks without a Yahoo mapping, by exchange"
CAT_YAHOO_ERRORS = "Incomplete Yahoo downloads"
PROGRESS_INTERVAL = 25


@dataclass
class Options:
    strategy: str = "higgons"
    output: Path | None = None           # report folder (default: data/reports)
    no_cache: bool = False
    markets: list[str] | None = None     # subset of markets (partial run, for testing)
    date: dt.date | None = None          # run date (default: today)
    root: Path | None = None             # data root (default: repository root)
    source_tv: Callable | None = None    # TradingView query factory
    source_fx: Callable | None = None    # exchange-rate downloader
    source_yahoo: Callable | None = None  # Yahoo source (ticker, start_date) -> YahooData


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


def _no_mapping(df: pd.DataFrame, first_step: str, survivors: pd.Index) -> list[str]:
    """Count by exchange of the stocks without a Yahoo ticker: universe (after step 1) and survivors."""
    without = df["yahoo_ticker"].isna()
    universe = Counter(df.loc[without & df[first_step].eq(PASS), "tv_ticker"].map(exchange_of))
    stage = Counter(df.loc[survivors][without.loc[survivors]]["tv_ticker"].map(exchange_of))
    return [f"{exchange}: {universe[exchange]} in the universe after {first_step}, "
            f"{stage[exchange]} among the survivors of the TradingView stage"
            for exchange in sorted(universe, key=lambda b: (-universe[b], str(b)))]


def run(options: Options | None = None) -> RunOutput:
    options = options or Options()
    start, clock = dt.datetime.now(), time.monotonic()
    date = options.date or start.date()
    strategy = importlib.import_module(f"investment_agent.strategies.{options.strategy}")
    cfg = load_config(strategy.CONFIG_PATH, strategy.REQUIRED_KEYS)
    tech = cfg["technical"]
    root = Path(options.root) if options.root else project_root()
    folder = Path(options.output) if options.output else root / "data" / "reports"
    cache = None if options.no_cache else root / "data" / "cache" / "yahoo"

    # 2. TradingView stage
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

    df = strategy.filter_tv(universe, cfg, rates)
    tv_steps = [c for c, _ in strategy.TV_STEPS]
    survivors = df.index[eligibility(df, tv_steps)]
    log.info("TradingView stage: %d survivors out of %d", len(survivors), len(df))

    # 3. Yahoo stage
    df[MATCH_COLUMN] = pd.Series(None, index=df.index, dtype=object)
    df[reason_column(MATCH_COLUMN)] = pd.Series(None, index=df.index, dtype=object)
    warnings[CAT_MATCH] = []
    warnings[CAT_YAHOO_ERRORS] = []
    rows: dict = {}
    start_date = strategy.price_start_date(date, cfg)
    for n, i in enumerate(survivors, 1):
        tv, yt = df.at[i, "tv_ticker"], df.at[i, "yahoo_ticker"]
        status, reason = _yahoo_stage(df, i, tv, yt, date, start_date, cache, cfg, strategy, options,
                                      warnings, rows)
        df.at[i, MATCH_COLUMN] = status
        df.at[i, reason_column(MATCH_COLUMN)] = reason
        if n % PROGRESS_INTERVAL == 0 or n == len(survivors):
            log.info("Yahoo: %d/%d stocks (%.0f s elapsed)", n, len(survivors), time.monotonic() - clock)
    warnings[CAT_NO_MAPPING] = _no_mapping(df, tv_steps[0], survivors)

    if rows:
        extracted = pd.DataFrame(list(rows.values()), index=list(rows))
        yahoo_results = strategy.filter_yahoo(extracted, cfg, date)
        for column in yahoo_results.columns:
            df[column] = yahoo_results[column].reindex(df.index)
    presentation = strategy_presentation(strategy, cfg)
    columns = [c for c, _ in presentation.steps]
    for column in columns:
        if column not in df:
            df[column] = pd.Series(None, index=df.index, dtype=object)
    df[ELIGIBLE_COLUMN] = eligibility(df, columns)
    df[FIRST_FAILED_COLUMN] = first_failed_rule(df, columns)

    # 4. Score
    ranking = strategy.rank(df, cfg)
    log.info("Eligible: %d", len(ranking))

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


def _yahoo_stage(df, i, tv, yt, date, start_date, cache, cfg, strategy, options, warnings, rows):
    """Downloads, checks and extracts one stock; returns (status, reason) of the Yahoo match."""
    tech = cfg["technical"]
    if not isinstance(yt, str) or not yt:
        return FAIL, f"no Yahoo mapping for exchange {exchange_of(tv)}"
    try:
        data = yahoo.download(yt, start_date, cache, today=date, pause_s=tech["yahoo_pause_s"],
                              source=options.source_yahoo)
    except Exception as e:  # one failing stock must not stop the run
        reason = f"Yahoo error ({type(e).__name__}: {e})"
        warnings[CAT_MATCH].append(f"{tv} -> {yt}: {reason}")
        return FAIL, reason
    if data.error:
        warnings[CAT_YAHOO_ERRORS].append(f"{tv} ({yt}): {data.error}")
    reason = yahoo.check_match(
        data, df.at[i, strategy.PRICE_CURRENCY_COLUMN], df.at[i, strategy.PRICE_COLUMN],
        tech["tv_yahoo_price_gap_max_pct"], tech["comparison_tolerance"])
    if reason:
        warnings[CAT_MATCH].append(f"{tv} -> {yt}: {reason}")
        return FAIL, reason
    rows[i] = strategy.extract_yahoo(data, date, cfg)
    return PASS, ""
