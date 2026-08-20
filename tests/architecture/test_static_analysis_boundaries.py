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


def _class_attribute_annotation(
    relative_path: str,
    class_name: str,
    attribute_name: str,
) -> str:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    assert len(classes) == 1
    annotations = [
        node.annotation
        for node in classes[0].body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == attribute_name
    ]
    assert len(annotations) == 1
    return ast.unparse(annotations[0])


def test_session_history_owners_do_not_use_cast_escape_hatches() -> None:
    assert _cast_calls("travis/coding_agent/compaction_adapter.py") == []
    assert _cast_calls("travis/coding_agent/session_store.py") == []


def test_tui_theme_owner_does_not_use_cast_escape_hatches() -> None:
    assert _cast_calls("travis/tui/interactive_theme_helpers.py") == []


def test_tui_theme_owner_uses_direct_structural_session_ports() -> None:
    relative_path = "travis/tui/interactive_theme_helpers.py"
    assert _class_attribute_annotation(
        relative_path,
        "InteractiveThemeAppPort",
        "session",
    ) == "InteractiveThemeSessionPort"
    assert _class_attribute_annotation(
        relative_path,
        "InteractiveThemeSessionPort",
        "resource_loader",
    ) == "InteractiveThemeResourceLoaderPort"
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    assert "getattr(session, \"resource_loader\"" not in source
    assert "view.app.session.resource_loader" in source


def test_application_session_boundaries_do_not_use_cast_escape_hatches() -> None:
    assert _cast_calls("travis/app.py") == []
    assert _cast_calls("travis/coding_agent/session_contracts.py") == []
    assert _function_cast_calls("travis/coding_agent/agent_session.py", "_session_runtime_port") == []
    assert _function_cast_calls("travis/coding_agent/agent_session_runtime.py", "_validate_runtime_session") == []
