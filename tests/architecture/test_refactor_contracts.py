from __future__ import annotations

import ast
import inspect
from pathlib import Path

from travis.coding_agent.agent_session import AgentSession, _SessionRuntime
from travis.coding_agent.session_contracts import AGENT_SESSION_PUBLIC_MEMBERS
from travis.tui.interactive_contracts import INTERACTIVE_MODE_PUBLIC_MEMBERS
from travis.tui.interactive_mode import InteractiveMode, _InteractiveRuntime


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_MODULES = (
    ROOT / "travis/coding_agent/session_contracts.py",
    ROOT / "travis/tui/interactive_contracts.py",
)
FORBIDDEN_CONTRACT_IMPORTS = {
    "travis.app",
    "travis.coding_agent.agent_session",
    "travis.tui.interactive_mode",
}
SESSION_FACTORY_MODULES = (
    ROOT / "travis/coding_agent/agent_session_services.py",
    ROOT / "travis/coding_agent/agent_session_runtime.py",
    ROOT / "travis/coding_agent/session_contracts.py",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _public_descriptors(owner: type[object]) -> set[str]:
    return {
        name
        for name, value in inspect.getmembers_static(owner)
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }


def test_contract_inventories_are_immutable_and_public_only() -> None:
    assert isinstance(AGENT_SESSION_PUBLIC_MEMBERS, frozenset)
    assert isinstance(INTERACTIVE_MODE_PUBLIC_MEMBERS, frozenset)
    assert all(not name.startswith("_") for name in AGENT_SESSION_PUBLIC_MEMBERS)
    assert all(not name.startswith("_") for name in INTERACTIVE_MODE_PUBLIC_MEMBERS)


def test_contract_modules_do_not_import_facades_or_composition_roots() -> None:
    for path in CONTRACT_MODULES:
        assert _imported_modules(path).isdisjoint(FORBIDDEN_CONTRACT_IMPORTS)


def test_session_factory_modules_do_not_import_concrete_agent_session() -> None:
    for path in SESSION_FACTORY_MODULES:
        assert "travis.coding_agent.agent_session" not in _imported_modules(path)


def test_session_runtime_public_descriptors_are_recorded() -> None:
    assert _public_descriptors(_SessionRuntime) <= AGENT_SESSION_PUBLIC_MEMBERS
    assert _public_descriptors(AgentSession) <= AGENT_SESSION_PUBLIC_MEMBERS


def test_interactive_runtime_public_descriptors_are_recorded() -> None:
    assert _public_descriptors(_InteractiveRuntime) <= INTERACTIVE_MODE_PUBLIC_MEMBERS
    assert _public_descriptors(InteractiveMode) <= INTERACTIVE_MODE_PUBLIC_MEMBERS
