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
    "travis/tui/interactive_extensions.py",
    "travis/tui/footer_data.py",
    "travis/tui/interactive_shutdown.py",
    "travis/ai/providers/*_stream.py",
    "travis/ai/providers/message_translation.py",
    "travis/ai/providers/provider_*.py",
    "travis/ai/providers/runtime_auth.py",
    "travis/ai/providers/sse_common.py",
    "travis/ai/providers/streaming_json.py",
)
FORBIDDEN_OWNER_IMPORTS = {
    "travis.coding_agent.agent_session",
    "travis.app",
    "travis.tui.interactive_mode",
    "travis.tui.component",
    "travis.ai.providers.travis_env",
}


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
