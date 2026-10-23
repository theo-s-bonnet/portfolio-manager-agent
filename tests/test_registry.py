"""Consistency between the funnel of the strategy and the rule register of config.yaml."""

from helpers import config
from investment_agent.engine.statuses import MAPPING_COLUMN
from investment_agent.strategies.higgons import FUNNEL

KIND_OF_GROUP = {"Universe": "universe", "Selection criteria": "criterion", "Data guards": "data_guard"}
RULES = config()["rules"]


def test_every_funnel_rule_is_registered_with_its_kind():
    for group, steps in FUNNEL:
        for column, _ in steps:
            if column == MAPPING_COLUMN:        # engine guard, not a strategy rule
                continue
            assert RULES[column]["status"] == "implemented", column
            assert RULES[column]["kind"] == KIND_OF_GROUP[group], column


def test_every_implemented_rule_is_in_the_funnel():
    columns = {column for _, steps in FUNNEL for column, _ in steps}
    implemented = {rule for rule, desc in RULES.items() if desc["status"] == "implemented"}
    assert implemented <= columns, implemented - columns
