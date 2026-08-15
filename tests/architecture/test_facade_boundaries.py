from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FACADE_LIMITS = {
    "travis/coding_agent/agent_session.py": (900, "AgentSession", 50),
    "travis/tui/interactive_mode.py": (500, "InteractiveMode", 20),
    "travis/tui/component.py": (120, None, 0),
    "travis/ai/providers/travis_env.py": (320, None, 12),
}
OWNER_GLOBS = (
    "travis/coding_agent/session_types.py",
    "travis/coding_agent/session_models.py",
    "travis/coding_agent/model_roles.py",
    "travis/coding_agent/session_bash.py",
    "travis/coding_agent/session_tooling.py",
    "travis/coding_agent/session_persistence.py",
    "travis/coding_agent/session_extensions.py",
    "travis/coding_agent/session_subagents.py",
    "travis/coding_agent/subagent_trace.py",
    "travis/coding_agent/session_turns.py",
    "travis/coding_agent/session_policy_controller.py",
    "travis/coding_agent/session_events.py",
    "travis/tui/components/*.py",
    "travis/tui/interactive_turn_controller.py",
    "travis/tui/interactive_command_dispatcher.py",
    "travis/tui/interactive_session_commands.py",
    "travis/tui/interactive_model_auth.py",
    "travis/tui/interactive_process_commands.py",
    "travis/tui/interactive_view.py",
    "travis/tui/interactive_prompt_input.py",
    "travis/tui/interactive_extensions.py",
    "travis/tui/interactive_lsp.py",
    "travis/tui/footer_data.py",
    "travis/tui/interactive_shutdown.py",
    "travis/ai/providers/*_stream.py",
    "travis/ai/providers/message_translation.py",
    "travis/ai/providers/provider_*.py",
    "travis/ai/providers/runtime_auth.py",
    "travis/ai/providers/sse_common.py",
    "travis/ai/providers/streaming_json.py",
    "travis/coding_agent/capabilities/*.py",
    "travis/coding_agent/resource_candidates.py",
    "travis/coding_agent/resource_extensions.py",
    "travis/coding_agent/resource_loader.py",
    "travis/coding_agent/resource_capability_projection.py",
    "travis/coding_agent/artifact_store.py",
    "travis/coding_agent/artifact_manifest.py",
    "travis/coding_agent/artifacts.py",
    "travis/coding_agent/resource_refs.py",
    "travis/coding_agent/artifact_gc.py",
)
FORBIDDEN_OWNER_IMPORTS = {
    "travis.coding_agent.agent_session",
    "travis.app",
    "travis.tui.interactive_mode",
    "travis.tui.component",
    "travis.ai.providers.travis_env",
}
COORDINATION_OWNERS = (
    "travis/coding_agent/agent_roles.py",
    "travis/coding_agent/subagent_roles.py",
    "travis/coding_agent/subagent_result_types.py",
    "travis/coding_agent/subagent_results.py",
    "travis/coding_agent/subagent_supervision.py",
)
FORBIDDEN_COORDINATION_PREFIXES = (
    "travis.agent",
    "travis.app",
    "travis.tui",
    "travis.coding_agent.agent_session",
)


def _defined_method_count(tree: ast.Module, class_name: str | None) -> int:
    if class_name is None:
        return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in owner.body)


def test_facades_stay_below_size_and_method_limits() -> None:
    failures: list[tuple[str, int, int]] = []
    for relative, (line_limit, class_name, method_limit) in FACADE_LIMITS.items():
        path = ROOT / relative
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        line_count = len(source.splitlines())
        method_count = _defined_method_count(tree, class_name)
        if line_count > line_limit or method_count > method_limit:
            failures.append((relative, line_count, method_count))

    assert failures == []


def test_collaborator_modules_are_bounded_and_do_not_import_facades() -> None:
    failures: list[str] = []
    paths = {path for pattern in OWNER_GLOBS for path in ROOT.glob(pattern)}
    for path in sorted(paths):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        if len(source.splitlines()) > 750:
            failures.append(f"{relative}: exceeds 750 lines")
        tree = ast.parse(source, filename=relative)
        for node in ast.walk(tree):
            imported = node.module if isinstance(node, ast.ImportFrom) else None
            if imported in FORBIDDEN_OWNER_IMPORTS:
                failures.append(f"{relative}:{node.lineno}: imports {imported}")

    assert failures == []


def test_tool_policy_does_not_import_generic_agent_or_tui() -> None:
    failures: list[str] = []
    for path in sorted((ROOT / "travis/coding_agent/policy").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = node.module if isinstance(node, ast.ImportFrom) else None
            if imported is not None and imported.startswith(("travis.agent", "travis.tui")):
                failures.append(f"{path.relative_to(ROOT)}:{node.lineno}: imports {imported}")

    assert failures == []


def test_language_services_do_not_import_tui_or_facades() -> None:
    failures: list[str] = []
    for path in sorted((ROOT / "travis/coding_agent/language_services").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = node.module if isinstance(node, ast.ImportFrom) else None
            if imported in FORBIDDEN_OWNER_IMPORTS or (
                imported is not None and imported.startswith("travis.tui")
            ):
                failures.append(f"{path.relative_to(ROOT)}:{node.lineno}: imports {imported}")

    assert failures == []


def test_coordination_owners_do_not_import_tui_app_session_or_agent_loop() -> None:
    failures: list[str] = []
    for relative in COORDINATION_OWNERS:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = (node.module,)
            elif isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            for module in imported:
                if module.startswith(FORBIDDEN_COORDINATION_PREFIXES):
                    failures.append(f"{relative}:{node.lineno}: imports {module}")

    assert failures == []
