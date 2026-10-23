"""Command: python -m investment_agent run [--strategy higgons] [--output DIR] [--no-cache]
[--markets america,uk]."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from investment_agent.engine.config import InvalidConfig
from investment_agent.engine.runner import Options, run


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m investment_agent",
                                     description="Investment agent: weekly stock screener.")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="runs the screener and writes the report")
    run_cmd.add_argument("--strategy", default="higgons", help="strategy folder (default: higgons)")
    run_cmd.add_argument("--output", type=Path, metavar="DIR",
                         help="folder of the report and the CSV (default: data/reports)")
    run_cmd.add_argument("--no-cache", action="store_true", help="ignores today's Yahoo cache")
    run_cmd.add_argument("--markets", metavar="LIST",
                         help="comma-separated TradingView markets (partial run, for testing)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)
    options = Options(
        strategy=args.strategy,
        output=args.output,
        no_cache=args.no_cache,
        markets=[m.strip() for m in args.markets.split(",") if m.strip()] if args.markets else None,
    )
    try:
        output = run(options)
    except (InvalidConfig, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"{output.n_eligible} eligible stocks")
    print(f"Report: {output.report}")
    print(f"Detail: {output.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
