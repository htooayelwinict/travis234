from __future__ import annotations

from tests._support_tui import CodingApp, FakeTerminal, faux_model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.session_contracts import AGENT_SESSION_PUBLIC_MEMBERS
from travis.runtime_facade import RuntimeFacade
from travis.tui.interactive_contracts import INTERACTIVE_MODE_PUBLIC_MEMBERS
from travis.tui.interactive_mode import InteractiveMode


class _Runtime:
    value = "runtime"

    def action(self) -> str:
        return "done"


class _Facade(RuntimeFacade):
    def __init__(self) -> None:
        object.__setattr__(self, "_runtime", _Runtime())


def test_runtime_facade_forwards_get_set_dir_and_overrides() -> None:
    facade = _Facade()

    assert facade.value == "runtime"
    assert facade.action() == "done"
    assert "action" in dir(facade)

    facade.value = "changed"
    facade.action = lambda: "override"

    assert facade._runtime.value == "changed"
    assert facade.action() == "override"


class _AgentSessionRuntimeProbe:
    def __init__(self) -> None:
        self.dispose_calls = 0
        self.shutdown_reasons: list[str] = []
        self._memory_store = None
        self.operation_coordinator = _CloseProbe()
        self._owns_operation_runtime = False

    def dispose(self) -> None:
        self.dispose_calls += 1

    def shutdown(self, reason: str = "quit") -> None:
        self.shutdown_reasons.append(reason)


class _CloseProbe:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_agent_session_explicit_lifecycle_methods_delegate_to_runtime() -> None:
    runtime = _AgentSessionRuntimeProbe()
    facade = object.__new__(AgentSession)
    object.__setattr__(facade, "_runtime", runtime)

    facade.shutdown(reason="replacement")

    assert runtime.shutdown_reasons == ["replacement"]
    assert runtime.operation_coordinator.close_calls == 1


def test_interactive_mode_preserves_dynamic_runtime_overrides() -> None:
    runtime = _Runtime()
    facade = object.__new__(InteractiveMode)
    object.__setattr__(facade, "_runtime", runtime)

    assert facade.action() == "done"

    facade.action = lambda: "interactive override"

    assert runtime.action() == "interactive override"
    assert facade.action() == "interactive override"


_AGENT_SESSION_CALLABLE_MEMBERS = frozenset(
    {
        "abort_bash",
        "abort_retry",
        "append_custom_entry",
        "bind_extensions",
        "branch",
        "clear_queue",
        "compact",
        "continue_",
        "create_branched_session",
        "create_replaced_session_context",
        "cycle_model",
        "cycle_thinking_level",
        "dispose",
        "emit_agent_settled",
        "emit_deferred_session_start",
        "execute_bash",
        "export_to_html",
        "export_to_jsonl",
        "follow_up",
        "get_active_tool_names",
        "get_all_tools",
        "get_available_thinking_levels",
        "get_context_usage",
        "get_follow_up_messages",
        "get_known_tool_names",
        "get_last_assistant_text",
        "get_session_entry",
        "get_session_leaf_id",
        "get_session_stats",
        "get_steering_messages",
        "get_tool_definition",
        "get_user_messages_for_forking",
        "has_extension_handlers",
        "navigate_tree",
        "prompt",
        "record_bash_result",
        "refresh_tools",
        "reload",
        "reload_resources",
        "rename_session",
        "reset_generation_param_override",
        "reset_generation_param_overrides",
        "resolve_model_role",
        "send_custom_message",
        "session_tree",
        "set_active_tools_by_name",
        "set_auto_retry_enabled",
        "set_compaction_manager",
        "set_follow_up_mode",
        "set_generation_param_override",
        "set_label",
        "set_model",
        "set_scoped_models",
        "set_session_name",
        "set_steering_mode",
        "set_thinking_level",
        "shutdown",
        "steer",
        "subscribe",
        "supports_thinking",
        "with_model_overrides",
    }
)

_INTERACTIVE_MODE_CALLABLE_MEMBERS = frozenset(
    {
        "add_autocomplete_provider",
        "add_terminal_input_listener",
        "create_base_autocomplete_provider",
        "get_autocomplete_suggestions",
        "get_editor_text",
        "init",
        "paste_to_editor",
        "prompt_extension_confirm",
        "prompt_extension_custom",
        "prompt_extension_editor",
        "prompt_extension_input",
        "prompt_extension_select",
        "run",
        "set_editor_text",
        "set_extension_footer",
        "set_extension_header",
        "set_extension_status",
        "set_extension_widget",
        "set_hidden_thinking_label",
        "set_terminal_title",
        "set_working_indicator",
        "set_working_message",
        "set_working_visible",
        "setup_autocomplete_provider",
    }
)


def test_agent_session_supported_inventory_resolves_on_normal_facade(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=False)
    session = app.session

    for name in AGENT_SESSION_PUBLIC_MEMBERS:
        value = getattr(session, name)
        if name in _AGENT_SESSION_CALLABLE_MEMBERS:
            assert callable(value), name


def test_interactive_mode_supported_inventory_resolves_on_normal_facade(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")

    for name in INTERACTIVE_MODE_PUBLIC_MEMBERS:
        value = getattr(mode, name)
        if name in _INTERACTIVE_MODE_CALLABLE_MEMBERS:
            assert callable(value), name
