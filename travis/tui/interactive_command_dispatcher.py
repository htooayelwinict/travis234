"""Focused command dispatcher ownership for the TUI."""

from __future__ import annotations

import queue
from collections.abc import Callable
from dataclasses import dataclass

from travis.compaction import estimate_tokens
from travis.tui.components import (
    Editor,
    StatusLine,
    Text,
)
from travis.tui.interactive import (
    user_message_to_component,
)
from travis.tui.interactive_shutdown import SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS
from travis.tui.interactive_surfaces import InteractiveCommandDispatchSurface
from travis.tui.motion import MotionState


def _is_manual_compression_command(prompt: str) -> bool:
    return prompt in {"/compress", "/compact"} or prompt.startswith("/compress ") or prompt.startswith("/compact ")


def _is_command_like_slash_prompt(prompt: str) -> bool:
    if not prompt.startswith("/"):
        return False
    command_name = prompt[1:].partition(" ")[0]
    return bool(command_name) and "/" not in command_name


def _is_prompt_level_skill_trigger(prompt: str) -> bool:
    return prompt == "/subagents" or prompt.startswith("/subagents ")


def _is_help_command(prompt: str) -> bool:
    return prompt == "/help" or prompt.startswith("/help ")


def _is_processes_command(prompt: str) -> bool:
    return prompt == "/processes"


def _parse_operations_command(prompt: str) -> str | None | object:
    if prompt == "/operations":
        return None
    if prompt.startswith("/operations "):
        operation_id = prompt[len("/operations ") :].strip()
        return operation_id if operation_id and " " not in operation_id else _INVALID_OPERATIONS_COMMAND
    return _NOT_OPERATIONS_COMMAND


def _parse_memory_command(prompt: str) -> bool | object:
    if prompt == "/memory status":
        return True
    if prompt == "/memory" or prompt.startswith("/memory "):
        return _INVALID_MEMORY_COMMAND
    return _NOT_MEMORY_COMMAND


def _is_lsp_status_command(prompt: str) -> bool:
    return prompt == "/lsp status"


def _parse_agents_command(prompt: str) -> tuple[str, tuple[str, ...]] | None:
    if prompt == "/agents" or prompt == "/agents status":
        return "status", ()
    if not prompt.startswith("/agents "):
        return None
    parts = prompt.split(maxsplit=3)
    if len(parts) == 3 and parts[1] in {"inspect", "cancel"}:
        return parts[1], (parts[2],)
    if len(parts) == 4 and parts[1] == "steer" and parts[3].strip():
        return "steer", (parts[2], parts[3].strip())
    return "invalid", ()


def _is_reload_command(prompt: str) -> bool:
    return prompt == "/reload"


def _is_trust_command(prompt: str) -> bool:
    return prompt == "/trust"


def _parse_session_command(prompt: str) -> str | None:
    if prompt == "/resume":
        return "resume"
    if prompt == "/new":
        return "new"
    if prompt == "/session":
        return "session"
    if prompt == "/name" or prompt.startswith("/name "):
        return "name"
    if prompt == "/fork":
        return "fork"
    if prompt == "/clone":
        return "clone"
    if prompt == "/tree":
        return "tree"
    if prompt == "/export" or prompt.startswith("/export "):
        return "export"
    if prompt == "/import" or prompt.startswith("/import "):
        return "import"
    if prompt == "/copy":
        return "copy"
    if prompt == "/share":
        return "share"
    if prompt == "/theme" or prompt.startswith("/theme "):
        return "theme"
    return None


def _parse_auth_command(prompt: str) -> tuple[str, str | None] | None:
    if prompt == "/login":
        return "login", None
    if prompt == "/logout":
        return "logout", None
    return None


def _parse_model_command(prompt: str) -> tuple[str, str | None] | None:
    if prompt == "/models":
        return "list", None
    if prompt == "/model":
        return "select", None
    if prompt.startswith("/model "):
        return "select", prompt[len("/model ") :].strip()
    return None


def _parse_params_command(prompt: str) -> str | None:
    if prompt == "/params":
        return ""
    if prompt.startswith("/params "):
        return prompt[len("/params ") :].strip()
    return None


_NOT_MOTION_COMMAND = object()
_INVALID_MOTION_COMMAND = object()
_NOT_MEMORY_COMMAND = object()
_INVALID_MEMORY_COMMAND = object()
_NOT_OPERATIONS_COMMAND = object()
_INVALID_OPERATIONS_COMMAND = object()
_NO_COMMAND_MATCH = object()


def _parse_motion_command(prompt: str) -> bool | None | object:
    if prompt == "/motion":
        return None
    if prompt == "/motion on":
        return True
    if prompt == "/motion off":
        return False
    if prompt.startswith("/motion "):
        return _INVALID_MOTION_COMMAND
    return _NOT_MOTION_COMMAND


@dataclass(frozen=True, slots=True)
class CommandBinding:
    name: str
    classifier: Callable[[str], object]
    handler_key: str


@dataclass(slots=True)
class _PromptEditorState:
    editor: Editor
    submitted: list[str]
    submitted_queue: queue.Queue[str]


@dataclass(frozen=True, slots=True)
class _PromptRead:
    prompt: str | None
    retry: bool = False


def _match_predicate(predicate: Callable[[str], bool], prompt: str) -> object:
    return True if predicate(prompt) else _NO_COMMAND_MATCH


def _match_parser(parser: Callable[[str], object], no_match: object, prompt: str) -> object:
    result = parser(prompt)
    return _NO_COMMAND_MATCH if result is no_match else result


BUILTIN_COMMAND_BINDINGS: tuple[CommandBinding, ...] = (
    CommandBinding(
        "motion", lambda prompt: _match_parser(_parse_motion_command, _NOT_MOTION_COMMAND, prompt), "motion"
    ),
    CommandBinding("help", lambda prompt: _match_predicate(_is_help_command, prompt), "help"),
    CommandBinding("session", lambda prompt: _match_parser(_parse_session_command, None, prompt), "session"),
    CommandBinding(
        "memory", lambda prompt: _match_parser(_parse_memory_command, _NOT_MEMORY_COMMAND, prompt), "memory"
    ),
    CommandBinding(
        "operations",
        lambda prompt: _match_parser(_parse_operations_command, _NOT_OPERATIONS_COMMAND, prompt),
        "operations",
    ),
    CommandBinding("processes", lambda prompt: _match_predicate(_is_processes_command, prompt), "processes"),
    CommandBinding("lsp", lambda prompt: _match_predicate(_is_lsp_status_command, prompt), "lsp"),
    CommandBinding("agents", lambda prompt: _match_parser(_parse_agents_command, None, prompt), "agents"),
    CommandBinding("reload", lambda prompt: _match_predicate(_is_reload_command, prompt), "reload"),
    CommandBinding("trust", lambda prompt: _match_predicate(_is_trust_command, prompt), "trust"),
    CommandBinding("bash", lambda prompt: _match_parser(_parse_bash_command, None, prompt), "bash"),
    CommandBinding("compact", lambda prompt: _match_predicate(_is_manual_compression_command, prompt), "compact"),
    CommandBinding("auth", lambda prompt: _match_parser(_parse_auth_command, None, prompt), "auth"),
    CommandBinding("model", lambda prompt: _match_parser(_parse_model_command, None, prompt), "model"),
    CommandBinding("params", lambda prompt: _match_parser(_parse_params_command, None, prompt), "params"),
)


def classify_builtin_command(prompt: str) -> tuple[CommandBinding, object] | None:
    for binding in BUILTIN_COMMAND_BINDINGS:
        parsed = binding.classifier(prompt)
        if parsed is not _NO_COMMAND_MATCH:
            return binding, parsed
    return None


def _is_openrouter_model(model) -> bool:
    return getattr(model, "provider", "") == "openrouter" or "openrouter.ai" in str(getattr(model, "base_url", ""))


def _parse_bash_command(prompt: str) -> tuple[str, bool] | None:
    if not prompt.startswith("!"):
        return None
    excluded = prompt.startswith("!!")
    command = prompt[2:].strip() if excluded else prompt[1:].strip()
    if not command:
        return None
    return command, excluded


class InteractiveCommandDispatcher(InteractiveCommandDispatchSurface):
    """Owns a focused interactive runtime concern."""

    __slots__ = ()

    def run(self) -> int:
        self._run_loop_active = True
        self.init()
        previous_sigint_handler = self._install_sigint_handler()
        try:
            if not self._resume_at_startup():
                return 0
            while self._run_prompt_iteration():
                pass
            return 0
        finally:
            self._finish_run(previous_sigint_handler)

    def _resume_at_startup(self) -> bool:
        if not self._open_resume_picker:
            return True
        self._open_resume_picker = False
        return bool(self._run_resume_command(startup=True))

    def _run_prompt_iteration(self) -> bool:
        state = self._create_prompt_editor()
        prompt_read = self._read_prompt(state)
        if prompt_read.retry:
            self._preserve_prompt_editor(state.editor)
            return True
        if prompt_read.prompt is None:
            return False
        prompt = prompt_read.prompt
        self._detach_prompt_editor(state.editor, prompt)
        if prompt in {"/exit", "/quit", "exit", "quit"}:
            self._request_exit()
            return False
        if not prompt:
            return True
        state.editor.add_to_history(prompt)
        self._dispatch_prompt(prompt)
        return True

    def _create_prompt_editor(self) -> _PromptEditorState:
        submitted: list[str] = []
        submitted_queue: queue.Queue[str] = queue.Queue()

        def on_submit(
            value: str,
            submitted: list[str] = submitted,
            submitted_queue: queue.Queue[str] = submitted_queue,
        ) -> None:
            submitted.append(value)
            submitted_queue.put(value)

        editor = Editor(
            value=self.editor_text,
            prompt=self.prompt_label,
            on_submit=on_submit,
            theme_context=self.theme_context,
        )
        editor.set_history(self.prompt_history)
        editor.on_escape = self._handle_editor_escape
        if not self._line_input_mode:
            editor.on_extension_shortcut = self._dispatch_extension_shortcut
        editor.set_autocomplete_provider(self.autocomplete_provider)
        self.active_editor = editor
        self.editor_container.add(editor)
        self.tui.set_focus(editor)
        self.tui.request_render()
        return _PromptEditorState(editor, submitted, submitted_queue)

    def _read_prompt(self, state: _PromptEditorState) -> _PromptRead:
        if self._line_input_mode:
            return self._read_line_prompt(state)
        prompt = self._read_prompt_from_tui(state.submitted_queue)
        return _PromptRead(None if prompt is None else prompt.strip())

    def _read_line_prompt(self, state: _PromptEditorState) -> _PromptRead:
        try:
            prompt_text = self._read_prompt_from_line_input()
        except EOFError:
            return _PromptRead(None)
        dispatch_result = self._dispatch_terminal_input(prompt_text)
        if dispatch_result[0]:
            return _PromptRead(None, retry=True)
        prompt_text = dispatch_result[1]
        state.editor.handle_input(f"{prompt_text}\r")
        prompt = state.submitted[0] if state.submitted else state.editor.get_value()
        return _PromptRead(prompt.strip())

    def _preserve_prompt_editor(self, editor: Editor) -> None:
        self.tui.set_focus(None)
        self.editor_container.remove(editor)
        self.editor_text = editor.get_value()
        self.active_editor = None
        self.tui.request_render()

    def _detach_prompt_editor(self, editor: Editor, prompt: str) -> None:
        self.tui.set_focus(None)
        self.editor_container.remove(editor)
        self.active_editor = None
        self.editor_text = ""
        self.tui.scroll_to_bottom()
        component = user_message_to_component(prompt) if prompt else Text("")
        self.history.add(component)
        self.tui.request_render()

    def _request_exit(self) -> None:
        self._shutdown_requested = True
        self._set_motion_signal("termination", MotionState.TERMINATING)
        self.status.set_message("Exiting")
        self._refresh_footer()
        self.tui.request_render()

    def _dispatch_prompt(self, prompt: str) -> None:
        if self._dispatch_early_builtin(prompt):
            return
        if self._run_package_command(prompt):
            return
        if self._dispatch_late_builtin(prompt):
            return
        if self._dispatch_extension_command(prompt):
            self._refresh_footer()
            self.tui.request_render()
            return
        if self._should_reject_unknown_command(prompt):
            self._run_unknown_command(prompt)
            return
        if self._handle_active_turn_prompt(prompt):
            return
        self._start_prompt_turn(prompt)

    def _dispatch_early_builtin(self, prompt: str) -> bool:
        motion_command = _parse_motion_command(prompt)
        if motion_command is not _NOT_MOTION_COMMAND:
            self._run_motion_command(motion_command)
            return True
        if _is_help_command(prompt):
            self._run_help_command()
            return True
        session_command = _parse_session_command(prompt)
        if session_command is not None:
            self._dispatch_session_builtin(session_command, prompt)
            return True
        if self._dispatch_memory_builtin(prompt):
            return True
        if self._dispatch_operations_builtin(prompt):
            return True
        if _is_processes_command(prompt):
            self._run_processes_command()
            return True
        if _is_lsp_status_command(prompt):
            self._run_lsp_status_command()
            return True
        if self._dispatch_agents_builtin(prompt):
            return True
        if _is_reload_command(prompt):
            self._run_reload_command()
            return True
        if _is_trust_command(prompt):
            self._run_trust_command()
            return True
        return False

    def _dispatch_session_builtin(self, command: str, prompt: str) -> None:
        if command == "resume":
            self._run_resume_command()
        elif command == "new":
            self._run_new_session_command()
        elif command == "session":
            self._run_session_info_command()
        elif command == "name":
            self._run_name_command(prompt)
        elif command == "fork":
            self._run_fork_command()
        elif command == "clone":
            self._run_clone_command()
        elif command == "tree":
            self._run_tree_command()
        elif command == "export":
            self._run_export_command(prompt)
        elif command == "import":
            self._run_import_command(prompt)
        elif command == "copy":
            self._run_copy_command()
        elif command == "share":
            self._run_share_command()
        elif command == "theme":
            self._run_theme_command(prompt)

    def _dispatch_memory_builtin(self, prompt: str) -> bool:
        command = _parse_memory_command(prompt)
        if command is _NOT_MEMORY_COMMAND:
            return False
        if command is _INVALID_MEMORY_COMMAND:
            self.history.add(StatusLine("Usage: /memory status", kind="error"))
            self.tui.request_render()
        else:
            self._run_memory_status_command()
        return True

    def _dispatch_operations_builtin(self, prompt: str) -> bool:
        command = _parse_operations_command(prompt)
        if command is _NOT_OPERATIONS_COMMAND:
            return False
        if command is _INVALID_OPERATIONS_COMMAND:
            self.history.add(StatusLine("Usage: /operations [operation-id]", kind="error"))
            self.tui.request_render()
        else:
            self._run_operations_command(command)
        return True

    def _dispatch_agents_builtin(self, prompt: str) -> bool:
        command = _parse_agents_command(prompt)
        if command is None:
            return False
        if self._is_turn_active() and self._handle_active_turn_prompt(prompt):
            return True
        self._run_agents_command(command)
        return True

    def _dispatch_late_builtin(self, prompt: str) -> bool:
        bash_command = _parse_bash_command(prompt)
        if bash_command is not None:
            self._run_bash_command(
                bash_command[0],
                exclude_from_context=bash_command[1],
            )
            return True
        if _is_manual_compression_command(prompt):
            self._run_manual_compress(prompt)
            return True
        auth_command = _parse_auth_command(prompt)
        if auth_command is not None:
            self._run_auth_command(auth_command[0], auth_command[1])
            return True
        model_command = _parse_model_command(prompt)
        if model_command is not None:
            self._run_model_command(model_command[0], model_command[1])
            return True
        params_query = _parse_params_command(prompt)
        if params_query is None:
            return False
        self._run_params_command(params_query)
        return True

    def _should_reject_unknown_command(self, prompt: str) -> bool:
        return (
            _is_command_like_slash_prompt(prompt)
            and not _is_prompt_level_skill_trigger(prompt)
            and not self._is_registered_extension_command(prompt)
            and not self._is_registered_prompt_template(prompt)
        )

    def _start_prompt_turn(self, prompt: str) -> None:
        self.status.set_message("Thinking")
        self._set_motion_signal("turn", MotionState.WORKING)
        before_compressions = self.app.compaction.compressor.compression_count
        before_tokens = estimate_tokens(self.app.messages)
        self._refresh_footer()
        self.tui.request_render()
        self._start_turn_thread(prompt, before_compressions, before_tokens)

    def _finish_run(self, previous_sigint_handler: object) -> None:
        self._shutdown_requested = True
        if self._user_commands is not None:
            self._user_commands.close()
        self.tui.drain_dispatcher()
        self._settle_active_turn()
        self.tui.drain_dispatcher()
        self._run_loop_active = False
        self._close_command_owners()
        self._unsubscribe_session_ui()
        self._dispose_remaining_owners(previous_sigint_handler)

    def _settle_active_turn(self) -> None:
        if not self._wait_for_active_turn():
            self._abort_active_turn_for_shutdown()
            self._wait_for_active_turn()

    def _close_command_owners(self) -> None:
        if self._session_commands is not None:
            self._session_commands.close(timeout=SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS)
            self._session_commands = None
        if self._extension_commands is not None:
            self._extension_commands.close(timeout=SESSION_COMMAND_SHUTDOWN_TIMEOUT_SECONDS)
            self._extension_commands = None

    def _unsubscribe_session_ui(self) -> None:
        if self._unsubscribe_session_events is not None:
            self._unsubscribe_session_events()
            self._unsubscribe_session_events = None
        if self._unsubscribe_footer_branch_change is not None:
            self._unsubscribe_footer_branch_change()
            self._unsubscribe_footer_branch_change = None
        if self._unsubscribe_tui_terminal_input is not None:
            self._unsubscribe_tui_terminal_input()
            self._unsubscribe_tui_terminal_input = None
        if self._unsubscribe_tui_scroll_change is not None:
            self._unsubscribe_tui_scroll_change()
            self._unsubscribe_tui_scroll_change = None
        if self._unsubscribe_app_session_rebound is not None:
            self._unsubscribe_app_session_rebound()
            self._unsubscribe_app_session_rebound = None

    def _dispose_remaining_owners(self, previous_sigint_handler: object) -> None:
        if self._extension_host is not None:
            self._extension_host.dispose()
            self._extension_host = None
        if self._unsubscribe_process_events is not None:
            self._unsubscribe_process_events()
            self._unsubscribe_process_events = None
        self._shutdown_subagent_ui()
        self.footer_data_provider.dispose()
        if self.app.event_trace is not None:
            self.app.event_trace.write("shutdown", {"status": "ok"})
        self.motion_controller.stop()
        self.tui.stop()
        self._restore_sigint_handler(previous_sigint_handler)

    def _run_motion_command(self, enabled: bool | None | object) -> None:
        if enabled is _INVALID_MOTION_COMMAND:
            self.history.add(StatusLine("Usage: /motion [on|off]", kind="error"))
        elif enabled is None:
            state = "enabled" if self.motion_controller.enabled else "disabled"
            self.history.add(StatusLine(f"Motion is {state} for this TUI process.", kind="info"))
        else:
            resolved = bool(enabled)
            self.motion_controller.set_enabled(resolved)
            state = "enabled" if resolved else "disabled"
            self.history.add(StatusLine(f"Motion {state} for this TUI process.", kind="info"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()


__all__ = (
    "BUILTIN_COMMAND_BINDINGS",
    "CommandBinding",
    "InteractiveCommandDispatcher",
    "classify_builtin_command",
    "_is_command_like_slash_prompt",
    "_is_help_command",
    "_is_manual_compression_command",
    "_is_openrouter_model",
    "_is_processes_command",
    "_is_lsp_status_command",
    "_parse_agents_command",
    "_is_reload_command",
    "_is_trust_command",
    "_is_prompt_level_skill_trigger",
    "_parse_auth_command",
    "_parse_bash_command",
    "_parse_model_command",
    "_parse_motion_command",
    "_parse_memory_command",
    "_parse_operations_command",
    "_parse_params_command",
    "_parse_session_command",
)
