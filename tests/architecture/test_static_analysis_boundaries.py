"""Regression checks for reviewed static-analysis ownership boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _cast_calls(relative_path: str) -> list[int]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "cast"
    ]


def _function_cast_calls(relative_path: str, function_name: str) -> list[int]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    ]
    assert len(functions) == 1
    return [
        node.lineno
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "cast"
    ]


def test_session_history_owners_do_not_use_cast_escape_hatches() -> None:
    assert _cast_calls("travis/coding_agent/compaction_adapter.py") == []
    assert _cast_calls("travis/coding_agent/session_store.py") == []


def test_tui_theme_owner_does_not_use_cast_escape_hatches() -> None:
    assert _cast_calls("travis/tui/interactive_theme_helpers.py") == []


def test_application_session_boundaries_do_not_use_cast_escape_hatches() -> None:
    assert _cast_calls("travis/app.py") == []
    assert _cast_calls("travis/coding_agent/session_contracts.py") == []
    assert _function_cast_calls("travis/coding_agent/agent_session.py", "_session_runtime_port") == []
    assert _function_cast_calls("travis/coding_agent/agent_session_runtime.py", "_validate_runtime_session") == []
