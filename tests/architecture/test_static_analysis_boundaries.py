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


def test_session_history_owners_do_not_use_cast_escape_hatches() -> None:
    assert _cast_calls("travis/coding_agent/compaction_adapter.py") == []
    assert _cast_calls("travis/coding_agent/session_store.py") == []
