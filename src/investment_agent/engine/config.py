"""Loading and validation of a strategy configuration.

The engine knows no strategy rule: the list of required keys is supplied by the strategy
(`required_keys`). The engine only checks the shape: sections present, numbers positive or
zero, score weights that sum to 1, no `TO_CONFIRM` value left, well-formed rule register
(status implemented with a kind, not_implemented with a planned version, removed with a reason).
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

UNCONFIRMED_VALUE = "TO_CONFIRM"

# Sections common to every strategy (engine contract, not rules).
ENGINE_SECTIONS = (
    "strategy",
    "strategy_version",
    "config_version",
    "score",
    "tradingview_fields",
    "technical",
    "rules",
)
# Sections whose numeric values must all be positive or zero.
NUMERIC_SECTIONS = ("universe", "filters", "technical")
ALLOWED_DIRECTIONS = ("high", "low")
RULE_STATUSES = ("implemented", "not_implemented", "removed")
RULE_KINDS = ("universe", "criterion", "data_guard")


class InvalidConfig(Exception):
    """Missing, incomplete or inconsistent configuration: the run stops."""


def load_config(
    path: str | Path, required_keys: Mapping[str, list[str]] | None = None
) -> dict:
    """Reads a YAML configuration file and validates it.

    `required_keys`: {section: [keys]} supplied by the strategy (e.g. REQUIRED_KEYS of higgons).
    """
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise InvalidConfig(f"configuration file not found: {path}") from e
    except yaml.YAMLError as e:
        raise InvalidConfig(f"invalid YAML in {path}: {e}") from e
    if not isinstance(cfg, dict):
        raise InvalidConfig(f"{path}: the content must be a YAML mapping")
    validate_config(cfg, required_keys)
    return cfg


def validate_config(cfg: dict, required_keys: Mapping[str, list[str]] | None = None) -> None:
    """Raises InvalidConfig with the list of every error found."""
    errors: list[str] = []

    for section in ENGINE_SECTIONS:
        if section not in cfg:
            errors.append(f"missing section: {section}")

    for section, keys in (required_keys or {}).items():
        content = cfg.get(section)
        if not isinstance(content, dict):
            errors.append(f"missing or malformed section: {section}")
            continue
        for key in keys:
            if key not in content:
                errors.append(f"missing key: {section}.{key}")

    for path, value in _walk(cfg):
        if isinstance(value, str) and value.strip() == UNCONFIRMED_VALUE:
            errors.append(f"unconfirmed value ({UNCONFIRMED_VALUE}): {path}")

    for section in NUMERIC_SECTIONS:
        for path, value in _walk(cfg.get(section, {}), prefix=section):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if math.isnan(value) or value < 0:
                errors.append(f"negative or invalid number: {path} = {value}")

    errors.extend(_score_errors(cfg))
    errors.extend(_field_errors(cfg))
    errors.extend(_rule_errors(cfg))

    if errors:
        raise InvalidConfig("invalid configuration:\n- " + "\n- ".join(errors))


def _score_errors(cfg: dict) -> list[str]:
    score = cfg.get("score")
    if not isinstance(score, dict):
        return []
    weights, direction = score.get("weights"), score.get("direction")
    if not isinstance(weights, dict) or not weights:
        return ["score.weights missing or empty"]
    if not isinstance(direction, dict):
        return ["score.direction missing"]
    errors = []
    for component, w in weights.items():
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0:
            errors.append(f"score.weights.{component} must be a positive number or zero")
        if component not in direction:
            errors.append(f"score.direction.{component} missing")
        elif direction[component] not in ALLOWED_DIRECTIONS:
            errors.append(f"score.direction.{component} must be {' or '.join(ALLOWED_DIRECTIONS)}")
    if errors:
        return errors
    tolerance = cfg.get("technical", {}).get("comparison_tolerance")
    if not isinstance(tolerance, (int, float)):
        return ["technical.comparison_tolerance missing: cannot check the weights"]
    total = math.fsum(weights.values())
    if abs(total - 1) > tolerance:
        errors.append(f"the score weights sum to {total}, not 1")
    return errors


def _field_errors(cfg: dict) -> list[str]:
    fields = cfg.get("tradingview_fields")
    if fields is None:
        return []
    if not isinstance(fields, dict):
        return ["tradingview_fields must be a mapping {internal name: TradingView field}"]
    return [
        f"tradingview_fields.{name} must be a non-empty field name"
        for name, field in fields.items()
        if not isinstance(field, str) or not field.strip()
    ]


def _rule_errors(cfg: dict) -> list[str]:
    rules = cfg.get("rules")
    if rules is None:
        return []
    if not isinstance(rules, dict):
        return ["rules must be a mapping {rule: {status: ...}}"]
    errors = []
    for rule, desc in rules.items():
        if not isinstance(desc, dict) or desc.get("status") not in RULE_STATUSES:
            errors.append(f"rules.{rule}.status must be {', '.join(RULE_STATUSES)}")
        elif desc["status"] == "implemented" and desc.get("kind") not in RULE_KINDS:
            errors.append(f"rules.{rule}.kind must be {', '.join(RULE_KINDS)} for an implemented rule")
        elif desc["status"] == "not_implemented" and not desc.get("version"):
            errors.append(f"rules.{rule}: planned version missing for a rule not implemented")
        elif desc["status"] == "removed" and not desc.get("reason"):
            errors.append(f"rules.{rule}: reason missing for a removed rule")
    return errors


def _walk(obj: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Walks dictionaries and lists recursively; yields (path, leaf value)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _walk(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _walk(value, f"{prefix}[{i}]")
    else:
        yield prefix, obj
