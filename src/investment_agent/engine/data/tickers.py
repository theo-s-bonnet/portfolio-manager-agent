"""Mapping of TradingView identifiers (EXCHANGE:SYMBOL) to Yahoo tickers."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

MAPPING_PATH = Path(__file__).with_name("yahoo_mapping.yaml")
_SEPARATORS = re.compile(r"[._/]")


def load_mapping(path: str | Path = MAPPING_PATH) -> dict:
    """Reads the table of exceptions and suffixes; missing sections = empty tables."""
    with Path(path).open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    mapping = {
        "exceptions": raw.get("exceptions") or {},
        "suffixes": raw.get("suffixes") or {},
        "suffixes_by_market": raw.get("suffixes_by_market") or {},
    }
    for section, table in mapping.items():
        if not isinstance(table, dict):
            raise ValueError(f"{path}: section {section} must be a mapping")
    return mapping


def split_ticker(tv_ticker: str) -> tuple[str, str] | None:
    """'EXCHANGE:SYMBOL' -> ('EXCHANGE', 'SYMBOL'); None if the format is invalid."""
    if not isinstance(tv_ticker, str) or tv_ticker.count(":") != 1:
        return None
    exchange, symbol = (p.strip() for p in tv_ticker.split(":"))
    if not exchange or not symbol:
        return None
    return exchange, symbol


def exchange_of(tv_ticker: str) -> str | None:
    parts = split_ticker(tv_ticker)
    return parts[0] if parts else None


def yahoo_symbol(symbol: str) -> str:
    """Replaces '.', '_' and '/' with '-' and removes a trailing '-' (BRK.B -> BRK-B)."""
    return _SEPARATORS.sub("-", symbol).rstrip("-")


def tv_to_yahoo(tv_ticker: str, market: str, mapping: dict) -> str | None:
    """Matching Yahoo ticker, or None if the exchange has no known suffix."""
    parts = split_ticker(tv_ticker)
    if parts is None:
        return None
    exception = mapping.get("exceptions", {}).get(tv_ticker)
    if exception:
        return str(exception)
    exchange, symbol = parts
    by_market = mapping.get("suffixes_by_market", {})
    if exchange in by_market:
        suffix = by_market[exchange].get(market)
    else:
        suffix = mapping.get("suffixes", {}).get(exchange)
    if suffix is None:
        return None
    return f"{yahoo_symbol(symbol)}{suffix}"
