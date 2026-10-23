"""Loading and validation of the configuration."""

import copy

import pytest
import yaml

from investment_agent.engine.config import InvalidConfig, load_config, validate_config

TEST_KEYS = {"filters": ["pe_max", "perf_6m_min_pct"], "universe": ["market_cap_min_eur"]}

MINIMAL_CONFIG = {
    "strategy": "test",
    "strategy_version": "1.0",
    "config_version": "1.0.0",
    "universe": {"market_cap_min_eur": 150, "markets": ["france"]},
    "filters": {"pe_max": 12, "perf_6m_min_pct": 0},
    "score": {
        "weights": {"momentum": 0.6, "value": 0.4},
        "direction": {"momentum": "high", "value": "low"},
    },
    "tradingview_fields": {"pe_ttm": "price_earnings_ttm"},
    "technical": {"comparison_tolerance": 1.0e-9},
    "rules": {"R4.1": {"status": "implemented", "kind": "criterion"},
              "R4.2": {"status": "removed", "reason": "no longer a filter"},
              "R6": {"status": "not_implemented", "version": "V5"}},
}


def write(tmp_path, cfg):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


def test_minimal_config_is_valid(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL_CONFIG), TEST_KEYS)
    assert cfg["filters"]["pe_max"] == 12


def test_weights_summing_to_0_95(tmp_path):
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    cfg["score"]["weights"] = {"momentum": 0.55, "value": 0.40}
    with pytest.raises(InvalidConfig, match="weights"):
        load_config(write(tmp_path, cfg), TEST_KEYS)


def test_missing_key_pe_max(tmp_path):
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    del cfg["filters"]["pe_max"]
    with pytest.raises(InvalidConfig, match="filters.pe_max"):
        load_config(write(tmp_path, cfg), TEST_KEYS)


def test_field_left_to_confirm(tmp_path):
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    cfg["tradingview_fields"]["revenue_cagr_5y"] = "TO_CONFIRM"
    with pytest.raises(InvalidConfig, match="TO_CONFIRM"):
        load_config(write(tmp_path, cfg), TEST_KEYS)


def test_component_without_direction():
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    del cfg["score"]["direction"]["value"]
    with pytest.raises(InvalidConfig, match="score.direction.value"):
        validate_config(cfg, TEST_KEYS)


def test_negative_number():
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    cfg["filters"]["pe_max"] = -1
    with pytest.raises(InvalidConfig, match="negative"):
        validate_config(cfg, TEST_KEYS)


def test_rule_not_implemented_without_version():
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    cfg["rules"]["R6"] = {"status": "not_implemented"}
    with pytest.raises(InvalidConfig, match="R6"):
        validate_config(cfg, TEST_KEYS)


def test_removed_rule_without_reason():
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    cfg["rules"]["R4.2"] = {"status": "removed"}
    with pytest.raises(InvalidConfig, match="R4.2: reason missing"):
        validate_config(cfg, TEST_KEYS)


def test_implemented_rule_without_a_valid_kind():
    for desc in ({"status": "implemented"}, {"status": "implemented", "kind": "filter"}):
        cfg = copy.deepcopy(MINIMAL_CONFIG)
        cfg["rules"]["R4.1"] = desc
        with pytest.raises(InvalidConfig, match="R4.1.kind"):
            validate_config(cfg, TEST_KEYS)


def test_typespec_exempt_markets_required():
    from investment_agent.strategies.higgons import CONFIG_PATH, REQUIRED_KEYS

    cfg = load_config(CONFIG_PATH, REQUIRED_KEYS)
    del cfg["universe"]["typespec_exempt_markets"]
    with pytest.raises(InvalidConfig, match="universe.typespec_exempt_markets"):
        validate_config(cfg, REQUIRED_KEYS)


def test_missing_file(tmp_path):
    with pytest.raises(InvalidConfig, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_real_config_loads():
    from investment_agent.strategies.higgons import CONFIG_PATH, REQUIRED_KEYS

    cfg = load_config(CONFIG_PATH, REQUIRED_KEYS)
    assert cfg["strategy"] == "higgons"
    assert set(cfg["score"]["weights"]) == set(cfg["score"]["direction"])
