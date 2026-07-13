from __future__ import annotations

import ast
from pathlib import Path


CORE = Path(__file__).parents[1] / "appv231" / "agent"
FORBIDDEN_PREFIXES = ("appv231.coding_agent", "appv231.compaction", "appv231.tui")


def test_agent_core_has_no_domain_imports() -> None:
    violations: list[str] = []
    for path in CORE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.name}:{node.lineno}:{node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_PREFIXES):
                        violations.append(f"{path.name}:{node.lineno}:{alias.name}")
    assert violations == []


def test_agent_core_does_not_own_coding_tool_policy_modules() -> None:
    assert not (CORE / "tool_dispatch.py").exists()
    assert not (CORE / "tool_guardrails.py").exists()
