"""Markdown report of a screener run, and detail CSV.

The engine knows no rule: the strategy describes the funnel (groups of steps), the score
components (with an optional marker for a fallback value), the descriptive columns and the
warning categories in a `Presentation`. The report does no financial calculation: it counts
and formats.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from investment_agent.engine.finance import is_number
from investment_agent.engine.statuses import (
    ELIGIBLE_COLUMN, FIRST_FAILED_COLUMN, MISSING, PASS, WARNINGS_COLUMN, reason_column)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MINUS = "\u2212"
DETAIL_NAME = "latest_run_detail.csv"
FORMATS = ("signed_pct", "pct", "number")


@dataclass
class Component:
    label: str                       # ranking column header (e.g. "Perf 6M")
    column: str                      # raw value
    pct_column: str                  # percentile rank
    format: str                      # signed_pct, pct or number
    # Optional marker shown after the value when marker_column == marker_value (fallback value),
    # explained by marker_note under the ranking; marker_count: sentence under the funnel with
    # {n}, the number of stocks of the first funnel group that carry the marker.
    marker_column: str | None = None
    marker_value: str | None = None
    marker: str | None = None
    marker_note: str | None = None
    marker_count: str | None = None


@dataclass
class Presentation:
    title: str                                   # e.g. "Higgons"
    steps: list[tuple[str, str, str]]            # (status column, label, group), funnel order
    components: list[Component]
    description_columns: list[tuple[str, str]]   # (label, column): name, country, sector
    country_column: str
    warning_categories: list[str] = field(default_factory=list)
    funnel_note: str = ""                        # sentence under the funnel


@dataclass
class RunResult:
    run_date: dt.date
    universe: pd.DataFrame         # one row per stock, statuses and reasons of every step
    ranking: pd.DataFrame          # eligible stocks, ranked (score, rank, pct columns)
    presentation: Presentation
    warnings: dict[str, list[str]] = field(default_factory=dict)   # engine categories
    n_markets: int = 0


def strategy_presentation(strategy, cfg: dict) -> Presentation:
    """Presentation of a strategy (module); FUNNEL = [(group label, [(column, label)])]."""
    steps = [(column, label, group) for group, group_steps in strategy.FUNNEL
             for column, label in group_steps]
    return Presentation(
        title=strategy.TITLE,
        steps=steps,
        components=[Component(**c) for c in strategy.components(cfg)],
        description_columns=list(strategy.DESCRIPTION_COLUMNS),
        country_column=strategy.COUNTRY_COLUMN,
        warning_categories=list(strategy.WARNING_CATEGORIES),
        funnel_note=getattr(strategy, "FUNNEL_NOTE", ""),
    )


# --- formatting ------------------------------------------------------------------------


def integer(n) -> str:
    return f"{int(n):,}".replace("-", MINUS)


def number(x, decimals: int = 1, signed: bool = False) -> str:
    """Number with thousands separators, decimal point and typographic minus sign."""
    if not is_number(x):
        return "—"
    x = float(x)
    if round(x, decimals) == 0:
        x = 0.0   # no "-0.0"
    text = f"{x:{'+' if signed else ''},.{decimals}f}"
    return text.replace("-", MINUS)


def formatted_value(x, format_: str) -> str:
    if format_ == "signed_pct":
        return f"{number(x, signed=True)}%" if is_number(x) else "—"
    if format_ == "pct":
        return f"{number(x)}%" if is_number(x) else "—"
    return number(x)


def cell(x) -> str:
    if x is None or (not isinstance(x, (list, tuple, dict)) and pd.isna(x)):
        return ""
    return str(x).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: Iterable[list]) -> list[str]:
    output = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    output += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return output


def duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60} min {s % 60:02d} s"


def displayed_stock(row) -> str:
    yahoo = row.get("yahoo_ticker")
    return f"{row['tv_ticker']} ({yahoo})" if isinstance(yahoo, str) and yahoo else str(row["tv_ticker"])


# --- sections --------------------------------------------------------------------------


def funnel(universe: pd.DataFrame, steps: list[tuple[str, str, str]]) -> list[tuple[str, str, int, int, int]]:
    """(group, label, remaining, excluded, of which missing) for each step, in order."""
    remaining = pd.Series(True, index=universe.index)
    rows = []
    for column, label, group in steps:
        status = universe[column]
        passed = remaining & status.eq(PASS)
        excluded = remaining & ~passed
        rows.append((group, label, int(passed.sum()), int(excluded.sum()),
                     int((excluded & status.eq(MISSING)).sum())))
        remaining = passed
    return rows


def first_group_passed(universe: pd.DataFrame, steps: list[tuple[str, str, str]]) -> pd.Series:
    """True for the stocks that pass every step of the first funnel group (the universe)."""
    if not steps:
        return pd.Series(True, index=universe.index)
    first = steps[0][2]
    columns = [c for c, _, g in steps if g == first]
    return universe[columns].eq(PASS).all(axis=1)


def excluded_for_missing_data(universe: pd.DataFrame, steps: list[tuple[str, str, str]]) -> list[list[str]]:
    """Non-eligible stocks whose steps are all `pass` or `missing` (one row per stock)."""
    columns = [c for c, *_ in steps]
    statuses = universe[columns]
    only_pass_or_missing = statuses.isin([PASS, MISSING]).all(axis=1)
    at_least_one_missing = statuses.eq(MISSING).any(axis=1)
    selection = universe[only_pass_or_missing & at_least_one_missing & ~universe[ELIGIBLE_COLUMN].eq(True)]
    rows = []
    for _, v in selection.sort_values("tv_ticker").iterrows():
        missing = [c for c in columns if v[c] == MISSING]
        reasons = list(dict.fromkeys(v.get(reason_column(c)) or "" for c in missing))
        rows.append([displayed_stock(v), ", ".join(missing), " ; ".join(r for r in reasons if r)])
    return rows


def component_cell(v, c: Component) -> str:
    """Raw value, marker if the value is a fallback, then the percentile rank."""
    marked = c.marker and c.marker_column and v.get(c.marker_column) == c.marker_value
    return f"{formatted_value(v[c.column], c.format)}{c.marker if marked else ''} ({number(v[c.pct_column], 0)})"


def markers_shown(ranking: pd.DataFrame, components: list[Component]) -> list[Component]:
    return [c for c in components if c.marker and c.marker_column in ranking
            and ranking[c.marker_column].eq(c.marker_value).any()]


def strategy_warnings(universe: pd.DataFrame) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if WARNINGS_COLUMN not in universe:
        return result
    for _, v in universe.sort_values("tv_ticker").iterrows():
        items = v[WARNINGS_COLUMN]
        if not isinstance(items, (list, tuple)):
            continue
        for category, text in items:
            result.setdefault(category, []).append(f"{v['tv_ticker']}: {text}")
    return result


def warnings_section(res: RunResult) -> list[str]:
    output = ["## Warnings", ""]
    by_strategy = strategy_warnings(res.universe)
    categories = list(res.warnings.items())
    order = res.presentation.warning_categories
    categories += [(c, by_strategy.get(c, [])) for c in order]
    categories += [(c, by_strategy[c]) for c in sorted(by_strategy) if c not in order]
    for category, rows in categories:
        output.append(f"### {category} ({integer(len(rows))})")
        output += [f"- {cell(r)}" for r in rows] if rows else ["None."]
        output.append("")
    country = res.presentation.country_column
    output.append(f"### Eligible by country ({integer(len(res.ranking))})")
    if len(res.ranking):
        counts = res.ranking[country].fillna("unknown country").value_counts()
        counts = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
        output += [f"- {cell(c)}: {integer(n)}" for c, n in counts]
    else:
        output.append("No eligible stock.")
    output.append("")
    return output


def rules_not_applied_section(cfg: dict) -> list[str]:
    rows = []
    for rule, desc in cfg.get("rules", {}).items():
        if desc.get("status") == "not_implemented":
            rows.append([rule, desc.get("version", ""), desc.get("reason", "—")])
        elif desc.get("status") == "implemented" and desc.get("note"):
            rows.append([rule, "implemented, with a limitation", desc["note"]])
    return ["## Rules not applied or applied with a limitation", "",
            *table(["Rule", "Planned version", "Reason"], rows), ""]


def render_report(res: RunResult, cfg: dict, meta: dict) -> str:
    """Markdown text of the report. `meta`: commit, start (datetime), duration_s, partial_markets."""
    p, d = res.presentation, res.run_date
    start: dt.datetime = meta["start"]
    output = [
        f"# {p.title} screener: {WEEKDAYS[d.weekday()]} {d:%Y-%m-%d}",
        f"Strategy {cfg['strategy_version']} · config {cfg['config_version']} · commit {meta.get('commit') or 'unknown'}"
        f" · run on {start:%Y-%m-%d} at {start:%H:%M} · duration {duration(meta.get('duration_s', 0))}",
        "Sources: TradingView (delayed data). "
        f"Exchange rates: last Yahoo Finance close available on {d:%Y-%m-%d}.",
        "",
    ]
    if meta.get("partial_markets"):
        output += [f"> **Partial run**: markets {', '.join(meta['partial_markets'])} only.", ""]

    output += ["## Summary", ""]
    rows = [[f"TradingView universe ({integer(res.n_markets)} markets)", integer(len(res.universe)), "—", "—"]]
    group = None
    for step_group, label, remaining, excluded, missing in funnel(res.universe, p.steps):
        if step_group and step_group != group:
            rows.append([f"**{step_group}**", "", "", ""])
        group = step_group
        rows.append([label, integer(remaining), integer(excluded), integer(missing)])
    rows.append(["**Eligible**", f"**{integer(len(res.ranking))}**", "", ""])
    output += table(["Step", "Remaining", "Excluded at this step", "of which missing data"], rows)
    output += ["", f"Each stock is counted at the first step it fails, in this order. {p.funnel_note}".strip(), ""]
    in_universe = first_group_passed(res.universe, p.steps)
    for c in p.components:
        if c.marker_count and c.marker_column in res.universe:
            n = int((in_universe & res.universe[c.marker_column].eq(c.marker_value)).sum())
            output += [c.marker_count.format(n=integer(n)), ""]

    output += ["## Ranking", ""]
    if len(res.ranking):
        headers = ["#", "Stock", *[l for l, _ in p.description_columns], "Score", *[c.label for c in p.components]]
        rows = []
        for _, v in res.ranking.iterrows():
            rows.append([
                integer(v["rank"]), displayed_stock(v), *[v.get(c) for _, c in p.description_columns],
                number(v["score"]),
                *[component_cell(v, c) for c in p.components],
            ])
        output += table(headers, rows)
        output += ["", "Raw value, then percentile rank among eligible stocks in parentheses.", ""]
        for c in markers_shown(res.ranking, p.components):
            output += [c.marker_note, ""]
    else:
        output += ["No eligible stock.", ""]

    output += ["## Excluded for missing or doubtful data", "",
               "Stocks that pass every step except one or more steps in `missing`.", ""]
    rows = excluded_for_missing_data(res.universe, p.steps)
    output += [f"Stocks concerned: {integer(len(rows))}.", ""] if rows else []
    output += table(["Stock", "Rules in `missing`", "Missing or doubtful data"], rows) \
        if rows else ["None."]
    output.append("")

    output += warnings_section(res)
    output += rules_not_applied_section(cfg)
    return "\n".join(output).rstrip() + "\n"


# --- writing ---------------------------------------------------------------------------


def write_report(text: str, folder: Path, date: dt.date, strategy: str) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date:%Y-%m-%d}_{strategy}.md"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _csv_text(x):
    if isinstance(x, (list, tuple)):
        return " | ".join(": ".join(map(str, e)) if isinstance(e, (list, tuple)) else str(e) for e in x)
    return x


def write_detail(universe: pd.DataFrame, folder: Path) -> Path:
    """Detail CSV, one row per stock of the universe; overwritten on every run.

    Standard CSV: comma separator, decimal point, UTF-8.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    first = [c for c in ("tv_ticker", "yahoo_ticker", ELIGIBLE_COLUMN, FIRST_FAILED_COLUMN) if c in universe]
    df = universe[first + [c for c in universe.columns if c not in first]].copy()
    for column in df.columns:
        if df[column].dtype == object:
            df[column] = df[column].map(_csv_text)
    path = folder / DETAIL_NAME
    df.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path
