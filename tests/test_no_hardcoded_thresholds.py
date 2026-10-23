"""No threshold written in the strategy code.

Syntax analysis (AST) of filters.py and scoring.py: the only numeric
constants allowed are 0, 1 and 100 (percentages). Every threshold comes from config.yaml.
"""

import ast
from pathlib import Path

import pytest

import investment_agent.strategies.higgons as higgons

FOLDER = Path(higgons.__file__).parent
FILES = ["filters.py", "scoring.py"]
ALLOWED = {0, 1, 100}


def numeric_constants(source: str) -> list[tuple[int, object]]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and not isinstance(node.value, bool) \
                and isinstance(node.value, (int, float, complex)):
            found.append((node.lineno, node.value))
    return sorted(found, key=lambda t: (t[0], str(t[1])))


@pytest.mark.parametrize("file", FILES)
def test_no_hardcoded_threshold(file):
    source = (FOLDER / file).read_text(encoding="utf-8")
    forbidden = [(line, v) for line, v in numeric_constants(source) if v not in ALLOWED]
    assert not forbidden, f"{file}: forbidden numeric constants (line, value): {forbidden}"


def test_the_check_detects_a_threshold():
    assert numeric_constants("x = a <= 12\ny = 0.05\nz = b[-1] * 100\nok = True") == [(1, 12), (2, 0.05), (3, 1), (3, 100)], "order (line, text)"
