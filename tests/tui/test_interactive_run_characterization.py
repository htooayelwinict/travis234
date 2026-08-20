from __future__ import annotations

import queue
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pytest

import travis.tui.interactive_command_dispatcher as command_dispatcher
from travis.controller_ports import (
    ControllerBinding,
    compose_controller_dependencies,
    install_controller_dependency_attributes,
)
from travis.tui.components import Editor, StatusLine, Text
from travis.tui.interactive_command_dispatcher import InteractiveCommandDispatcher
from travis.tui.interactive_dependencies import (
    InteractiveCommandDispatchDependencies,
    InteractiveRuntimeBindings,
)
from travis.tui.interactive_services import InteractiveServices
from travis.tui.interactive_shutdown import SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState
from travis.tui.motion import MotionState


Event = tuple[object, ...]


class _History:
    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.components: list[object] = []

    def add(self, component: object) -> None:
        self.components.append(component)
        self.events.append(("history:add", component))

    def clear(self) -> None:
        self.components.clear()
        self.events.append(("history:clear",))


class _Status:
    def __init__(self, events: list[Event]) -> None:
        self.events = events

    def set_message(self, message: str) -> None:
        self.events.append(("status", message))

    def set_visible(self, visible: bool) -> None:
        self.events.append(("status:visible", visible))

    def set_indicator(self, indicator: str | None, *, position: str = "suffix") -> None:
        self.events.append(("status:indicator", indicator, position))


class _Tui:
    def __init__(self, events: list[Event]) -> None:
        self.events = events

    def add(self, component: object) -> None:
        self.events.append(("tui:add", component))

    def post(self, callback: Callable[[], None]) -> None:
        self.events.append(("tui:post",))
        callback()

    def request_render(self, force: bool = False) -> None:
        self.events.append(("render", force))

    def set_focus(self, component: object | None) -> None:
        self.events.append(("focus", component))

    def scroll_to_bottom(self) -> None:
        self.events.append(("scroll",))

    def drain_dispatcher(self) -> None:
        self.events.append(("drain",))

    def stop(self) -> None:
        self.events.append(("tui:stop",))


class _Container:
    def __init__(self, events: list[Event]) -> None:
        self.events = events

    def add(self, component: object) -> None:
        self.events.append(("editor:add", component))

    def remove(self, component: object) -> None:
        self.events.append(("editor:remove", component))


class _Motion:
    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.enabled = True

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.events.append(("motion:enabled", enabled))

    def stop(self) -> None:
        self.events.append(("motion:stop",))


class _Disposable:
    def __init__(self, events: list[Event], name: str) -> None:
        self.events = events
        self.name = name

    def dispose(self) -> None:
        self.events.append((f"{self.name}:dispose",))


class _Closable:
    def __init__(self, events: list[Event], name: str) -> None:
        self.events = events
        self.name = name

    def close(self, *, timeout: float | None = None) -> None:
        self.events.append((f"{self.name}:close", timeout))


class _EventTrace:
    def __init__(self, events: list[Event]) -> None:
        self.events = events

    def write(self, event: str, payload: dict[str, str]) -> None:
        self.events.append(("trace", event, payload))


class _Compressor:
    compression_count = 7


class _Compaction:
    compressor = _Compressor()


class _App:
    def __init__(self, events: list[Event], *, trace: bool = True) -> None:
        self.compaction = _Compaction()
        self.messages: list[object] = ["one", "two"]
        self.event_trace = _EventTrace(events) if trace else None


class _Sessions:
    @property
    def session(self) -> object:
        return self


class _OwnerThread:
    def is_owner_thread(self) -> bool:
        return True

    def post(self, callback: Callable[[], None]) -> None:
        callback()

    def call_later(self, delay: float, callback: Callable[[], None]) -> object:
        return (delay, callback)


class _TerminalInput:
    def read(self, prompt: str) -> str:
        return prompt

    def select(self, title: str, choices: Sequence[str]) -> str | None:
        return choices[0] if choices else None


class _Theme:
    def role(self, name: str) -> object:
        return name


class _ObservedEditor(Editor):
    def __init__(
        self,
        events: list[Event],
        value: str,
        *,
        prompt: str,
        on_submit: Callable[[str], None] | None,
        theme_context: object | None,
    ) -> None:
        self._events = events
        self._record_handlers = False
        super().__init__(
            value=value,
            prompt=prompt,
            on_submit=on_submit,
            theme_context=theme_context,
        )
        self._record_handlers = True
        self._events.append(("editor:create", value, prompt, theme_context))

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"on_escape", "on_extension_shortcut"} and getattr(self, "_record_handlers", False):
            self._events.append((f"editor:{name}", value))
        super().__setattr__(name, value)

    def set_history(self, history: list[str]) -> None:
        self._events.append(("editor:history", history))
        super().set_history(history)

    def set_autocomplete_provider(self, provider: object | None) -> None:
        self._events.append(("editor:autocomplete", provider))
        super().set_autocomplete_provider(provider)

    def handle_input(self, data: str) -> None:
        self._events.append(("editor:input", data))
        super().handle_input(data)

    def add_to_history(self, text: str) -> None:
        self._events.append(("editor:add-history", text))
        super().add_to_history(text)


def _set_binding(bindings: InteractiveRuntimeBindings, name: str, value: object) -> None:
    binding = getattr(bindings, name)
    if not isinstance(binding, ControllerBinding):
        raise TypeError(f"interactive test dependency is not a binding: {name}")
    binding.set(value)


def _set_dispatcher_binding(
    dispatcher: InteractiveCommandDispatcher,
    name: str,
    value: object,
) -> None:
    binding = getattr(dispatcher.dependencies, name)
    if not isinstance(binding, ControllerBinding):
        raise TypeError(f"dispatcher test dependency is not a binding: {name}")
    binding.set(value)


@dataclass(slots=True)
class _Harness:
    dispatcher: InteractiveCommandDispatcher
    events: list[Event]
    history: _History
    tui: _Tui
    editors: list[_ObservedEditor]

    def run(self) -> int:
        return self.dispatcher.run()


def _make_harness(
    monkeypatch: pytest.MonkeyPatch,
    prompts: list[str | None],
    *,
    line_input: bool = False,
    editor_text: str = "draft",
    open_resume_picker: bool = False,
    wait_results: tuple[bool, ...] = (True,),
    trace: bool = True,
) -> _Harness:
    events: list[Event] = []
    editors: list[_ObservedEditor] = []
    prompt_values = deque(prompts)
    waits = deque(wait_results)
    bindings = InteractiveRuntimeBindings()
    tui = _Tui(events)
    history = _History(events)
    status = _Status(events)
    motion = _Motion(events)
    container = _Container(events)
    app = _App(events, trace=trace)
    autocomplete = object()
    theme = object()
    escape = lambda: events.append(("escape",))
    shortcut = lambda value: events.append(("shortcut", value)) or False

    def editor_factory(
        value: str,
        *,
        prompt: str,
        on_submit: Callable[[str], None] | None,
        theme_context: object | None,
    ) -> _ObservedEditor:
        editor = _ObservedEditor(
            events,
            value,
            prompt=prompt,
            on_submit=on_submit,
            theme_context=theme_context,
        )
        editors.append(editor)
        return editor

    def read_tui(_submitted: queue.Queue[str]) -> str | None:
        events.append(("read:tui",))
        return prompt_values.popleft() if prompt_values else None

    def read_line() -> str:
        events.append(("read:line",))
        if not prompt_values or prompt_values[0] is None:
            if prompt_values:
                prompt_values.popleft()
            raise EOFError
        value = prompt_values.popleft()
        if value is None:
            raise EOFError
        return value

    def wait_for_turn() -> bool:
        result = waits.popleft() if waits else True
        events.append(("wait", result))
        return result

    defaults: dict[str, object] = {
        "_abort_active_turn_for_shutdown": lambda: events.append(("abort",)),
        "_dispatch_extension_command": lambda prompt: events.append(("extension", prompt)) or False,
        "_dispatch_extension_shortcut": shortcut,
        "_dispatch_terminal_input": lambda prompt: events.append(("terminal", prompt)) or (False, prompt),
        "_extension_commands": None,
        "_extension_host": None,
        "_handle_active_turn_prompt": lambda prompt: events.append(("active", prompt)) or False,
        "_handle_editor_escape": escape,
        "_install_sigint_handler": lambda: events.append(("sigint:install",)) or "previous",
        "_is_registered_extension_command": lambda prompt: events.append(("registered:extension", prompt)) or False,
        "_is_registered_prompt_template": lambda prompt: events.append(("registered:template", prompt)) or False,
        "_is_turn_active": lambda: events.append(("turn:active",)) or False,
        "_line_input_mode": line_input,
        "_open_resume_picker": open_resume_picker,
        "_read_prompt_from_line_input": read_line,
        "_read_prompt_from_tui": read_tui,
        "_refresh_footer": lambda: events.append(("footer",)),
        "_restore_sigint_handler": lambda previous: events.append(("sigint:restore", previous)),
        "_run_agents_command": lambda parsed: events.append(("command:agents", parsed)),
        "_run_auth_command": lambda action, argument: events.append(("command:auth", action, argument)),
        "_run_bash_command": lambda command, *, exclude_from_context: events.append(
            ("command:bash", command, exclude_from_context)
        ),
        "_run_clone_command": lambda: events.append(("command:clone",)),
        "_run_copy_command": lambda: events.append(("command:copy",)),
        "_run_export_command": lambda prompt: events.append(("command:export", prompt)),
        "_run_fork_command": lambda: events.append(("command:fork",)),
        "_run_help_command": lambda: events.append(("command:help",)),
        "_run_import_command": lambda prompt: events.append(("command:import", prompt)),
        "_run_loop_active": False,
        "_run_lsp_status_command": lambda: events.append(("command:lsp",)),
        "_run_manual_compress": lambda prompt: events.append(("command:compact", prompt)),
        "_run_memory_status_command": lambda: events.append(("command:memory",)),
        "_run_model_command": lambda action, argument: events.append(("command:model", action, argument)),
        "_run_name_command": lambda prompt: events.append(("command:name", prompt)),
        "_run_new_session_command": lambda: events.append(("command:new",)),
        "_run_operations_command": lambda operation_id: events.append(("command:operations", operation_id)),
        "_run_package_command": lambda prompt: events.append(("command:package", prompt)) or False,
        "_run_params_command": lambda query: events.append(("command:params", query)),
        "_run_processes_command": lambda: events.append(("command:processes",)),
        "_run_reload_command": lambda: events.append(("command:reload",)),
        "_run_resume_command": lambda *, startup=False: events.append(("command:resume", startup)) or True,
        "_run_session_info_command": lambda: events.append(("command:session",)),
        "_run_share_command": lambda: events.append(("command:share",)),
        "_run_theme_command": lambda prompt: events.append(("command:theme", prompt)),
        "_run_tree_command": lambda: events.append(("command:tree",)),
        "_run_trust_command": lambda: events.append(("command:trust",)),
        "_run_unknown_command": lambda prompt: events.append(("command:unknown", prompt)),
        "_session_commands": None,
        "_set_motion_signal": lambda owner, state: events.append(("motion", owner, state)),
        "_shutdown_requested": False,
        "_shutdown_subagent_ui": lambda: events.append(("subagents:shutdown",)),
        "_start_turn_thread": lambda prompt, compressions, tokens: events.append(
            ("turn:start", prompt, compressions, tokens)
        ),
        "_unsubscribe_app_session_rebound": None,
        "_unsubscribe_footer_branch_change": None,
        "_unsubscribe_process_events": None,
        "_unsubscribe_session_events": None,
        "_unsubscribe_tui_scroll_change": None,
        "_unsubscribe_tui_terminal_input": None,
        "_user_commands": None,
        "_wait_for_active_turn": wait_for_turn,
        "active_editor": None,
        "app": app,
        "autocomplete_provider": autocomplete,
        "editor_container": container,
        "editor_text": editor_text,
        "footer_data_provider": _Disposable(events, "footer-provider"),
        "history": history,
        "init": lambda: events.append(("init",)),
        "motion_controller": motion,
        "prompt_history": ["older"],
        "prompt_label": "travis> ",
        "status": status,
        "theme_context": theme,
        "tui": tui,
    }
    for name, value in defaults.items():
        _set_binding(bindings, name, value)

    services = InteractiveServices(
        render=tui,
        status=status,
        history=history,
        sessions=_Sessions(),
        owner_thread=_OwnerThread(),
        terminal_input=_TerminalInput(),
        theme=_Theme(),
    )
    dependencies = compose_controller_dependencies(
        InteractiveCommandDispatchDependencies,
        bindings,
        state=InteractiveState(),
        lifecycle=InteractiveLifecycleState(),
        services=services,
    )
    install_controller_dependency_attributes(
        InteractiveCommandDispatcher,
        InteractiveCommandDispatchDependencies,
    )
    monkeypatch.setattr(command_dispatcher, "Editor", editor_factory)
    monkeypatch.setattr(
        command_dispatcher,
        "user_message_to_component",
        lambda prompt: Text(f"user:{prompt}"),
    )
    monkeypatch.setattr(
        command_dispatcher,
        "estimate_tokens",
        lambda messages: events.append(("tokens", tuple(messages))) or 19,
    )
    return _Harness(
        dispatcher=InteractiveCommandDispatcher(dependencies),
        events=events,
        history=history,
        tui=tui,
        editors=editors,
    )


def _event_names(events: list[Event]) -> list[object]:
    return [event[0] for event in events]


def test_run_initializes_resume_and_editor_in_observable_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(
        monkeypatch,
        [None],
        open_resume_picker=True,
    )

    assert harness.run() == 0

    names = _event_names(harness.events)
    assert names[:5] == [
        "init",
        "sigint:install",
        "command:resume",
        "editor:create",
        "editor:history",
    ]
    escape_events = [event for event in harness.events if event[0] == "editor:on_escape"]
    assert escape_events
    assert all(event[1] is harness.dispatcher._handle_editor_escape for event in escape_events)
    shortcut_index = names.index("editor:on_extension_shortcut")
    assert names[shortcut_index + 1 : shortcut_index + 6] == [
        "editor:autocomplete",
        "editor:add",
        "focus",
        "render",
        "read:tui",
    ]
    editor = harness.editors[0]
    assert editor.get_value() == "draft"
    assert editor.prompt == "travis> "
    escape_handler = editor.on_escape
    assert escape_handler is not None
    assert escape_handler is harness.dispatcher._handle_editor_escape
    assert editor.on_extension_shortcut is harness.dispatcher._dispatch_extension_shortcut
    escape_count = harness.events.count(("escape",))
    escape_handler()
    assert harness.events.count(("escape",)) == escape_count + 1
    assert harness.dispatcher._open_resume_picker is False
    assert harness.dispatcher.active_editor is editor


def test_startup_resume_failure_exits_before_editor_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, [], open_resume_picker=True)
    harness.dispatcher._run_resume_command = lambda *, startup=False: (
        harness.events.append(("command:resume", startup)) or False
    )

    assert harness.run() == 0

    assert ("command:resume", True) in harness.events
    assert "editor:create" not in _event_names(harness.events)


def test_line_input_eof_preserves_the_attached_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, [None], line_input=True)

    assert harness.run() == 0

    editor = harness.editors[0]
    assert harness.dispatcher.active_editor is editor
    assert ("editor:on_extension_shortcut", harness.dispatcher._dispatch_extension_shortcut) not in harness.events
    assert ("editor:remove", editor) not in harness.events


def test_terminal_interception_preserves_editor_text_for_next_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, ["intercept", None], line_input=True)

    def dispatch(prompt: str) -> tuple[bool, str]:
        harness.events.append(("terminal", prompt))
        return (True, "ignored") if prompt == "intercept" else (False, prompt)

    harness.dispatcher._dispatch_terminal_input = dispatch

    assert harness.run() == 0

    first, second = harness.editors
    assert first.get_value() == "draft"
    assert second.get_value() == "draft"
    first_remove = harness.events.index(("editor:remove", first))
    second_create = harness.events.index(
        ("editor:create", "draft", "travis> ", harness.dispatcher.theme_context),
        first_remove + 1,
    )
    assert first_remove < second_create
    assert harness.dispatcher.editor_text == "draft"


@pytest.mark.parametrize("exit_prompt", ["/exit", "/quit", "exit", "quit"])
def test_exit_spellings_teardown_history_and_termination_order(
    monkeypatch: pytest.MonkeyPatch,
    exit_prompt: str,
) -> None:
    harness = _make_harness(monkeypatch, [f"  {exit_prompt}  "])

    assert harness.run() == 0

    editor = harness.editors[0]
    component = harness.history.components[0]
    assert isinstance(component, Text)
    assert component.text == f"user:{exit_prompt}"
    expected = [
        harness.events.index(("focus", None)),
        harness.events.index(("editor:remove", editor)),
        harness.events.index(("scroll",)),
        harness.events.index(("history:add", component)),
        harness.events.index(("render", False), harness.events.index(("history:add", component))),
        harness.events.index(("motion", "termination", MotionState.TERMINATING)),
        harness.events.index(("status", "Exiting")),
        harness.events.index(("footer",)),
    ]
    assert expected == sorted(expected)
    assert harness.dispatcher._shutdown_requested is True


def test_empty_prompt_records_blank_line_without_command_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, ["   ", "/exit"])

    assert harness.run() == 0

    blank = harness.history.components[0]
    assert isinstance(blank, Text)
    assert blank.text == ""
    assert all(event != ("editor:add-history", "") for event in harness.events)


def test_submitted_prompt_callback_precedes_teardown_history_and_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, [], editor_text="")
    submitted_values = deque(["  submitted prompt  ", "/exit"])

    def read_submitted(submitted: queue.Queue[str]) -> str:
        editor = harness.editors[-1]
        callback = editor.on_submit
        assert callback is not None
        callback(submitted_values.popleft())
        harness.events.append(("submitted:queued",))
        return submitted.get_nowait()

    harness.dispatcher._read_prompt_from_tui = read_submitted

    assert harness.run() == 0

    first_editor = harness.editors[0]
    component = harness.history.components[0]
    assert isinstance(component, Text)
    assert component.text == "user:submitted prompt"
    ordered = [
        harness.events.index(("submitted:queued",)),
        harness.events.index(("focus", None)),
        harness.events.index(("editor:remove", first_editor)),
        harness.events.index(("scroll",)),
        harness.events.index(("history:add", component)),
        harness.events.index(("render", False), harness.events.index(("history:add", component))),
        harness.events.index(("editor:add-history", "submitted prompt")),
    ]
    assert ordered == sorted(ordered)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("/motion off", ("motion:enabled", False)),
        ("/help details", ("command:help",)),
        ("/resume", ("command:resume", False)),
        ("/new", ("command:new",)),
        ("/session", ("command:session",)),
        ("/name chosen", ("command:name", "/name chosen")),
        ("/fork", ("command:fork",)),
        ("/clone", ("command:clone",)),
        ("/tree", ("command:tree",)),
        ("/export out.jsonl", ("command:export", "/export out.jsonl")),
        ("/import in.jsonl", ("command:import", "/import in.jsonl")),
        ("/copy", ("command:copy",)),
        ("/share", ("command:share",)),
        ("/theme Signal Glass", ("command:theme", "/theme Signal Glass")),
        ("/memory status", ("command:memory",)),
        ("/operations", ("command:operations", None)),
        ("/operations op_123", ("command:operations", "op_123")),
        ("/processes", ("command:processes",)),
        ("/lsp status", ("command:lsp",)),
        ("/agents", ("command:agents", ("status", ()))),
        ("/agents inspect child", ("command:agents", ("inspect", ("child",)))),
        ("/agents cancel child", ("command:agents", ("cancel", ("child",)))),
        ("/agents steer child do this", ("command:agents", ("steer", ("child", "do this")))),
        ("/agents broken", ("command:agents", ("invalid", ()))),
        ("/reload", ("command:reload",)),
        ("/trust", ("command:trust",)),
        ("! pwd", ("command:bash", "pwd", False)),
        ("!! pwd", ("command:bash", "pwd", True)),
        ("/compact now", ("command:compact", "/compact now")),
        ("/compress", ("command:compact", "/compress")),
        ("/login", ("command:auth", "login", None)),
        ("/logout", ("command:auth", "logout", None)),
        ("/models", ("command:model", "list", None)),
        ("/model", ("command:model", "select", None)),
        ("/model provider/name", ("command:model", "select", "provider/name")),
        ("/params", ("command:params", "")),
        ("/params temperature 0.2", ("command:params", "temperature 0.2")),
    ],
)
def test_builtin_dispatch_preserves_exact_handler_arguments(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    expected: Event,
) -> None:
    harness = _make_harness(monkeypatch, [prompt, "/exit"])

    assert harness.run() == 0

    assert expected in harness.events
    if expected[0] in {"command:bash", "command:compact", "command:auth", "command:model", "command:params"}:
        assert harness.events.index(("command:package", prompt)) < harness.events.index(expected)
    else:
        assert ("command:package", prompt) not in harness.events


def test_package_dispatch_keeps_its_precedence_before_bash_and_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, ["! owned", "/exit"])
    harness.dispatcher._run_package_command = lambda prompt: harness.events.append(("command:package", prompt)) or True

    assert harness.run() == 0

    assert ("command:package", "! owned") in harness.events
    assert ("command:bash", "owned", False) not in harness.events
    assert ("extension", "! owned") not in harness.events


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        ("/motion sideways", "Usage: /motion [on|off]"),
        ("/memory private", "Usage: /memory status"),
        ("/operations one two", "Usage: /operations [operation-id]"),
    ],
)
def test_builtin_usage_errors_are_exact(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    message: str,
) -> None:
    harness = _make_harness(monkeypatch, [prompt, "/exit"])

    assert harness.run() == 0

    error = next(component for component in harness.history.components if isinstance(component, StatusLine))
    assert error.text == f"error: {message}"
    assert error.kind == "error"


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        ("/motion", "info: Motion is enabled for this TUI process."),
        ("/motion on", "info: Motion enabled for this TUI process."),
    ],
)
def test_motion_query_and_enable_messages_are_exact(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    message: str,
) -> None:
    harness = _make_harness(monkeypatch, [prompt, "/exit"])

    assert harness.run() == 0

    notice = next(component for component in harness.history.components if isinstance(component, StatusLine))
    assert notice.text == message
    assert notice.kind == "info"


def test_active_turn_agents_routes_before_the_agents_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, ["/agents status", "/exit"])
    harness.dispatcher._is_turn_active = lambda: harness.events.append(("turn:active",)) or True
    harness.dispatcher._handle_active_turn_prompt = lambda prompt: harness.events.append(("active", prompt)) or True

    assert harness.run() == 0

    assert ("active", "/agents status") in harness.events
    assert not any(event[0] == "command:agents" for event in harness.events)


def test_extension_command_refreshes_footer_and_renders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, ["/extension", "/exit"])
    harness.dispatcher._dispatch_extension_command = lambda prompt: (
        harness.events.append(("extension", prompt)) or prompt == "/extension"
    )

    assert harness.run() == 0

    extension_index = harness.events.index(("extension", "/extension"))
    assert harness.events[extension_index + 1 : extension_index + 3] == [
        ("footer",),
        ("render", False),
    ]


@pytest.mark.parametrize(
    ("prompt", "registered_extension", "registered_template", "unknown"),
    [
        ("/unknown", False, False, True),
        ("/subagents inspect", False, False, False),
        ("/registered", True, False, False),
        ("/template", False, True, False),
        ("/coordination delegate this", False, True, False),
    ],
)
def test_slash_rejection_exceptions_preserve_agent_prompt_routing(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    registered_extension: bool,
    registered_template: bool,
    unknown: bool,
) -> None:
    harness = _make_harness(monkeypatch, [prompt, "/exit"])
    harness.dispatcher._is_registered_extension_command = lambda value: value == prompt and registered_extension
    harness.dispatcher._is_registered_prompt_template = lambda value: value == prompt and registered_template

    assert harness.run() == 0

    assert (("command:unknown", prompt) in harness.events) is unknown
    assert (("turn:start", prompt, 7, 19) in harness.events) is not unknown


def test_active_generic_prompt_routes_without_starting_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, ["steer this", "/exit"])
    harness.dispatcher._handle_active_turn_prompt = lambda prompt: (
        harness.events.append(("active", prompt)) or prompt == "steer this"
    )

    assert harness.run() == 0

    assert ("active", "steer this") in harness.events
    assert not any(event[:2] == ("turn:start", "steer this") for event in harness.events)
    assert ("status", "Thinking") not in harness.events


def test_normal_prompt_measures_and_starts_turn_in_exact_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, ["do the work", "/exit"])

    assert harness.run() == 0

    ordered = [
        harness.events.index(("status", "Thinking")),
        harness.events.index(("motion", "turn", MotionState.WORKING)),
        harness.events.index(("tokens", ("one", "two"))),
        harness.events.index(("footer",)),
        harness.events.index(("render", False), harness.events.index(("tokens", ("one", "two")))),
        harness.events.index(("turn:start", "do the work", 7, 19)),
    ]
    assert ordered == sorted(ordered)


def test_finally_cleanup_preserves_owner_subscription_and_abort_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, [None], wait_results=(False, True))
    dispatcher = harness.dispatcher
    dispatcher._user_commands = _Closable(harness.events, "user")
    _set_dispatcher_binding(dispatcher, "_session_commands", _Closable(harness.events, "session"))
    _set_dispatcher_binding(
        dispatcher,
        "_extension_commands",
        _Closable(harness.events, "extension"),
    )
    _set_dispatcher_binding(
        dispatcher,
        "_extension_host",
        _Disposable(harness.events, "extension-host"),
    )
    for attribute, name in (
        ("_unsubscribe_session_events", "unsubscribe:session"),
        ("_unsubscribe_footer_branch_change", "unsubscribe:footer"),
        ("_unsubscribe_tui_terminal_input", "unsubscribe:terminal"),
        ("_unsubscribe_tui_scroll_change", "unsubscribe:scroll"),
        ("_unsubscribe_app_session_rebound", "unsubscribe:rebound"),
        ("_unsubscribe_process_events", "unsubscribe:process"),
    ):
        setattr(dispatcher, attribute, lambda name=name: harness.events.append((name,)))

    assert harness.run() == 0

    cleanup = [
        ("user:close", None),
        ("drain",),
        ("wait", False),
        ("abort",),
        ("wait", True),
        ("drain",),
        ("session:close", SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS),
        ("extension:close", SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS),
        ("unsubscribe:session",),
        ("unsubscribe:footer",),
        ("unsubscribe:terminal",),
        ("unsubscribe:scroll",),
        ("unsubscribe:rebound",),
        ("extension-host:dispose",),
        ("unsubscribe:process",),
        ("subagents:shutdown",),
        ("footer-provider:dispose",),
        ("trace", "shutdown", {"status": "ok"}),
        ("motion:stop",),
        ("tui:stop",),
        ("sigint:restore", "previous"),
    ]
    indices: list[int] = []
    cursor = -1
    for event in cleanup:
        cursor = harness.events.index(event, cursor + 1)
        indices.append(cursor)
    assert indices == sorted(indices)
    assert dispatcher._session_commands is None
    assert dispatcher._extension_commands is None
    assert dispatcher._extension_host is None
    assert dispatcher._run_loop_active is False


def test_finally_skips_optional_owners_and_trace_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _make_harness(monkeypatch, [None], trace=False)

    assert harness.run() == 0

    names = _event_names(harness.events)
    assert "abort" not in names
    assert "trace" not in names
    assert names[-3:] == ["motion:stop", "tui:stop", "sigint:restore"]
