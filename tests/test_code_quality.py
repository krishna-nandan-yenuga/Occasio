"""Structural rules for the code base that linters do not cover."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).parent.parent / "app"
MAX_FUNCTION_LINES = 40


@pytest.mark.parametrize("path", sorted(APP.rglob("*.py")), ids=lambda p: str(p.relative_to(APP)))
def test_functions_are_short(path: Path) -> None:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            assert length < MAX_FUNCTION_LINES, f"{node.name} is {length} lines"
