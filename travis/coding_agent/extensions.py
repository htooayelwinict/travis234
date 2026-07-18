"""Extension runner subset ported from Travis coding-agent extension plumbing."""

from __future__ import annotations

from collections.abc import Callable
import copy
import inspect
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from travis.agent.async_utils import resolve, run_sync
from travis.coding_agent.event_bus import EventBusController, create_event_bus
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.tools.types import ToolDefinition, wrap_tool_definition

ExtensionEvent = dict[str, Any]
ExtensionHandler = Callable[[ExtensionEvent], object]
ExtensionErrorListener = Callable[[dict[str, object]], None]

PINNED_PI_EXTENSION_EVENTS = (
    "project_trust",
    "resources_discover",
    "session_start",
    "session_info_changed",
    "session_before_switch",
    "session_before_fork",
    "session_before_compact",
    "session_compact",
    "session_shutdown",
    "session_before_tree",
    "session_tree",
    "context",
    "before_provider_request",
    "before_provider_headers",
    "after_provider_response",
    "before_agent_start",
    "agent_start",
    "agent_end",
    "agent_settled",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "model_select",
    "thinking_level_select",
    "tool_call",
    "tool_result",
    "user_bash",
    "input",
)

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



@dataclass(frozen=True)
class RegisteredCommand:
    name: str
    registration_name: str
    description: str | None
    handler: Callable[..., object]
    source_info: SourceInfo
    get_argument_completions: Callable[[str], object] | None = None


@dataclass(frozen=True)
class RegisteredExtensionHandler:
    handler: ExtensionHandler
    extension_path: str




@dataclass(frozen=True)
class ExtensionFlag:
    name: str
    type: str
    description: str | None = None
    default: bool | str | None = None
    extension_path: str = "<python-extension>"


@dataclass(frozen=True)
class ExtensionFlagConflict:
    name: str
    first_extension_path: str
    conflicting_extension_path: str


class ExtensionFlagValidationError(ValueError):
    def __init__(self, diagnostics: list[dict[str, object]]) -> None:
        self.diagnostics = [dict(item) for item in diagnostics]
        super().__init__(
            "; ".join(str(item.get("message", "invalid extension flag")) for item in diagnostics)
        )


def apply_extension_flag_values(
    runtime: ExtensionRunner,
    raw_values: object,
) -> list[dict[str, object]]:
    if raw_values is None:
        return []
    if isinstance(raw_values, dict):
        items = list(raw_values.items())
    elif hasattr(raw_values, "items"):
        items = list(raw_values.items())
    else:
        try:
            items = list(raw_values)  # type: ignore[arg-type]
        except TypeError:
            items = []

    diagnostics: list[dict[str, object]] = []
    registered_flags = runtime.get_flags()
    unknown_flags: list[str] = []
    for name, value in items:
        flag_name = str(name)
        flag = registered_flags.get(flag_name)
        if flag is None:
            unknown_flags.append(flag_name)
            continue
        if flag.type == "boolean":
            runtime.set_flag_value(flag_name, True)
            continue
        if isinstance(value, str):
            runtime.set_flag_value(flag_name, value)
            continue
        diagnostics.append({"type": "error", "message": f'Extension flag "--{flag_name}" requires a value'})

    if unknown_flags:
        label = "option" if len(unknown_flags) == 1 else "options"
        names = ", ".join(f"--{name}" for name in unknown_flags)
        diagnostics.append({"type": "error", "message": f"Unknown {label}: {names}"})
    return diagnostics



@dataclass(frozen=True)
class ExtensionShortcut:
    key: str
    handler: Callable[..., object]
    description: str | None = None
    extension_path: str = "<python-extension>"



_STALE_CONTEXT_MESSAGE = (
    "This extension ctx is stale after session replacement or reload. Do not use a captured travis or command ctx "
    "after ctx.newSession(), ctx.fork(), ctx.switchSession(), or ctx.reload()."
)


def define_tool(tool: ToolDefinition) -> ToolDefinition:
    return tool




def wrap_registered_tool(registered_tool: RegisteredTool, runner: "ExtensionRunner"):
    tool = wrap_tool_definition(registered_tool.definition, lambda: runner.create_context())
    execute = tool.execute

    async def _execute(tool_call_id, args, signal=None, on_update=None):
        active_before = runner.get_active_tools()
        result = execute(tool_call_id, args, signal, on_update)
        if inspect.isawaitable(result):
            result = await result
        active_after = runner.get_active_tools()
        if not all(name in active_after for name in active_before):
            return result
        before_names = set(active_before)
        added_names = [name for name in active_after if name not in before_names]
        if not added_names:
            return result
        result.added_tool_names = list(dict.fromkeys([*(result.added_tool_names or []), *added_names]))
        return result

    tool.execute = _execute
    return tool


def wrap_registered_tools(registered_tools: list[RegisteredTool], runner: "ExtensionRunner") -> list:
    return [wrap_registered_tool(registered_tool, runner) for registered_tool in registered_tools]




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




class ExtensionContextView:
    """Lazy context passed to extension event handlers."""

    def __init__(
        self,
        runner: "ExtensionRunner",
        generation: int,
        system_prompt_getter: Callable[[], str] | None = None,
    ) -> None:
        self._runner = runner
        self._generation = generation
        self._system_prompt_getter = system_prompt_getter

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
    def cwd(self) -> str:
        self._assert_active()
        return self._runner._cwd

    @property
    def session_manager(self) -> object | None:
        self._assert_active()
        return self._runner._session_manager


    @property
    def model_registry(self) -> object | None:
        self._assert_active()
        return self._runner._model_registry


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


    def is_project_trusted(self) -> bool:
        self._assert_active()
        return bool(self._runner._is_project_trusted())


    def abort(self) -> object:
        self._assert_active()
        return self._runner._abort()

    def has_pending_messages(self) -> bool:
        self._assert_active()
        return bool(self._runner._has_pending_messages())


    def shutdown(self) -> object:
        self._assert_active()
        return self._runner._shutdown()

    def get_context_usage(self) -> object | None:
        self._assert_active()
        return self._runner._get_context_usage()


    def compact(self, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._compact(options)

    def get_system_prompt(self) -> str:
        self._assert_active()
        getter = self._system_prompt_getter or self._runner._get_system_prompt
        return str(getter())


    def spawn_subagent(self, role: str, goal: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._spawn_subagent(role, goal, options)


    def list_subagents(self) -> object:
        self._assert_active()
        return self._runner._list_subagents()


    def get_subagent_result(self, task_id: str) -> object | None:
        self._assert_active()
        return self._runner._get_subagent_result(task_id)


    def cancel_subagent(self, task_id: str, reason: str | None = None) -> object:
        self._assert_active()
        return self._runner._cancel_subagent(task_id, reason)



class ExtensionCommandContextView(ExtensionContextView):
    """command context with session-control actions."""

    def get_system_prompt_options(self) -> object:
        self._assert_active()
        return self._runner._get_system_prompt_options()


    def wait_for_idle(self) -> object:
        self._assert_active()
        return self._runner._wait_for_idle()


    def new_session(self, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._new_session(options)


    def fork(self, entry_id: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._fork(entry_id, options)

    def navigate_tree(self, target_id: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._navigate_tree(target_id, options)


    def switch_session(self, session_path: str, options: object | None = None) -> object:
        self._assert_active()
        return self._runner._switch_session(session_path, options)


    def reload(self) -> object:
        self._assert_active()
        return self._runner._reload()


class SourceScopedEventBus:
    """Generation-guarded view of the shared extension event bus."""

    def __init__(self, api: "SourceScopedExtensionAPI") -> None:
        self._api = api

    def __getattr__(self, name: str) -> object:
        self._api._assert_active()
        target = getattr(self._api._runner.events, name)
        if not callable(target):
            return target

        def delegated(*args: object, **kwargs: object) -> object:
            self._api._assert_active()
            owner_scope = getattr(self._api._runner.events, "owner", None)
            scope = (
                owner_scope(self._api._runner._event_bus_owner)
                if callable(owner_scope)
                else nullcontext()
            )
            with scope, self._api._runner.source_scope(self._api.extension_path):
                return target(*args, **kwargs)

        return delegated


class SourceScopedExtensionAPI:
    """Thin source and generation guard around the shared extension runner."""

    _CORE_ACTIONS = frozenset(
        {
            "send_message",
            "send_user_message",
            "append_entry",
            "set_session_name",
            "get_session_name",
            "set_label",
            "get_active_tools",
            "get_all_tools",
            "set_active_tools",
            "refresh_tools",
            "get_commands",
            "exec",
            "set_model",
            "get_thinking_level",
            "set_thinking_level",
            "spawn_subagent",
            "list_subagents",
            "get_subagent_result",
            "cancel_subagent",
            "abort",
            "shutdown",
        }
    )
    _COMMAND_ACTIONS = frozenset(
        {
            "wait_for_idle",
            "new_session",
            "fork",
            "navigate_tree",
            "switch_session",
            "reload",
        }
    )

    def __init__(self, runner: "ExtensionRunner", extension_path: str) -> None:
        self._runner = runner
        self.extension_path = extension_path
        self._generation = runner._context_generation
        self._events = SourceScopedEventBus(self)

    def _assert_active(self) -> None:
        if self._generation != self._runner._context_generation:
            raise RuntimeError(self._runner._stale_context_message)

    @property
    def events(self) -> SourceScopedEventBus:
        self._assert_active()
        return self._events

    def __getattr__(self, name: str) -> object:
        self._assert_active()
        target = getattr(self._runner, name)
        if not callable(target):
            return target

        def delegated(*args: object, **kwargs: object) -> object:
            self._assert_active()
            if name in self._CORE_ACTIONS and not self._runner._core_bound:
                raise RuntimeError(
                    f"Extension session action '{name}' is unavailable before the session is bound"
                )
            if name in self._COMMAND_ACTIONS and not self._runner._command_context_bound:
                raise RuntimeError(
                    f"Extension host action '{name}' is unavailable before the session host is bound"
                )
            with self._runner.source_scope(self.extension_path):
                return target(*args, **kwargs)

        return delegated


class ExtensionRunner:
    """Small extension runner matching the AgentSession-facing Travis runner API."""

    def __init__(
        self,
        cwd: str = "",
        session_manager: object | None = None,
        model_registry: object | None = None,
        event_bus: EventBusController | None = None,
    ) -> None:
        self._registered_tools: dict[str, RegisteredTool] = {}
        self._registered_commands: dict[str, RegisteredCommand] = {}
        self._registered_flags: dict[str, ExtensionFlag] = {}
        self._flag_conflicts: list[ExtensionFlagConflict] = []
        self._flag_values: dict[str, bool | str] = {}
        self._message_renderers: dict[str, Callable[..., object]] = {}
        self._shortcuts: dict[str, ExtensionShortcut] = {}
        self._reported_shortcut_conflicts: set[tuple[str, str]] = set()
        self._handlers: dict[str, list[RegisteredExtensionHandler]] = {}
        self._error_listeners: list[ExtensionErrorListener] = []
        self._pending_errors: list[dict[str, object]] = []
        self._pending_provider_registrations: list[tuple[str, dict[str, Any], str]] = []
        self._loading_extension_path: str | None = None
        self._source_path: ContextVar[str | None] = ContextVar(
            f"travis_extension_source_{id(self)}",
            default=None,
        )
        self._register_provider: Callable[[str, dict[str, Any]], None] | None = None
        self._unregister_provider: Callable[[str], None] | None = None
        self._ui_context: object | None = None
        self._has_ui = False
        self._mode = "print"
        self._core_bound = False
        self._command_context_bound = False
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
        self.events = event_bus or create_event_bus()
        self._event_bus_owner = object()
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
        self._exec: Callable[[str, list[str], dict[str, object] | None], dict[str, object]] = (
            lambda command, args, options=None: (_ for _ in ()).throw(
                RuntimeError("extension execution is unavailable before the session is bound")
            )
        )
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

    def create_extension_api(self, extension_path: str) -> SourceScopedExtensionAPI:
        return SourceScopedExtensionAPI(self, extension_path)

    @contextmanager
    def source_scope(self, extension_path: str):
        token = self._source_path.set(extension_path)
        try:
            yield
        finally:
            self._source_path.reset(token)

    def _current_extension_path(self, fallback: str = "<python-extension>") -> str:
        return self._source_path.get() or self._loading_extension_path or fallback

    @property
    def mode(self) -> str:
        return self._mode

    def set_ui_context(
        self,
        ui_context: object | None = None,
        mode: str = "print",
        *,
        has_ui: bool | None = None,
    ) -> None:
        self._ui_context = ui_context
        self._mode = mode
        self._has_ui = ui_context is not None if has_ui is None else bool(has_ui)


    def get_ui_context(self) -> object | None:
        return self._ui_context


    def has_ui(self) -> bool:
        return self._has_ui


    def bind_command_context(self, actions: object | None = None) -> None:
        self._command_context_bound = actions is not None
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


    def wait_for_idle(self) -> object:
        return self._wait_for_idle()


    def new_session(self, options: object | None = None) -> object:
        return self._new_session(options)


    def fork(self, entry_id: str, options: object | None = None) -> object:
        return self._fork(entry_id, options)

    def navigate_tree(self, target_id: str, options: object | None = None) -> object:
        return self._navigate_tree(target_id, options)


    def switch_session(self, session_path: str, options: object | None = None) -> object:
        return self._switch_session(session_path, options)


    def reload(self) -> object:
        return self._reload()

    def set_abort_handler(self, handler: Callable[[], object] | None = None) -> None:
        self._abort_handler = handler or (lambda: None)


    def abort(self) -> object:
        return self._abort_handler()

    def set_shutdown_handler(self, handler: Callable[[], object] | None = None) -> None:
        self._shutdown_handler = handler or (lambda: None)


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
        self._exec = _callable_action(actions, "exec") or self._exec
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
        self._core_bound = True


    def send_message(self, message: dict[str, Any], options: object | None = None) -> object:
        return self._send_message(message, options)


    def send_user_message(self, content: object, options: object | None = None) -> object:
        return self._send_user_message(content, options)


    def append_entry(self, custom_type: str, data: object | None = None) -> object:
        return self._append_entry(custom_type, data)


    def set_session_name(self, name: str | None) -> object:
        return self._set_session_name(name)


    def get_session_name(self) -> str | None:
        return self._get_session_name()


    def set_label(self, entry_id: str, label: str | None) -> object:
        return self._set_label(entry_id, label)


    def get_active_tools(self) -> list[str]:
        return list(self._get_active_tools())


    def get_all_tools(self) -> object:
        return self._get_all_tools()


    def set_active_tools(self, tool_names: list[str]) -> object:
        return self._set_active_tools(list(tool_names))


    def refresh_tools(self) -> object:
        return self._refresh_tools()


    def get_commands(self) -> object:
        return self._get_commands()


    def exec(
        self,
        command: str,
        args: list[str],
        options: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self._exec(command, list(args), dict(options) if options is not None else None)


    def set_model(self, model: object) -> object:
        return self._set_model(model)


    def get_thinking_level(self) -> object:
        return self._get_thinking_level()


    def set_thinking_level(self, level: object) -> object:
        return self._set_thinking_level(level)


    def spawn_subagent(self, role: str, goal: str, options: object | None = None) -> object:
        return self._spawn_subagent(role, goal, options)


    def list_subagents(self) -> object:
        return self._list_subagents()


    def get_subagent_result(self, task_id: str) -> object | None:
        return self._get_subagent_result(task_id)


    def cancel_subagent(self, task_id: str, reason: str | None = None) -> object:
        return self._cancel_subagent(task_id, reason)


    def create_context(
        self,
        system_prompt_getter: Callable[[], str] | None = None,
    ) -> ExtensionContextView:
        return ExtensionContextView(
            self,
            self._context_generation,
            system_prompt_getter=system_prompt_getter,
        )


    def create_command_context(self) -> ExtensionCommandContextView:
        return ExtensionCommandContextView(self, self._context_generation)


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
        for name, config, extension_path in pending:
            try:
                with self.source_scope(extension_path):
                    self.register_provider(name, config, extension_path)
            except Exception as error:  # noqa: BLE001 - one invalid extension provider stays isolated.
                self.emit_error(
                    {
                        "extensionPath": extension_path,
                        "event": "register_provider",
                        "error": str(error),
                    }
                )


    def register_provider(self, name: str, config: dict[str, Any], extension_path: str = "<python-extension>") -> None:
        if extension_path == "<python-extension>":
            extension_path = self._current_extension_path()
        if self._register_provider is None:
            self._pending_provider_registrations.append((name, dict(config), extension_path))
            return
        self._register_provider(name, dict(config))


    @property
    def pending_provider_registrations(self) -> list[tuple[str, dict[str, Any], str]]:
        return [(name, dict(config), extension_path) for name, config, extension_path in self._pending_provider_registrations]


    def clear_pending_provider_registrations(self) -> None:
        self._pending_provider_registrations.clear()


    def unregister_provider(self, name: str) -> None:
        if self._unregister_provider is None:
            self._pending_provider_registrations = [
                (provider_name, config, extension_path)
                for provider_name, config, extension_path in self._pending_provider_registrations
                if provider_name != name
            ]
            return
        self._unregister_provider(name)


    def on(self, event_type: str, handler: ExtensionHandler) -> Callable[[], None]:
        registration = RegisteredExtensionHandler(
            handler=handler,
            extension_path=self._current_extension_path(),
        )
        handlers = self._handlers.setdefault(event_type, [])
        handlers.append(registration)

        def unsubscribe() -> None:
            current_handlers = self._handlers.get(event_type)
            if not current_handlers:
                return
            try:
                current_handlers.remove(registration)
            except ValueError:
                return
            if not current_handlers:
                self._handlers.pop(event_type, None)

        return unsubscribe

    @staticmethod
    def supported_event_types() -> tuple[str, ...]:
        return PINNED_PI_EXTENSION_EVENTS

    def dispose(self) -> None:
        self.invalidate()
        clear_owner = getattr(self.events, "clear_owner", None)
        if callable(clear_owner):
            clear_owner(self._event_bus_owner)

    def on_error(self, listener: ExtensionErrorListener) -> Callable[[], None]:
        self._error_listeners.append(listener)
        if self._pending_errors:
            pending = list(self._pending_errors)
            self._pending_errors.clear()
            for error in pending:
                listener(dict(error))

        def unsubscribe() -> None:
            try:
                self._error_listeners.remove(listener)
            except ValueError:
                return

        return unsubscribe


    def emit_error(self, error: dict[str, object]) -> None:
        if not self._error_listeners:
            self._pending_errors.append(dict(error))
            return
        for listener in list(self._error_listeners):
            listener(error)

    def _emit_handler_error(
        self,
        registration: RegisteredExtensionHandler,
        event_type: str,
        error: object,
    ) -> None:
        self.emit_error(
            {
                "extensionPath": registration.extension_path,
                "event": event_type,
                "error": str(error),
            }
        )


    def has_handlers(self, event_type: str) -> bool:
        return bool(self._handlers.get(event_type))


    def emit(self, event: ExtensionEvent) -> object:
        return run_sync(self.async_emit(event))

    async def async_emit(self, event: ExtensionEvent) -> object:
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Extension event must include a string 'type'")

        context = self.create_context()
        result: object = None
        for registration in list(self._handlers.get(event_type, [])):
            try:
                handler_result = await _call_extension_handler_async(registration.handler, event, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, event_type, error)
                continue

            if event_type in _SESSION_BEFORE_EVENTS and handler_result:
                result = handler_result
                if _is_cancelled(handler_result):
                    return result

        return result

    async def async_emit_project_trust(
        self,
        event: ExtensionEvent,
        context: object,
    ) -> dict[str, object] | None:
        """Return the first decisive bootstrap-extension trust response."""

        for registration in list(self._handlers.get("project_trust", [])):
            try:
                result = await _call_extension_handler_async(registration.handler, event, context)
            except Exception as error:  # noqa: BLE001 - trust handlers fail closed and report diagnostics.
                self._emit_handler_error(registration, "project_trust", error)
                continue
            if not isinstance(result, dict):
                continue
            decision = result.get("trusted")
            if decision == "undecided":
                continue
            if decision in {"yes", "no"}:
                return result
        return None

    def emit_resources_discover(self, cwd: str, reason: str) -> dict[str, list[dict[str, str]]]:
        discovered = {"skillPaths": [], "promptPaths": [], "themePaths": []}
        context = self.create_context()
        for registration in list(self._handlers.get("resources_discover", [])):
            try:
                result = _call_extension_handler(
                    registration.handler,
                    {"type": "resources_discover", "cwd": cwd, "reason": reason},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, "resources_discover", error)
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
                    {"path": path, "extensionPath": registration.extension_path}
                    for path in values
                    if isinstance(path, str)
                )
        return discovered


    def emit_user_bash(self, event: ExtensionEvent) -> object:
        context = self.create_context()
        for registration in list(self._handlers.get("user_bash", [])):
            try:
                result = _call_extension_handler(registration.handler, event, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, "user_bash", error)
                continue
            if result:
                return result
        return None


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
        for registration in list(self._handlers.get("input", [])):
            try:
                event = {
                    "type": "input",
                    "text": current_text,
                    "images": current_images,
                    "source": source,
                }
                if streaming_behavior is not None:
                    event["streamingBehavior"] = streaming_behavior
                result = _call_extension_handler(registration.handler, event, context)
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, "input", error)
                continue
            if not isinstance(result, dict):
                continue
            action = result.get("action")
            if action == "handled":
                return {"action": "handled"}
            if action == "transform":
                current_text = str(result.get("text", current_text))
                if result.get("images") is not None:
                    current_images = result.get("images")
        if current_text != text or current_images is not images:
            return {"action": "transform", "text": current_text, "images": current_images}
        return {"action": "continue"}


    def emit_message_end(self, event: ExtensionEvent) -> object:
        return run_sync(self.async_emit_message_end(event))

    async def async_emit_message_end(self, event: ExtensionEvent) -> object:
        current_message = event.get("message")
        modified = False
        context = self.create_context()
        for registration in list(self._handlers.get("message_end", [])):
            try:
                result = await _call_extension_handler_async(
                    registration.handler,
                    {**event, "message": current_message},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, "message_end", error)
                continue
            if not isinstance(result, dict) or "message" not in result:
                continue
            replacement = result["message"]
            if getattr(replacement, "role", None) != getattr(current_message, "role", None):
                self.emit_error(
                    {
                        "extensionPath": registration.extension_path,
                        "event": "message_end",
                        "error": "message_end handlers must return a message with the same role",
                    }
                )
                continue
            current_message = replacement
            modified = True
        return current_message if modified else None


    def emit_tool_result(self, event: ExtensionEvent) -> dict[str, object] | None:
        return run_sync(self.async_emit_tool_result(event))

    async def async_emit_tool_result(self, event: ExtensionEvent) -> dict[str, object] | None:
        current_event = dict(event)
        modified = False
        context = self.create_context()
        for registration in list(self._handlers.get("tool_result", [])):
            try:
                result = await _call_extension_handler_async(
                    registration.handler,
                    dict(current_event),
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, "tool_result", error)
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


    def emit_tool_call(self, event: ExtensionEvent) -> dict[str, object] | None:
        return run_sync(self.async_emit_tool_call(event))

    async def async_emit_tool_call(self, event: ExtensionEvent) -> dict[str, object] | None:
        result: dict[str, object] | None = None
        context = self.create_context()
        for registration in list(self._handlers.get("tool_call", [])):
            handler_result = await _call_extension_handler_async(
                registration.handler,
                event,
                context,
            )
            if not isinstance(handler_result, dict):
                continue
            result = handler_result
            if handler_result.get("block") is True:
                return handler_result
        return result


    def emit_before_provider_headers(self, headers: dict[str, object]) -> dict[str, object]:
        return run_sync(self.async_emit_before_provider_headers(headers))

    async def async_emit_before_provider_headers(self, headers: dict[str, object]) -> dict[str, object]:
        context = self.create_context()
        for registration in list(self._handlers.get("before_provider_headers", [])):
            try:
                # The shared object is the contract. Handler return values do
                # not replace it; assigning None requests header deletion.
                await _call_extension_handler_async(
                    registration.handler,
                    {"type": "before_provider_headers", "headers": headers},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - header observers are fail-open.
                self._emit_handler_error(registration, "before_provider_headers", error)
        return headers


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
        context = self.create_context(lambda: current_system_prompt)
        for registration in list(self._handlers.get("before_agent_start", [])):
            try:
                result = _call_extension_handler(
                    registration.handler,
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
                self._emit_handler_error(registration, "before_agent_start", error)
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


    def emit_context(self, messages: list[object]) -> list[object]:
        return run_sync(self.async_emit_context(messages))

    async def async_emit_context(self, messages: list[object]) -> list[object]:
        current_messages = copy.deepcopy(list(messages))
        context = self.create_context()
        for registration in list(self._handlers.get("context", [])):
            try:
                result = await _call_extension_handler_async(
                    registration.handler,
                    {"type": "context", "messages": current_messages},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, "context", error)
                continue
            if isinstance(result, dict) and isinstance(result.get("messages"), list):
                current_messages = result["messages"]
        return current_messages


    def emit_before_provider_request(self, payload: object) -> object:
        return run_sync(self.async_emit_before_provider_request(payload))

    async def async_emit_before_provider_request(self, payload: object) -> object:
        current_payload = payload
        context = self.create_context()
        for registration in list(self._handlers.get("before_provider_request", [])):
            try:
                result = await _call_extension_handler_async(
                    registration.handler,
                    {"type": "before_provider_request", "payload": current_payload},
                    context,
                )
            except Exception as error:  # noqa: BLE001 - preserves the established extension error forwarding.
                self._emit_handler_error(registration, "before_provider_request", error)
                continue
            if result is not None:
                current_payload = result
        return current_payload


    def register_tool(self, definition: ToolDefinition, source_info: SourceInfo | None = None) -> None:
        extension_path = self._current_extension_path(f"<extension:{definition.name}>")
        self._registered_tools[definition.name] = RegisteredTool(
            definition=definition,
            source_info=source_info
            or definition.source_info
            or create_synthetic_source_info(extension_path, source="extension"),
        )
        self._refresh_tools()


    def unregister_tool(self, name: str) -> None:
        if self._registered_tools.pop(name, None) is not None:
            self._refresh_tools()


    def clear_tools(self) -> None:
        self._registered_tools.clear()


    def get_all_registered_tools(self) -> list[RegisteredTool]:
        return list(self._registered_tools.values())


    def register_command(self, name: str, options: dict[str, object]) -> None:
        handler = options.get("handler")
        if not callable(handler):
            raise ValueError("Registered command requires a callable handler")
        invocation_name = name
        suffix = 1
        while invocation_name in self._registered_commands:
            invocation_name = f"{name}:{suffix}"
            suffix += 1
        source_info = options.get("sourceInfo", options.get("source_info"))
        self._registered_commands[invocation_name] = RegisteredCommand(
            name=invocation_name,
            registration_name=name,
            description=str(options["description"]) if options.get("description") is not None else None,
            handler=handler,
            source_info=source_info
            if isinstance(source_info, SourceInfo)
            else create_synthetic_source_info(
                self._current_extension_path(f"<extension-command:{invocation_name}>"),
                source="extension",
            ),
            get_argument_completions=options.get("getArgumentCompletions")
            if callable(options.get("getArgumentCompletions"))
            else options.get("get_argument_completions")
            if callable(options.get("get_argument_completions"))
            else None,
        )


    def unregister_command(self, name: str) -> None:
        self._registered_commands.pop(name, None)


    def get_registered_command(self, name: str) -> RegisteredCommand | None:
        return self._registered_commands.get(name)


    def get_all_registered_commands(self) -> list[RegisteredCommand]:
        return list(self._registered_commands.values())


    def register_flag(self, name: str, options: dict[str, object]) -> None:
        existing = self._registered_flags.get(name)
        if existing is not None:
            conflicting_path = self._current_extension_path()
            if existing.extension_path != conflicting_path:
                conflict = ExtensionFlagConflict(name, existing.extension_path, conflicting_path)
                if conflict not in self._flag_conflicts:
                    self._flag_conflicts.append(conflict)
            return
        flag_type = str(options.get("type", "boolean"))
        flag = ExtensionFlag(
            name=name,
            type=flag_type,
            description=str(options["description"]) if options.get("description") is not None else None,
            default=options.get("default") if isinstance(options.get("default"), (bool, str)) else None,
            extension_path=self._current_extension_path(),
        )
        self._registered_flags[name] = flag
        if flag.default is not None and name not in self._flag_values:
            self._flag_values[name] = flag.default


    def get_flags(self) -> dict[str, ExtensionFlag]:
        return dict(self._registered_flags)


    def get_flag_conflicts(self) -> list[ExtensionFlagConflict]:
        return list(self._flag_conflicts)


    def set_flag_value(self, name: str, value: bool | str) -> None:
        self._flag_values[name] = value


    def get_flag_values(self) -> dict[str, bool | str]:
        return dict(self._flag_values)


    def get_flag(self, name: str) -> bool | str | None:
        if name not in self._registered_flags:
            return None
        return self._flag_values.get(name)


    def register_message_renderer(self, custom_type: str, renderer: Callable[..., object]) -> None:
        self._message_renderers[custom_type] = renderer


    def get_message_renderer(self, custom_type: str) -> Callable[..., object] | None:
        return self._message_renderers.get(custom_type)


    def get_message_renderers(self) -> dict[str, Callable[..., object]]:
        return dict(self._message_renderers)


    def register_shortcut(self, shortcut: str, options: dict[str, object]) -> None:
        handler = options.get("handler")
        if not callable(handler):
            raise ValueError("Registered shortcut requires a callable handler")
        normalized = shortcut.lower()
        self._reported_shortcut_conflicts = {
            conflict
            for conflict in self._reported_shortcut_conflicts
            if conflict[0] != normalized
        }
        self._shortcuts[normalized] = ExtensionShortcut(
            key=normalized,
            handler=handler,
            description=str(options["description"]) if options.get("description") is not None else None,
            extension_path=self._current_extension_path(),
        )


    def get_shortcuts(self, resolved_keybindings: dict[str, object] | None = None) -> dict[str, ExtensionShortcut]:
        if not resolved_keybindings:
            return dict(self._shortcuts)

        protected: dict[str, list[str]] = {}
        for binding, value in resolved_keybindings.items():
            keys = value if isinstance(value, list) else [value]
            for key in keys:
                normalized = str(key).lower()
                if normalized:
                    protected.setdefault(normalized, []).append(str(binding))

        available: dict[str, ExtensionShortcut] = {}
        for key, shortcut in self._shortcuts.items():
            bindings = protected.get(key)
            if not bindings:
                available[key] = shortcut
                continue
            conflict = (key, shortcut.extension_path)
            if conflict not in self._reported_shortcut_conflicts:
                self._reported_shortcut_conflicts.add(conflict)
                self.emit_error(
                    {
                        "extensionPath": shortcut.extension_path,
                        "event": "shortcut_conflict",
                        "error": (
                            f"Shortcut {key} conflicts with protected keybinding "
                            f"{', '.join(bindings)} and was ignored"
                        ),
                    }
                )
        return available



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
    return run_sync(_call_extension_handler_async(handler, event, context))


async def _call_extension_handler_async(
    handler: ExtensionHandler,
    event: ExtensionEvent,
    context: ExtensionContextView,
) -> object:
    if _handler_accepts_context(handler):
        result = handler(event, context)
    else:
        result = handler(event)
    if inspect.isawaitable(result):
        return await resolve(result)
    return result


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
