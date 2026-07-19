"""Mode-facing bindings for the Python extension runtime."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Iterable
from typing import Any

from travis.agent.async_utils import resolve, run_sync


def settle_extension_result(value: object) -> object:
    """Resolve one optional extension awaitable on a synchronous host boundary."""

    if inspect.isawaitable(value):
        return run_sync(resolve(value))
    return value


def call_extension_command(handler: Callable[..., object], args: str, context: object) -> object:
    """Invoke a command once while supporting the legacy one-argument shape."""

    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        accepts_context = True
    else:
        positional = 0
        accepts_context = False
        for parameter in signature.parameters.values():
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                accepts_context = True
                break
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                positional += 1
        else:
            accepts_context = positional >= 2

    value = handler(args, context) if accepts_context else handler(args)
    return settle_extension_result(value)


class ExtensionCommandContextProxy:
    """Combine guarded runner context with Travis command conveniences."""

    _ALIASES = {
        "hasUI": "has_ui",
        "sessionManager": "session_manager",
        "modelRegistry": "model_registry",
        "isIdle": "is_idle",
        "isProjectTrusted": "is_project_trusted",
        "hasPendingMessages": "has_pending_messages",
        "getContextUsage": "get_context_usage",
        "getSystemPrompt": "get_system_prompt",
        "getSystemPromptOptions": "get_system_prompt_options",
        "waitForIdle": "wait_for_idle",
        "newSession": "new_session",
        "navigateTree": "navigate_tree",
        "switchSession": "switch_session",
    }

    def __init__(self, runtime_context: object, action_context: object) -> None:
        self._runtime_context = runtime_context
        self._action_context = action_context

    def __getattr__(self, name: str) -> object:
        target = self._ALIASES.get(name, name)
        assert_active = getattr(self._runtime_context, "_assert_active", None)
        if callable(assert_active):
            assert_active()
        try:
            return getattr(self._runtime_context, target)
        except AttributeError:
            return getattr(self._action_context, target)


class ExtensionHostAdapter:
    """Bind the active session to one presentation host and its replacements."""

    def __init__(
        self,
        app: object,
        *,
        mode: str,
        bindings_factory: Callable[[object], dict[str, object] | None],
        before_rebind: Callable[[object], object] | None = None,
        on_rebound: Callable[[object], object] | None = None,
    ) -> None:
        self._app = app
        self._mode = mode
        self._bindings_factory = bindings_factory
        self._before_rebind = before_rebind
        self._on_rebound = on_rebound
        self._started = False
        self._unsubscribe: Callable[[], None] | None = None

    def start(self) -> None:
        session = getattr(self._app, "session", None)
        if not callable(getattr(session, "bind_extensions", None)):
            return
        if self._started:
            return
        self._started = True
        try:
            subscribe = getattr(self._app, "subscribe_session_rebound", None)
            if callable(subscribe):
                self._unsubscribe = subscribe(self._handle_rebound)
            self.bind(session)
        except BaseException:
            self.dispose()
            raise

    def bind(self, session: object) -> None:
        bind_extensions = getattr(session, "bind_extensions", None)
        if not callable(bind_extensions):
            return
        bindings = dict(self._bindings_factory(session) or {})
        bindings["mode"] = self._mode
        bind_extensions(bindings)

    def _handle_rebound(self, session: object) -> None:
        if self._before_rebind is not None:
            self._before_rebind(session)
        self.bind(session)
        if self._on_rebound is not None:
            self._on_rebound(session)

    def dispose(self) -> None:
        self._started = False
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()


def noninteractive_extension_bindings(
    app: object,
    session: object,
    *,
    diagnostic_output: object | None = None,
) -> dict[str, object]:
    """Build safe bindings shared by print, JSON, and RPC hosts."""

    output = diagnostic_output or sys.stderr

    def report_error(error: dict[str, object]) -> None:
        path = str(error.get("extensionPath") or "<extension>")
        message = str(error.get("error") or "unknown extension error")
        output.write(f"Extension error ({path}): {message}\n")
        output.flush()

    runtime = getattr(app, "session_runtime")
    agent = getattr(session, "agent")
    return {
        "uiContext": NoOpExtensionUI(),
        "hasUI": False,
        "abortHandler": getattr(agent, "abort"),
        "shutdownHandler": getattr(app, "close"),
        "onError": report_error,
        "commandContextActions": {
            "waitForIdle": getattr(agent, "wait_for_idle"),
            "newSession": getattr(runtime, "new_session"),
            "fork": getattr(runtime, "fork"),
            "navigateTree": getattr(session, "navigate_tree"),
            "switchSession": getattr(runtime, "switch_session"),
            "reload": getattr(session, "reload"),
        },
    }


class NoOpExtensionUI:
    """Safe UI surface for print, JSON, and RPC extension contexts."""

    _ALIASES = {
        "onTerminalInput": "on_terminal_input",
        "setStatus": "set_status",
        "setWorkingMessage": "set_working_message",
        "setWorkingVisible": "set_working_visible",
        "setWorkingIndicator": "set_working_indicator",
        "setHiddenThinkingLabel": "set_hidden_thinking_label",
        "setWidget": "set_widget",
        "setFooter": "set_footer",
        "setHeader": "set_header",
        "setTitle": "set_title",
        "pasteToEditor": "paste_to_editor",
        "setEditorText": "set_editor_text",
        "getEditorText": "get_editor_text",
        "addAutocompleteProvider": "add_autocomplete_provider",
        "setEditorComponent": "set_editor_component",
        "getEditorComponent": "get_editor_component",
        "getAllThemes": "get_all_themes",
        "getTheme": "get_theme",
        "setTheme": "set_theme",
        "getToolsExpanded": "get_tools_expanded",
        "setToolsExpanded": "set_tools_expanded",
    }

    def __getattr__(self, name: str) -> object:
        target = self._ALIASES.get(name)
        if target is None:
            raise AttributeError(name)
        return getattr(self, target)

    @staticmethod
    def _unavailable_value(_operation: str) -> None:
        return None

    def select(
        self,
        _title: str,
        _options: Iterable[str],
        _dialog_options: dict[str, object] | None = None,
    ) -> None:
        return self._unavailable_value("select")

    def confirm(
        self,
        _title: str,
        _message: str,
        _options: dict[str, object] | None = None,
    ) -> bool:
        return False

    def input(
        self,
        _title: str,
        _placeholder: str | None = None,
        _options: dict[str, object] | None = None,
    ) -> None:
        return self._unavailable_value("input")

    def notify(self, _message: str, _kind: str | None = None) -> None:
        return None

    def on_terminal_input(self, _handler: Callable[[str], object]) -> Callable[[], None]:
        return lambda: None

    def set_status(self, _key: str, _text: str | None, _options: dict[str, object] | None = None) -> None:
        return None

    def set_working_message(self, _message: str | None = None) -> None:
        return None

    def set_working_visible(self, _visible: bool) -> None:
        return None

    def set_working_indicator(self, _options: dict[str, object] | None = None) -> None:
        return None

    def set_hidden_thinking_label(self, _label: str | None = None) -> None:
        return None

    def set_widget(self, _key: str, _content: object = None, _options: dict[str, object] | None = None) -> None:
        return None

    def set_footer(self, _factory: Callable[..., object] | None = None) -> None:
        return None

    def set_header(self, _factory: Callable[..., object] | None = None) -> None:
        return None

    def set_title(self, _title: str) -> None:
        return None

    def custom(self, _factory: Callable[..., object], _options: dict[str, object] | None = None) -> None:
        return None

    def paste_to_editor(self, _text: str) -> None:
        return None

    def set_editor_text(self, _text: str) -> None:
        return None

    def get_editor_text(self) -> str:
        return ""

    def editor(self, _title: str, _prefill: str | None = None) -> None:
        return None

    def add_autocomplete_provider(self, _factory: Callable[[object], object]) -> None:
        return None

    def set_editor_component(self, _factory: Callable[..., object] | None = None) -> None:
        return None

    def get_editor_component(self) -> None:
        return None

    @property
    def theme(self) -> None:
        return None

    def get_all_themes(self) -> list[object]:
        return []

    def get_theme(self, _name: str) -> None:
        return None

    def set_theme(self, _theme: object) -> dict[str, Any]:
        return {"success": False, "error": "UI not available"}

    def get_tools_expanded(self) -> bool:
        return False

    def set_tools_expanded(self, _expanded: bool) -> None:
        return None
