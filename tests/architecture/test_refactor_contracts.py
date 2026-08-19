from __future__ import annotations

import ast
import inspect
from pathlib import Path

import travis.ai as ai_package
import travis.ai.types as ai_types
import travis.coding_agent.agent_session as agent_session_module
import travis.coding_agent.session_types as session_types
import travis.tui.component as component_compatibility_module
import travis.tui.components as components_package
from travis.coding_agent.agent_session import AgentSession, _SessionRuntime
from travis.coding_agent.session_contracts import AGENT_SESSION_PUBLIC_MEMBERS
from travis.tui.interactive_contracts import INTERACTIVE_MODE_PUBLIC_MEMBERS
from travis.tui.interactive_mode import InteractiveMode, _InteractiveRuntime

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = ROOT / "travis"
CONTRACT_MODULES = (
    ROOT / "travis/coding_agent/session_contracts.py",
    ROOT / "travis/coding_agent/session_ports.py",
    ROOT / "travis/tui/interactive_contracts.py",
    ROOT / "travis/tui/interactive_services.py",
)
COMPOSITION_MODULES = (
    ROOT / "travis/controller_ports.py",
    ROOT / "travis/coding_agent/agent_session.py",
    ROOT / "travis/coding_agent/session_ports.py",
    ROOT / "travis/tui/interactive_mode.py",
    ROOT / "travis/tui/interactive_services.py",
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
AI_TYPE_REEXPORTS = (
    "Api",
    "AssistantMessage",
    "AssistantMessageEvent",
    "ContentBlock",
    "Context",
    "Cost",
    "CostTier",
    "DoneEvent",
    "ErrorEvent",
    "ImageContent",
    "Message",
    "Model",
    "ProviderResponse",
    "SimpleStreamOptions",
    "StartEvent",
    "StopReason",
    "StreamOptions",
    "TextContent",
    "TextDeltaEvent",
    "TextEndEvent",
    "TextStartEvent",
    "ThinkingContent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ThinkingLevel",
    "ThinkingStartEvent",
    "Tool",
    "ToolCall",
    "ToolResultMessage",
    "ToolcallDeltaEvent",
    "ToolcallEndEvent",
    "ToolcallStartEvent",
    "Transport",
    "Usage",
    "UserMessage",
    "empty_usage",
    "now_ms",
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


def test_production_modules_do_not_use_star_imports() -> None:
    offenders: list[str] = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_explicit_imports_preserve_compatibility_reexports() -> None:
    for name in session_types.__all__:
        assert getattr(agent_session_module, name) is getattr(session_types, name)
    for name in components_package.__all__:
        assert getattr(component_compatibility_module, name) is getattr(components_package, name)
    for name in AI_TYPE_REEXPORTS:
        assert getattr(ai_package, name) is getattr(ai_types, name)


def test_contract_inventories_are_immutable_and_public_only() -> None:
    assert isinstance(AGENT_SESSION_PUBLIC_MEMBERS, frozenset)
    assert isinstance(INTERACTIVE_MODE_PUBLIC_MEMBERS, frozenset)
    assert all(not name.startswith("_") for name in AGENT_SESSION_PUBLIC_MEMBERS)
    assert all(not name.startswith("_") for name in INTERACTIVE_MODE_PUBLIC_MEMBERS)


def test_contract_modules_do_not_import_facades_or_composition_roots() -> None:
    for path in CONTRACT_MODULES:
        assert _imported_modules(path).isdisjoint(FORBIDDEN_CONTRACT_IMPORTS)


def test_collaborator_contracts_do_not_expose_generic_runtime_escape_hatches() -> None:
    for path in CONTRACT_MODULES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__"
            for node in ast.walk(tree)
        )
        assert "state: object" not in source


def test_controller_composition_has_no_generic_method_rebinding_bridge() -> None:
    offenders: list[str] = []
    for path in COMPOSITION_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "types" and any(
                alias.name == "MethodType" for alias in node.names
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: imports MethodType")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattribute__":
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: defines generic __getattribute__"
                )

    assert offenders == []


def test_session_factory_modules_do_not_import_concrete_agent_session() -> None:
    for path in SESSION_FACTORY_MODULES:
        assert "travis.coding_agent.agent_session" not in _imported_modules(path)


def test_session_runtime_public_descriptors_are_recorded() -> None:
    assert _public_descriptors(_SessionRuntime) <= AGENT_SESSION_PUBLIC_MEMBERS
    assert _public_descriptors(AgentSession) <= AGENT_SESSION_PUBLIC_MEMBERS


def test_interactive_runtime_public_descriptors_are_recorded() -> None:
    assert _public_descriptors(_InteractiveRuntime) <= INTERACTIVE_MODE_PUBLIC_MEMBERS
    assert _public_descriptors(InteractiveMode) <= INTERACTIVE_MODE_PUBLIC_MEMBERS
