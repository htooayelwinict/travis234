"""Extension runner subset ported from Travis coding-agent extension plumbing."""

from __future__ import annotations

from collections.abc import Callable
import copy
import inspect
from dataclasses import dataclass
from typing import Any

from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.tools.types import ToolDefinition, wrap_tool_definition

ExtensionEvent = dict[str, Any]
ExtensionHandler = Callable[[ExtensionEvent], object]
ExtensionErrorListener = Callable[[dict[str, object]], None]

_SESSION_BEFORE_EVENTS = frozenset(
    {
        "session_before_switch",
        "session_before_fork",
        "session_before_compact",
        "session_before_tree",
    }
)


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    source_info: SourceInfo

    @property
    def sourceInfo(self) -> SourceInfo:
        return self.source_info


@dataclass(frozen=True)
class RegisteredCommand:
    name: str
    description: str | None
    handler: Callable[..., object]
    source_info: SourceInfo
    get_argument_completions: Callable[[str], object] | None = None

    @property
    def sourceInfo(self) -> SourceInfo:
        return self.source_info

    @property
    def getArgumentCompletions(self) -> Callable[[str], object] | None:
        return self.get_argument_completions


@dataclass(frozen=True)
class ExtensionFlag:
    name: str
    type: str
    description: str | None = None
    default: bool | str | None = None
    extension_path: str = "<python-extension>"

    @property
    def extensionPath(self) -> str:
        return self.extension_path


@dataclass(frozen=True)
class ExtensionShortcut:
    key: str
    handler: Callable[..., object]
    description: str | None = None
    extension_path: str = "<python-extension>"

    @property
    def extensionPath(self) -> str:
        return self.extension_path


_STALE_CONTEXT_MESSAGE = (
    "This extension ctx is stale after session replacement or reload. Do not use a captured travis or command ctx "
    "after ctx.newSession(), ctx.fork(), ctx.switchSession(), or ctx.reload()."
)


def define_tool(tool: ToolDefinition) -> ToolDefinition:
    return tool


defineTool = define_tool


def wrap_registered_tool(registered_tool: RegisteredTool, runner: "ExtensionRunner"):
    return wrap_tool_definition(registered_tool.definition, lambda: runner.create_context())


def wrap_registered_tools(registered_tools: list[RegisteredTool], runner: "ExtensionRunner") -> list:
    return [wrap_registered_tool(registered_tool, runner) for registered_tool in registered_tools]


wrapRegisteredTool = wrap_registered_tool
wrapRegisteredTools = wrap_registered_tools


def _tool_name(event: object) -> object:
    if isinstance(event, dict):
        return event.get("toolName", event.get("tool_name"))
    return getattr(event, "toolName", getattr(event, "tool_name", None))


def is_bash_tool_result(event: object) -> bool:
    return _tool_name(event) == "bash"


def is_read_tool_result(event: object) -> bool:
    return _tool_name(event) == "read"


def is_edit_tool_result(event: object) -> bool:
    return _tool_name(event) == "edit"


def is_write_tool_result(event: object) -> bool:
    return _tool_name(event) == "write"


def is_grep_tool_result(event: object) -> bool:
    return _tool_name(event) == "grep"


def is_find_tool_result(event: object) -> bool:
    return _tool_name(event) == "find"


def is_ls_tool_result(event: object) -> bool:
    return _tool_name(event) == "ls"


def is_tool_call_event_type(tool_name: str, event: object) -> bool:
    return _tool_name(event) == tool_name


isBashToolResult = is_bash_tool_result
isReadToolResult = is_read_tool_result
isEditToolResult = is_edit_tool_result
isWriteToolResult = is_write_tool_result
isGrepToolResult = is_grep_tool_result
isFindToolResult = is_find_tool_result
isLsToolResult = is_ls_tool_result
isToolCallEventType = is_tool_call_event_type


class ExtensionContextView:
    """Lazy context passed to extension event handlers."""

    def __init__(self, runner: "ExtensionRunner", generation: int) -> None:
        self._runner = runner
        self._generation = generation

    def _assert_active(self) -> None:
        if self._generation != self._runner._context_generation:
            raise RuntimeError(self._runner._stale_context_message)

    @property
    def ui(self) -> object | None:
        self._assert_active()
        return self._runner._ui_context

    @property
    def mode(self) -> str:
        self._assert_active()
        return self._runner._mode

    @property
    def has_ui(self) -> bool:
        self._assert_active()
        return self._runner.has_ui()

    @property
    def hasUI(self) -> bool:
        return self.has_ui

    @property
    def cwd(self) -> str:
        self._assert_active()
        return self._runner._cwd

    @property
    def session_manager(self) -> object | None:
        self._assert_active()
        return self._runner._session_manager

    @property
    def sessionManager(self) -> object | None:
        return self.session_manager

    @property
    def model_registry(self) -> object | None:
        self._assert_active()
        return self._runner._model_registry

    @property
    def modelRegistry(self) -> object | None:
        return self.model_registry

    @property
    def model(self) -> object | None:
        self._assert_active()
        return self._runner._get_model()

    @property
    def signal(self) -> object | None:
        self._assert_active()
        return self._runner._get_signal()

    def is_idle(self) -> bool:
        self._assert_active()
        return bool(self._runner._is_idle())

    isIdle = is_idle

    def is_project_trusted(self) -> bool:
        self._assert_active()
        return bool(self._runner._is_project_trusted())

    isProjectTrusted = is_project_trusted

    def abort(self) -> object:
        self._assert_active()
        return self._runner._abort()

    def has_pending_messages(self) -> bool:
        self._assert_active()
        return bool(self._runner._has_pending_messages())

    hasPendingMessages = has_pending_messages

    def shutdown(self) -> object:
        self._assert_active()
        return self._runner._shutdown()

    def get_context_usage(self) -> object | None:
        self._assert_active()
        return self._runner._get_context_usage()

    getContextUsage = get_context_usage

    def compact(self, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._compact(options)

    def get_system_prompt(self) -> str:
        self._assert_active()
        return str(self._runner._get_system_prompt())

    getSystemPrompt = get_system_prompt

    def spawn_subagent(self, role: str, goal: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._spawn_subagent(role, goal, options)

    spawnSubagent = spawn_subagent

    def list_subagents(self) -> object:
        self._assert_active()
        return self._runner._list_subagents()

    listSubagents = list_subagents

    def get_subagent_result(self, task_id: str) -> object | None:
        self._assert_active()
        return self._runner._get_subagent_result(task_id)

    getSubagentResult = get_subagent_result

    def cancel_subagent(self, task_id: str, reason: str | None = None) -> object:
        self._assert_active()
        return self._runner._cancel_subagent(task_id, reason)

    cancelSubagent = cancel_subagent


class ExtensionCommandContextView(ExtensionContextView):
    """command context with session-control actions."""

    def get_system_prompt_options(self) -> object:
        self._assert_active()
        return self._runner._get_system_prompt_options()

    getSystemPromptOptions = get_system_prompt_options

    def wait_for_idle(self) -> object:
        self._assert_active()
        return self._runner._wait_for_idle()

    waitForIdle = wait_for_idle

    def new_session(self, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._new_session(options)

    newSession = new_session

    def fork(self, entry_id: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._fork(entry_id, options)

    def navigate_tree(self, target_id: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._navigate_tree(target_id, options)

    navigateTree = navigate_tree

    def switch_session(self, session_path: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._switch_session(session_path, options)

    switchSession = switch_session

    def reload(self) -> object:
        self._assert_active()
        return self._runner._reload()


class ExtensionRunner:
    """Small extension runner matching the AgentSession-facing Travis runner API."""

    def __init__(
        self,
        cwd: str = "",
        session_manager: object | None = None,
        model_registry: object | None = None,
    ) -> None:
        self._registered_tools: dict[str, RegisteredTool] = {}
        self._registered_commands: dict[str, RegisteredCommand] = {}
        self._registered_flags: dict[str, ExtensionFlag] = {}
        self._flag_values: dict[str, bool | str] = {}
        self._message_renderers: dict[str, Callable[..., object]] = {}
        self._shortcuts: dict[str, ExtensionShortcut] = {}
        self._handlers: dict[str, list[ExtensionHandler]] = {}
        self._error_listeners: list[ExtensionErrorListener] = []
        self._pending_provider_registrations: list[tuple[str, dict[str, Any], str]] = []
        self._register_provider: Callable[[str, dict[str, Any]], None] | None = None
        self._unregister_provider: Callable[[str], None] | None = None
        self._ui_context: object | None = None
        self._mode = "print"
        self._wait_for_idle: Callable[[], object] = lambda: None
        self._new_session: Callable[[object | None], object] = lambda options=None: {"cancelled": False}
        self._fork: Callable[[str, object | None], object] = lambda entry_id, options=None: {"cancelled": False}
        self._navigate_tree: Callable[[str, object | None], object] = (
            lambda target_id, options=None: {"cancelled": False}
        )
        self._switch_session: Callable[[str, object | None], object] = (
            lambda session_path, options=None: {"cancelled": False}
        )
        self._reload: Callable[[], object] = lambda: None
        self._abort_handler: Callable[[], object] = lambda: None
        self._shutdown_handler: Callable[[], object] = lambda: None
        self._cwd = cwd
        self._session_manager = session_manager
        self._model_registry = model_registry
        self._send_message: Callable[[dict[str, Any], object | None], object] = (
            lambda message, options=None: []
        )
        self._send_user_message: Callable[[object, object | None], object] = lambda content, options=None: None
        self._append_entry: Callable[[str, object | None], object] = lambda custom_type, data=None: None
        self._set_session_name: Callable[[str | None], object] = lambda name: None
        self._get_session_name: Callable[[], str | None] = lambda: None
        self._set_label: Callable[[str, str | None], object] = lambda entry_id, label: None
        self._get_active_tools: Callable[[], list[str]] = lambda: []
        self._get_all_tools: Callable[[], object] = lambda: []
        self._set_active_tools: Callable[[list[str]], object] = lambda tool_names: None
        self._refresh_tools: Callable[[], object] = lambda: None
        self._get_commands: Callable[[], object] = lambda: []
        self._set_model: Callable[[object], object] = lambda model: False
        self._get_thinking_level: Callable[[], object] = lambda: "off"
        self._set_thinking_level: Callable[[object], object] = lambda level: None
        self._get_model: Callable[[], object | None] = lambda: None
        self._is_idle: Callable[[], bool] = lambda: True
        self._is_project_trusted: Callable[[], bool] = lambda: True
        self._get_signal: Callable[[], object | None] = lambda: None
        self._abort: Callable[[], object] = self.abort
        self._has_pending_messages: Callable[[], bool] = lambda: False
        self._shutdown: Callable[[], object] = self.shutdown
        self._get_context_usage: Callable[[], object | None] = lambda: None
        self._compact: Callable[[object | None], object] = lambda options=None: None
        self._get_system_prompt: Callable[[], str] = lambda: ""
        self._get_system_prompt_options: Callable[[], object] = lambda: {"cwd": self._cwd}
        self._spawn_subagent: Callable[[str, str, object | None], object] = lambda role, goal, options=None: None
        self._list_subagents: Callable[[], object] = lambda: []
        self._get_subagent_result: Callable[[str], object | None] = lambda task_id: None
        self._cancel_subagent: Callable[[str, str | None], object] = (
            lambda task_id, reason=None: {"status": "failed", "errors": ["No subagent supervisor bound"]}
        )
        self._context_generation = 0
        self._stale_context_message = _STALE_CONTEXT_MESSAGE

    @property
    def mode(self) -> str:
        return self._mode

    def set_ui_context(self, ui_context: object | None = None, mode: str = "print") -> None:
        self._ui_context = ui_context
        self._mode = mode

    setUIContext = set_ui_context

    def get_ui_context(self) -> object | None:
        return self._ui_context

    getUIContext = get_ui_context

    def has_ui(self) -> bool:
        return self._ui_context is not None

    hasUI = has_ui

    def bind_command_context(self, actions: object | None = None) -> None:
        if actions is None:
            self._wait_for_idle = lambda: None
            self._new_session = lambda options=None: {"cancelled": False}
            self._fork = lambda entry_id, options=None: {"cancelled": False}
            self._navigate_tree = lambda target_id, options=None: {"cancelled": False}
            self._switch_session = lambda session_path, options=None: {"cancelled": False}
            self._reload = lambda: None
            return

        self._wait_for_idle = _callable_action(actions, "waitForIdle", "wait_for_idle") or (lambda: None)
        self._new_session = _callable_action(actions, "newSession", "new_session") or (
            lambda options=None: {"cancelled": False}
        )
        self._fork = _callable_action(actions, "fork") or (lambda entry_id, options=None: {"cancelled": False})
        self._navigate_tree = _callable_action(actions, "navigateTree", "navigate_tree") or (
            lambda target_id, options=None: {"cancelled": False}
        )
        self._switch_session = _callable_action(actions, "switchSession", "switch_session") or (
            lambda session_path, options=None: {"cancelled": False}
        )
        self._reload = _callable_action(actions, "reload") or (lambda: None)

    bindCommandContext = bind_command_context

    def wait_for_idle(self) -> object:
        return self._wait_for_idle()

    waitForIdle = wait_for_idle

    def new_session(self, options: object | None = None) -> object:
        return self._new_session(options)

    newSession = new_session

    def fork(self, entry_id: str, options: object | None = None) -> object:
        return self._fork(entry_id, options)

    def navigate_tree(self, target_id: str, options: object | None = None) -> object:
        return self._navigate_tree(target_id, options)

    navigateTree = navigate_tree

    def switch_session(self, session_path: str, options: object | None = None) -> object:
        return self._switch_session(session_path, options)

    switchSession = switch_session

    def reload(self) -> object:
        return self._reload()

    def set_abort_handler(self, handler: Callable[[], object] | None = None) -> None:
        self._abort_handler = handler or (lambda: None)

    setAbortHandler = set_abort_handler

    def abort(self) -> object:
        return self._abort_handler()

    def set_shutdown_handler(self, handler: Callable[[], object] | None = None) -> None:
        self._shutdown_handler = handler or (lambda: None)

    setShutdownHandler = set_shutdown_handler

    def shutdown(self) -> object:
        return self._shutdown_handler()

    def bind_core(
        self,
        actions: object | None = None,
        context_actions: object | None = None,
        provider_actions: object | None = None,
    ) -> None:
        actions = actions or {}
        self._send_message = _callable_action(actions, "sendMessage", "send_message") or (
            lambda message, options=None: []
        )
        self._send_user_message = _callable_action(actions, "sendUserMessage", "send_user_message") or (
            lambda content, options=None: None
        )
        self._append_entry = _callable_action(actions, "appendEntry", "append_entry") or (
            lambda custom_type, data=None: None
        )
        self._set_session_name = _callable_action(actions, "setSessionName", "set_session_name") or (
            lambda name: None
        )
        self._get_session_name = _callable_action(actions, "getSessionName", "get_session_name") or (lambda: None)
        self._set_label = _callable_action(actions, "setLabel", "set_label") or (lambda entry_id, label: None)
        self._get_active_tools = _callable_action(actions, "getActiveTools", "get_active_tools") or (lambda: [])
        self._get_all_tools = _callable_action(actions, "getAllTools", "get_all_tools") or (lambda: [])
        self._set_active_tools = _callable_action(actions, "setActiveTools", "set_active_tools") or (
            lambda tool_names: None
        )
        self._refresh_tools = _callable_action(actions, "refreshTools", "refresh_tools") or (lambda: None)
        self._get_commands = _callable_action(actions, "getCommands", "get_commands") or (lambda: [])
        self._set_model = _callable_action(actions, "setModel", "set_model") or (lambda model: False)
        self._get_thinking_level = _callable_action(
            actions,
            "getThinkingLevel",
            "get_thinking_level",
        ) or (lambda: "off")
        self._set_thinking_level = _callable_action(
            actions,
            "setThinkingLevel",
            "set_thinking_level",
        ) or (lambda level: None)

        context_actions = context_actions or {}
        self._get_model = _callable_action(context_actions, "getModel", "get_model") or (lambda: None)
        self._is_idle = _callable_action(context_actions, "isIdle", "is_idle") or (lambda: True)
        self._is_project_trusted = _callable_action(
            context_actions,
            "isProjectTrusted",
            "is_project_trusted",
        ) or (lambda: True)
        self._get_signal = _callable_action(context_actions, "getSignal", "get_signal") or (lambda: None)
        self._abort = _callable_action(context_actions, "abort") or self.abort
        self._has_pending_messages = _callable_action(
            context_actions,
            "hasPendingMessages",
            "has_pending_messages",
        ) or (lambda: False)
        self._shutdown = _callable_action(context_actions, "shutdown") or self.shutdown
        self._get_context_usage = _callable_action(
            context_actions,
            "getContextUsage",
            "get_context_usage",
        ) or (lambda: None)
        self._compact = _callable_action(context_actions, "compact") or (lambda options=None: None)
        self._get_system_prompt = _callable_action(
            context_actions,
            "getSystemPrompt",
            "get_system_prompt",
        ) or (lambda: "")
        self._get_system_prompt_options = _callable_action(
            context_actions,
            "getSystemPromptOptions",
            "get_system_prompt_options",
        ) or (lambda: {"cwd": self._cwd})
        self._spawn_subagent = _callable_action(
            actions,
            "spawnSubagent",
            "spawn_subagent",
        ) or (lambda role, goal, options=None: None)
        self._list_subagents = _callable_action(
            actions,
            "listSubagents",
            "list_subagents",
        ) or (lambda: [])
        self._get_subagent_result = _callable_action(
            actions,
            "getSubagentResult",
            "get_subagent_result",
        ) or (lambda task_id: None)
        self._cancel_subagent = _callable_action(
            actions,
            "cancelSubagent",
            "cancel_subagent",
        ) or (lambda task_id, reason=None: {"status": "failed", "errors": ["No subagent supervisor bound"]})

        register_provider = _callable_action(provider_actions or {}, "registerProvider", "register_provider")
        unregister_provider = _callable_action(provider_actions or {}, "unregisterProvider", "unregister_provider")
        if register_provider is not None or unregister_provider is not None:
            self.bind_provider_actions(
                register_provider or (lambda name, config: None),
                unregister_provider or (lambda name: None),
            )

    bindCore = bind_core

    def send_message(self, message: dict[str, Any], options: object | None = None) -> object:
        return self._send_message(message, options)

    sendMessage = send_message

    def send_user_message(self, content: object, options: object | None = None) -> object:
        return self._send_user_message(content, options)

    sendUserMessage = send_user_message

    def append_entry(self, custom_type: str, data: object | None = None) -> object:
        return self._append_entry(custom_type, data)

    appendEntry = append_entry

    def set_session_name(self, name: str | None) -> object:
        return self._set_session_name(name)

    setSessionName = set_session_name

    def get_session_name(self) -> str | None:
        return self._get_session_name()

    getSessionName = get_session_name

    def set_label(self, entry_id: str, label: str | None) -> object:
        return self._set_label(entry_id, label)

    setLabel = set_label

    def get_active_tools(self) -> list[str]:
        return list(self._get_active_tools())

    getActiveTools = get_active_tools

    def get_all_tools(self) -> object:
        return self._get_all_tools()

    getAllTools = get_all_tools

    def set_active_tools(self, tool_names: list[str]) -> object:
        return self._set_active_tools(list(tool_names))

    setActiveTools = set_active_tools

    def refresh_tools(self) -> object:
        return self._refresh_tools()

    refreshTools = refresh_tools

    def get_commands(self) -> object:
        return self._get_commands()

    getCommands = get_commands

    def set_model(self, model: object) -> object:
        return self._set_model(model)

    setModel = set_model

    def get_thinking_level(self) -> object:
        return self._get_thinking_level()

    getThinkingLevel = get_thinking_level

    def set_thinking_level(self, level: object) -> object:
        return self._set_thinking_level(level)

    setThinkingLevel = set_thinking_level

    def spawn_subagent(self, role: str, goal: str, options: object | None = None) -> object:
        return self._spawn_subagent(role, goal, options)

    spawnSubagent = spawn_subagent

    def list_subagents(self) -> object:
        return self._list_subagents()

    listSubagents = list_subagents

    def get_subagent_result(self, task_id: str) -> object | None:
        return self._get_subagent_result(task_id)

    getSubagentResult = get_subagent_result

    def cancel_subagent(self, task_id: str, reason: str | None = None) -> object:
        return self._cancel_subagent(task_id, reason)

    cancelSubagent = cancel_subagent

    def create_context(self) -> ExtensionContextView:
        return ExtensionContextView(self, self._context_generation)

    createContext = create_context

    def create_command_context(self) -> ExtensionCommandContextView:
        return ExtensionCommandContextView(self, self._context_generation)

    createCommandContext = create_command_context

    def invalidate(self, message: str | None = None) -> None:
        self._stale_context_message = message or _STALE_CONTEXT_MESSAGE
        self._context_generation += 1

    def bind_provider_actions(
        self,
        register_provider: Callable[[str, dict[str, Any]], None],
        unregister_provider: Callable[[str], None],
    ) -> None:
        self._register_provider = register_provider
        self._unregister_provider = unregister_provider
        pending = list(self._pending_provider_registrations)
        self._pending_provider_registrations.clear()
        for name, config, _extension_path in pending:
            self.register_provider(name, config)

    bindProviderActions = bind_provider_actions

    def register_provider(self, name: str, config: dict[str, Any], extension_path: str = "<python-extension>") -> None:
        if self._register_provider is None:
            self._pending_provider_registrations.append((name, dict(config), extension_path))
            return
        self._register_provider(name, dict(config))

    registerProvider = register_provider

    @property
    def pending_provider_registrations(self) -> list[tuple[str, dict[str, Any], str]]:
        return [(name, dict(config), extension_path) for name, config, extension_path in self._pending_provider_registrations]

    @property
    def pendingProviderRegistrations(self) -> list[tuple[str, dict[str, Any], str]]:
        return self.pending_provider_registrations

    def clear_pending_provider_registrations(self) -> None:
        self._pending_provider_registrations.clear()

    clearPendingProviderRegistrations = clear_pending_provider_registrations

    def unregister_provider(self, name: str) -> None:
        if self._unregister_provider is None:
            self._pending_provider_registrations = [
                (provider_name, config, extension_path)
                for provider_name, config, extension_path in self._pending_provider_registrations
                if provider_name != name
            ]
            return
        self._unregister_provider(name)

    unregisterProvider = unregister_provider

    def on(self, event_type: str, handler: ExtensionHandler) -> Callable[[], None]:
        handlers = self._handlers.setdefault(event_type, [])
        handlers.append(handler)

        def unsubscribe() -> None:
            current_handlers = self._handlers.get(event_type)
            if not current_handlers:
                return
            try:
                current_handlers.remove(handler)
            except ValueError:
                return
            if not current_handlers:
                self._handlers.pop(event_type, None)

        return unsubscribe

    def on_error(self, listener: ExtensionErrorListener) -> Callable[[], None]:
        self._error_listeners.append(listener)

        def unsubscribe() -> None:
            try:
                self._error_listeners.remove(listener)
            except ValueError:
                return

        return unsubscribe

    onError = on_error

    def emit_error(self, error: dict[str, object]) -> None:
        for listener in list(self._error_listeners):
            listener(error)

    emitError = emit_error

    def has_handlers(self, event_type: str) -> bool:
        return bool(self._handlers.get(event_type))

    hasHandlers = has_handlers

    def emit(self, event: ExtensionEvent) -> object:
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Extension event must include a string 'type'")

        context = self.create_context()
        result: object = None
        for handler in list(self._handlers.get(event_type, [])):
            try:
                handler_result = _call_extension_handler(handler, event, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": event_type,
                        "error": str(error),
                    }
                )
                continue

            if event_type in _SESSION_BEFORE_EVENTS and handler_result:
                result = handler_result
                if _is_cancelled(handler_result):
                    return result

        return result

    def emit_resources_discover(self, cwd: str, reason: str) -> dict[str, list[dict[str, str]]]:
        discovered = {"skillPaths": [], "promptPaths": [], "themePaths": []}
        context = self.create_context()
        for handler in list(self._handlers.get("resources_discover", [])):
            try:
                result = _call_extension_handler(
                    handler,
                    {"type": "resources_discover", "cwd": cwd, "reason": reason},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "resources_discover",
                        "error": str(error),
                    }
                )
                continue
            if not isinstance(result, dict):
                continue
            for result_key, output_key in (
                ("skillPaths", "skillPaths"),
                ("promptPaths", "promptPaths"),
                ("themePaths", "themePaths"),
            ):
                values = result.get(result_key)
                if not isinstance(values, list):
                    continue
                discovered[output_key].extend(
                    {"path": path, "extensionPath": "<python-extension>"}
                    for path in values
                    if isinstance(path, str)
                )
        return discovered

    emitResourcesDiscover = emit_resources_discover

    def emit_user_bash(self, event: ExtensionEvent) -> object:
        context = self.create_context()
        for handler in list(self._handlers.get("user_bash", [])):
            try:
                result = _call_extension_handler(handler, event, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "user_bash",
                        "error": str(error),
                    }
                )
                continue
            if result:
                return result
        return None

    emitUserBash = emit_user_bash

    def emit_input(
        self,
        text: str,
        images: list[object] | None = None,
        source: str = "interactive",
        streaming_behavior: str | None = None,
    ) -> dict[str, object]:
        current_text = text
        current_images = images
        context = self.create_context()
        for handler in list(self._handlers.get("input", [])):
            try:
                event = {
                    "type": "input",
                    "text": current_text,
                    "images": current_images,
                    "source": source,
                }
                if streaming_behavior is not None:
                    event["streamingBehavior"] = streaming_behavior
                result = _call_extension_handler(handler, event, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "input",
                        "error": str(error),
                    }
                )
                continue
            if not isinstance(result, dict):
                continue
            action = result.get("action")
            if action == "handled":
                return {"action": "handled"}
            if action == "transform":
                current_text = str(result.get("text", current_text))
                if "images" in result:
                    current_images = result.get("images")
        return {"action": "transform", "text": current_text, "images": current_images}

    emitInput = emit_input

    def emit_message_end(self, event: ExtensionEvent) -> object:
        current_message = event.get("message")
        modified = False
        context = self.create_context()
        for handler in list(self._handlers.get("message_end", [])):
            try:
                result = _call_extension_handler(handler, {**event, "message": current_message}, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "message_end",
                        "error": str(error),
                    }
                )
                continue
            if not isinstance(result, dict) or "message" not in result:
                continue
            replacement = result["message"]
            if getattr(replacement, "role", None) != getattr(current_message, "role", None):
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "message_end",
                        "error": "message_end handlers must return a message with the same role",
                    }
                )
                continue
            current_message = replacement
            modified = True
        return current_message if modified else None

    emitMessageEnd = emit_message_end

    def emit_tool_result(self, event: ExtensionEvent) -> dict[str, object] | None:
        current_event = dict(event)
        modified = False
        context = self.create_context()
        for handler in list(self._handlers.get("tool_result", [])):
            try:
                result = _call_extension_handler(handler, dict(current_event), context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "tool_result",
                        "error": str(error),
                    }
                )
                continue
            if not isinstance(result, dict):
                continue
            for key in ("content", "details", "isError"):
                if key in result:
                    current_event[key] = result[key]
                    modified = True
        if not modified:
            return None
        return {
            "content": current_event.get("content"),
            "details": current_event.get("details"),
            "isError": current_event.get("isError"),
        }

    emitToolResult = emit_tool_result

    def emit_tool_call(self, event: ExtensionEvent) -> dict[str, object] | None:
        result: dict[str, object] | None = None
        context = self.create_context()
        for handler in list(self._handlers.get("tool_call", [])):
            try:
                handler_result = _call_extension_handler(handler, event, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "tool_call",
                        "error": str(error),
                    }
                )
                continue
            if not isinstance(handler_result, dict):
                continue
            result = handler_result
            if handler_result.get("block") is True:
                return handler_result
        return result

    emitToolCall = emit_tool_call

    def emit_before_agent_start(
        self,
        prompt: str,
        images: list[object] | None,
        system_prompt: str,
        system_prompt_options: object | None = None,
    ) -> dict[str, object] | None:
        current_system_prompt = system_prompt
        messages: list[object] = []
        system_prompt_modified = False
        context = self.create_context()
        for handler in list(self._handlers.get("before_agent_start", [])):
            try:
                result = _call_extension_handler(
                    handler,
                    {
                        "type": "before_agent_start",
                        "prompt": prompt,
                        "images": images,
                        "systemPrompt": current_system_prompt,
                        "systemPromptOptions": system_prompt_options,
                    },
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "before_agent_start",
                        "error": str(error),
                    }
                )
                continue
            if not isinstance(result, dict):
                continue
            if "message" in result:
                messages.append(result["message"])
            if "systemPrompt" in result:
                current_system_prompt = str(result["systemPrompt"])
                system_prompt_modified = True
        if not messages and not system_prompt_modified:
            return None
        output: dict[str, object] = {}
        if messages:
            output["messages"] = messages
        if system_prompt_modified:
            output["systemPrompt"] = current_system_prompt
        return output

    emitBeforeAgentStart = emit_before_agent_start

    def emit_context(self, messages: list[object]) -> list[object]:
        current_messages = copy.deepcopy(list(messages))
        context = self.create_context()
        for handler in list(self._handlers.get("context", [])):
            try:
                result = _call_extension_handler(
                    handler,
                    {"type": "context", "messages": current_messages},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "context",
                        "error": str(error),
                    }
                )
                continue
            if isinstance(result, dict) and isinstance(result.get("messages"), list):
                current_messages = result["messages"]
        return current_messages

    emitContext = emit_context

    def emit_before_provider_request(self, payload: object) -> object:
        current_payload = payload
        context = self.create_context()
        for handler in list(self._handlers.get("before_provider_request", [])):
            try:
                result = _call_extension_handler(
                    handler,
                    {"type": "before_provider_request", "payload": current_payload},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self.emit_error(
                    {
                        "extensionPath": "<python-extension>",
                        "event": "before_provider_request",
                        "error": str(error),
                    }
                )
                continue
            if result is not None:
                current_payload = result
        return current_payload

    emitBeforeProviderRequest = emit_before_provider_request

    def register_tool(self, definition: ToolDefinition, source_info: SourceInfo | None = None) -> None:
        self._registered_tools[definition.name] = RegisteredTool(
            definition=definition,
            source_info=source_info
            or definition.source_info
            or create_synthetic_source_info(f"<extension:{definition.name}>", source="extension"),
        )

    registerTool = register_tool

    def unregister_tool(self, name: str) -> None:
        self._registered_tools.pop(name, None)

    unregisterTool = unregister_tool

    def clear_tools(self) -> None:
        self._registered_tools.clear()

    clearTools = clear_tools

    def get_all_registered_tools(self) -> list[RegisteredTool]:
        return list(self._registered_tools.values())

    getAllRegisteredTools = get_all_registered_tools

    def register_command(self, name: str, options: dict[str, object]) -> None:
        handler = options.get("handler")
        if not callable(handler):
            raise ValueError("Registered command requires a callable handler")
        self._registered_commands[name] = RegisteredCommand(
            name=name,
            description=str(options["description"]) if options.get("description") is not None else None,
            handler=handler,
            source_info=create_synthetic_source_info(f"<extension-command:{name}>", source="extension"),
            get_argument_completions=options.get("getArgumentCompletions")
            if callable(options.get("getArgumentCompletions"))
            else options.get("get_argument_completions")
            if callable(options.get("get_argument_completions"))
            else None,
        )

    registerCommand = register_command

    def unregister_command(self, name: str) -> None:
        self._registered_commands.pop(name, None)

    unregisterCommand = unregister_command

    def get_registered_command(self, name: str) -> RegisteredCommand | None:
        return self._registered_commands.get(name)

    getRegisteredCommand = get_registered_command

    def get_all_registered_commands(self) -> list[RegisteredCommand]:
        return list(self._registered_commands.values())

    getAllRegisteredCommands = get_all_registered_commands

    def register_flag(self, name: str, options: dict[str, object]) -> None:
        if name in self._registered_flags:
            return
        flag_type = str(options.get("type", "boolean"))
        flag = ExtensionFlag(
            name=name,
            type=flag_type,
            description=str(options["description"]) if options.get("description") is not None else None,
            default=options.get("default") if isinstance(options.get("default"), (bool, str)) else None,
        )
        self._registered_flags[name] = flag
        if flag.default is not None and name not in self._flag_values:
            self._flag_values[name] = flag.default

    registerFlag = register_flag

    def get_flags(self) -> dict[str, ExtensionFlag]:
        return dict(self._registered_flags)

    getFlags = get_flags

    def set_flag_value(self, name: str, value: bool | str) -> None:
        self._flag_values[name] = value

    setFlagValue = set_flag_value

    def get_flag_values(self) -> dict[str, bool | str]:
        return dict(self._flag_values)

    getFlagValues = get_flag_values

    def get_flag(self, name: str) -> bool | str | None:
        if name not in self._registered_flags:
            return None
        return self._flag_values.get(name)

    getFlag = get_flag

    def register_message_renderer(self, custom_type: str, renderer: Callable[..., object]) -> None:
        self._message_renderers[custom_type] = renderer

    registerMessageRenderer = register_message_renderer

    def get_message_renderer(self, custom_type: str) -> Callable[..., object] | None:
        return self._message_renderers.get(custom_type)

    getMessageRenderer = get_message_renderer

    def get_message_renderers(self) -> dict[str, Callable[..., object]]:
        return dict(self._message_renderers)

    getMessageRenderers = get_message_renderers

    def register_shortcut(self, shortcut: str, options: dict[str, object]) -> None:
        handler = options.get("handler")
        if not callable(handler):
            raise ValueError("Registered shortcut requires a callable handler")
        normalized = shortcut.lower()
        self._shortcuts[normalized] = ExtensionShortcut(
            key=normalized,
            handler=handler,
            description=str(options["description"]) if options.get("description") is not None else None,
        )

    registerShortcut = register_shortcut

    def get_shortcuts(self, resolved_keybindings: dict[str, object] | None = None) -> dict[str, ExtensionShortcut]:
        _ = resolved_keybindings
        return dict(self._shortcuts)

    getShortcuts = get_shortcuts


def _is_cancelled(result: object) -> bool:
    if isinstance(result, dict):
        return result.get("cancel") is True
    return getattr(result, "cancel", False) is True


def _callable_action(actions: object, *names: str) -> Callable[..., object] | None:
    for name in names:
        value: object
        if isinstance(actions, dict):
            value = actions.get(name)
        else:
            value = getattr(actions, name, None)
        if callable(value):
            return value
    return None


def _call_extension_handler(handler: ExtensionHandler, event: ExtensionEvent, context: ExtensionContextView) -> object:
    if _handler_accepts_context(handler):
        return handler(event, context)
    return handler(event)


def _handler_accepts_context(handler: Callable[..., object]) -> bool:
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return True
    positional = 0
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional += 1
    return positional >= 2


def emit_session_shutdown_event(extension_runner: ExtensionRunner, event: ExtensionEvent) -> bool:
    if extension_runner.has_handlers("session_shutdown"):
        extension_runner.emit(event)
        return True
    return False


emitSessionShutdownEvent = emit_session_shutdown_event
