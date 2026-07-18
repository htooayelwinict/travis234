from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from pathlib import Path

import pytest

from travis.ai.providers.faux import faux_model
from travis.coding_agent import AgentSession
from travis.coding_agent.extensions import ExtensionRunner


def test_extension_ui_object_can_be_present_without_reporting_ui_available(tmp_path: Path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    ui = object()

    runner.set_ui_context(ui, "json", has_ui=False)

    context = runner.create_context()
    assert context.ui is ui
    assert context.mode == "json"
    assert context.has_ui is False


def test_session_extension_binding_preserves_explicit_ui_availability(tmp_path: Path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    session.bind_extensions(
        {
            "uiContext": object(),
            "mode": "json",
            "hasUI": False,
        }
    )

    context = runner.create_context()
    assert context.mode == "json"
    assert context.has_ui is False


def test_noop_extension_ui_returns_safe_noninteractive_values() -> None:
    try:
        module = importlib.import_module("travis.coding_agent.extension_host")
    except ModuleNotFoundError:
        pytest.fail("extension host module is missing")
    ui = module.NoOpExtensionUI()

    assert ui.select("Pick", ["one"]) is None
    assert ui.confirm("Confirm", "Continue?") is False
    assert ui.input("Value") is None
    assert ui.get_editor_text() == ""
    assert ui.set_theme("night") == {"success": False, "error": "UI not available"}
    assert callable(ui.on_terminal_input(lambda _data: None))


class _FakeSession:
    def __init__(self, name: str) -> None:
        self.name = name
        self.bindings: list[dict[str, object]] = []

    def bind_extensions(self, bindings: dict[str, object]) -> None:
        self.bindings.append(dict(bindings))


class _FakeApp:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self._listeners: list[Callable[[_FakeSession], None]] = []

    def subscribe_session_rebound(
        self,
        listener: Callable[[_FakeSession], None],
    ) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            self._listeners.remove(listener)

        return unsubscribe

    def replace(self, session: _FakeSession) -> None:
        self.session = session
        for listener in list(self._listeners):
            listener(session)


def test_extension_host_adapter_binds_initial_and_replacement_before_rebound_callback() -> None:
    module = importlib.import_module("travis.coding_agent.extension_host")
    first = _FakeSession("first")
    replacement = _FakeSession("replacement")
    app = _FakeApp(first)
    seen: list[tuple[str, str]] = []

    adapter = module.ExtensionHostAdapter(
        app,
        mode="tui",
        bindings_factory=lambda session: {"sessionName": session.name},
        on_rebound=lambda session: seen.append(
            (session.name, str(session.bindings[-1]["mode"]))
        ),
    )
    adapter.start()
    app.replace(replacement)

    assert first.bindings == [{"sessionName": "first", "mode": "tui"}]
    assert replacement.bindings == [{"sessionName": "replacement", "mode": "tui"}]
    assert seen == [("replacement", "tui")]

    adapter.dispose()
    app.replace(_FakeSession("after-dispose"))
    assert seen == [("replacement", "tui")]


def test_extension_host_adapter_ignores_embedding_without_extension_session() -> None:
    module = importlib.import_module("travis.coding_agent.extension_host")
    factory_calls: list[object] = []
    app = object()
    adapter = module.ExtensionHostAdapter(
        app,
        mode="print",
        bindings_factory=lambda session: factory_calls.append(session) or {},
    )

    adapter.start()
    adapter.dispose()

    assert factory_calls == []


def test_async_extension_command_is_awaited_exactly_once(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    calls: list[tuple[str, str]] = []

    async def handler(args, context):
        await asyncio.sleep(0)
        calls.append((args, context.cwd))

    session.extension_runner.register_command("async-probe", {"handler": handler})

    assert session._try_execute_extension_command("/async-probe value") == []
    assert calls == [("value", str(tmp_path))]


def test_extension_command_internal_typeerror_is_not_reclassified_as_legacy_arity(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    calls: list[str] = []

    def handler(args, _context):
        calls.append(args)
        raise TypeError("inside extension command")

    session.extension_runner.register_command("broken-probe", {"handler": handler})

    with pytest.raises(TypeError, match="inside extension command"):
        session._try_execute_extension_command("/broken-probe value")
    assert calls == ["value"]


def test_extension_command_context_combines_runtime_and_travis_action_surfaces(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    ui = object()
    seen: list[object] = []
    session.bind_extensions({"uiContext": ui, "hasUI": True, "mode": "tui"})
    session.extension_runner.register_command(
        "context-probe",
        {"handler": lambda _args, context: seen.append(context)},
    )

    assert session._try_execute_extension_command("/context-probe") == []

    context = seen[0]
    assert context.ui is ui
    assert context.mode == "tui"
    assert context.has_ui is True
    assert context.model is session.model
    assert context.model_registry is session.model_registry
    assert context.get_context_usage() == session.get_context_usage()
    assert callable(context.send_message)
    assert callable(context.exec)


def test_binding_empty_extension_host_does_not_change_context_envelope(tmp_path: Path) -> None:
    module = importlib.import_module("travis.coding_agent.extension_host")
    session = AgentSession(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
    )
    before = {
        "system_prompt": session.system_prompt,
        "messages": list(session.messages),
        "active_tools": session.get_active_tool_names(),
        "all_tools": session.get_all_tools(),
    }

    session.bind_extensions(
        {
            "mode": "print",
            "uiContext": module.NoOpExtensionUI(),
            "hasUI": False,
        }
    )

    assert session.system_prompt == before["system_prompt"]
    assert session.messages == before["messages"]
    assert session.get_active_tool_names() == before["active_tools"]
    assert session.get_all_tools() == before["all_tools"]
