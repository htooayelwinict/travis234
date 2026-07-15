"""Focused view ownership for the TUI."""

from __future__ import annotations

import json
import os
import queue
import signal as signal_module
import subprocess
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from travis.ai.providers.capabilities import ProviderParamWarning
from travis.ai.providers.params import GenerationParams, compact_generation_params_display
from travis.compaction import estimate_tokens
from travis.coding_agent.session_types import BashResult
from travis.coding_agent.session_catalog import SessionInfo
from travis.coding_agent.session_commands import SessionCommandExecutor
from travis.coding_agent.processes.types import ProcessEvent, ProcessSnapshot, ProcessState
from travis.coding_agent.tools.bash import BashExecOptions, get_shell_env
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.tui.components import (
    CombinedAutocompleteProvider,
    Component,
    Container,
    FooterComponent,
    Input,
    SelectItem,
    SelectList,
    Spacer,
    StatusLine,
    Text,
)
from travis.tui.components.autocomplete import _call_autocomplete_method, _settle_autocomplete_result
from travis.tui.interactive import (
    AssistantMessageComponent,
    BashExecutionComponent,
    message_to_component,
    user_message_to_component,
)
from travis.tui.user_commands import (
    ResolvedUserCommand,
    UserCommandBinding,
    UserCommandController,
    UserCommandHandle,
)

from travis.tui.footer_data import _footer_usage_stats
from travis.tui.interactive_custom_dialog import prompt_extension_custom as _prompt_extension_custom
from travis.tui.interactive_extensions import _apply_hidden_thinking_label, _autocomplete_trigger_characters, _coerce_extension_component, _create_extension_widget_component, _dispose_extension_widget, _extension_dialog_aborted, _extension_dialog_label, _extension_dialog_secret, _resolve_extension_select_choice, _set_autocomplete_trigger_characters

def _short_status_text(text: str, *, limit: int) -> str:
    value = str(text or "").replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."

class InteractiveView:
    """Owns a focused interactive runtime concern."""

    def init(self) -> None:
        if self._initialized:
            return
        if hasattr(self.app, "renderer") and hasattr(self.app.renderer, "set_hidden_thinking_label"):
            self.app.renderer.set_hidden_thinking_label(self.hidden_thinking_label)
        if hasattr(self.app, "renderer") and hasattr(self.app.renderer, "set_hide_thinking_block"):
            self.app.renderer.set_hide_thinking_block(self.hide_thinking_block)
        self.tui.add(self.header_container)
        self.tui.add(self.history)
        self._populate_existing_history()
        self._render_widgets(request_render=False)
        self.tui.add(self.widget_container_above)
        self.tui.add(self.editor_container)
        self.tui.add(self.widget_container_below)
        self.tui.add(self.status)
        self.tui.add(self.footer_container)
        if self._unsubscribe_session_events is None:
            self._unsubscribe_session_events = self.app.session.subscribe(
                lambda event: self.tui.post(lambda: self._handle_session_event(event))
            )
        if self._unsubscribe_footer_branch_change is None:
            self._unsubscribe_footer_branch_change = self.footer_data_provider.on_branch_change(
                lambda: self.tui.post(self._handle_footer_branch_change)
            )
        if self._unsubscribe_tui_terminal_input is None:
            self._unsubscribe_tui_terminal_input = self.tui.add_input_listener(self._handle_tui_terminal_input)
        process_service = getattr(self.app, "process_service", None)
        subscribe_process = getattr(process_service, "subscribe", None)
        if self._unsubscribe_process_events is None and callable(subscribe_process):
            self._unsubscribe_process_events = subscribe_process(
                lambda event: self.tui.post(lambda: self._handle_process_event(event))
            )
        self._update_available_provider_count()
        self._refresh_footer()
        self.tui.start()
        self.app.session.bind_extensions(self._extension_bindings())
        self.setup_autocomplete_provider()
        if self.app.event_trace is not None:
            self.app.event_trace.write(
                "tui_ready",
                {
                    "provider": self.app.session.model.provider,
                    "model": self.app.session.model.id,
                    "session_id": self.app.session.session_id,
                    "session_path": self.app.session.session_path,
                },
            )
        self._initialized = True

    def _populate_existing_history(self) -> None:
        if self._history_populated:
            return
        self._history_populated = True
        custom_renderers = self._custom_message_renderers()
        for message in self.app.messages:
            component = message_to_component(
                message,
                custom_renderers,
                hide_thinking_block=self.hide_thinking_block,
                hidden_thinking_label=self.hidden_thinking_label,
            )
            if component is not None:
                self.history.add(component)

    def _custom_message_renderers(self) -> dict:
        runner = getattr(self.app.session, "extension_runner", None)
        if runner is None or not hasattr(runner, "get_message_renderers"):
            return {}
        return runner.get_message_renderers()

    def create_base_autocomplete_provider(self) -> CombinedAutocompleteProvider:
        commands = [
            {"name": "compact", "description": "Compress conversation context; use 'deep' for near-baseline cleanup"},
            {"name": "compress", "description": "Compress conversation context; use 'deep' for near-baseline cleanup"},
            {"name": "clone", "description": "Clone the complete active branch"},
            {"name": "copy", "description": "Copy the last agent message"},
            {"name": "exit", "description": "Exit the interactive session"},
            {"name": "export", "description": "Export the active session as HTML or JSONL"},
            {"name": "fork", "description": "Fork before a selected user message"},
            {"name": "help", "description": "Show TUI commands"},
            {"name": "import", "description": "Import a JSONL session"},
            {"name": "login", "description": "Configure provider authentication"},
            {"name": "logout", "description": "Remove provider authentication"},
            {"name": "model", "description": "Switch model"},
            {"name": "models", "description": "List available models"},
            {"name": "install", "description": "Install a resource package"},
            {"name": "remove", "description": "Remove an installed resource package"},
            {"name": "update", "description": "Update installed resource packages"},
            {"name": "packages", "description": "List installed resource packages"},
            {"name": "params", "description": "Show active provider generation parameters"},
            {"name": "processes", "description": "Inspect and control managed processes"},
            {"name": "quit", "description": "Exit the interactive session"},
            {"name": "reload", "description": "Reload extensions, skills, prompts, and themes"},
            {"name": "resume", "description": "Switch to a previous session"},
            {"name": "new", "description": "Start a new persistent session"},
            {"name": "name", "description": "Name the active session"},
            {"name": "session", "description": "Show active session details"},
            {"name": "share", "description": "Report configured session sharing support"},
            {"name": "theme", "description": "Select a discovered theme"},
            {"name": "tree", "description": "Navigate the active session tree"},
            {"name": "trust", "description": "View or change the project trust decision"},
        ]
        runner = getattr(self.app.session, "extension_runner", None)
        if runner is not None and hasattr(runner, "get_all_registered_commands"):
            for command in runner.get_all_registered_commands():
                command_info = {"name": command.name, "description": command.description}
                get_argument_completions = getattr(command, "get_argument_completions", None)
                if callable(get_argument_completions):
                    command_info["getArgumentCompletions"] = get_argument_completions
                commands.append(command_info)
        return CombinedAutocompleteProvider(commands, str(self.app.cwd))

    def setup_autocomplete_provider(self) -> None:
        provider: object = self.create_base_autocomplete_provider()
        trigger_characters: list[str] = []
        for wrap_provider in self.autocomplete_provider_wrappers:
            provider = wrap_provider(provider)
            trigger_characters.extend(_autocomplete_trigger_characters(provider))
        if trigger_characters:
            _set_autocomplete_trigger_characters(provider, list(dict.fromkeys(trigger_characters)))
        self.autocomplete_provider = provider
        if self.active_editor is not None:
            self.active_editor.set_autocomplete_provider(provider)

    def add_autocomplete_provider(self, factory: Callable[[object], object]) -> None:
        self.autocomplete_provider_wrappers.append(factory)
        self.setup_autocomplete_provider()

    def get_autocomplete_suggestions(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        options: dict | None = None,
    ) -> object:
        if self.autocomplete_provider is None:
            return None
        return _settle_autocomplete_result(
            _call_autocomplete_method(
                self.autocomplete_provider,
                "get_suggestions",
                "getSuggestions",
                lines,
                cursor_line,
                cursor_col,
                options or {"signal": None, "force": False},
            )
        )

    def _handle_session_event(self, event) -> None:
        event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
        if event_type in {"subagent_start", "subagent_stop"}:
            self._render_subagent_lifecycle_event(event)
            return
        if event_type in {"subagent_tool_start", "subagent_tool_end"}:
            self._render_subagent_tool_event(event)
            return
        if event_type == "auto_retry_start":
            delay_ms = int(getattr(event, "delay_ms", getattr(event, "delayMs", 0)) or 0)
            seconds = max(0, (delay_ms + 999) // 1000)
            attempt = getattr(event, "attempt", 0)
            max_attempts = getattr(event, "max_attempts", getattr(event, "maxAttempts", 0))
            self.status.set_message(f"Retrying ({attempt}/{max_attempts}) in {seconds}s")
            self._refresh_footer()
            self.tui.request_render()
            return
        if event_type == "auto_retry_end":
            if not getattr(event, "success", False):
                final_error = getattr(event, "final_error", getattr(event, "finalError", None)) or "Unknown error"
                self.history.add(StatusLine(f"Retry failed after {event.attempt} attempts: {final_error}", kind="error"))
            self.status.set_message("Running" if self._is_turn_active() else "Idle")
            self._refresh_footer()
            self.tui.request_render()
            return
        if event_type == "message_end":
            message = getattr(event, "message", None)
            if getattr(message, "role", None) == "custom":
                component = message_to_component(
                    message,
                    self._custom_message_renderers(),
                    hide_thinking_block=self.hide_thinking_block,
                    hidden_thinking_label=self.hidden_thinking_label,
                )
                if component is not None:
                    self.history.add(component)
                self.tui.request_render()
                return
        if event_type == "session_info_changed":
            self._refresh_footer()
            self.tui.request_render()

    def _render_subagent_lifecycle_event(self, event) -> None:
        get = event.get if isinstance(event, dict) else lambda key, default=None: getattr(event, key, default)
        role = str(get("child_role", get("role", "subagent")) or "subagent")
        task_id = str(get("child_subagent_id", get("taskId", get("task_id", ""))) or "").strip()
        event_type = str(get("type", "") or "")
        if event_type == "subagent_start":
            line = f"subagent {role} started"
            if task_id:
                line = f"{line} {task_id}"
            kind = "info"
        else:
            status = str(get("status", "") or "completed")
            line = f"subagent {role} {status}"
            if task_id:
                line = f"{line} {task_id}"
            summary = _short_status_text(str(get("child_summary", "") or ""), limit=120)
            if summary:
                line = f"{line}: {summary}"
            kind = "warning" if status in {"failed", "timeout", "cancelled"} else "info"
        self.history.add(StatusLine(line, kind=kind))
        self._refresh_footer()
        self.tui.request_render()

    def _render_subagent_tool_event(self, event) -> None:
        get = event.get if isinstance(event, dict) else lambda key, default=None: getattr(event, key, default)
        event_type = str(get("type", "") or "")
        role = str(get("role", "subagent") or "subagent")
        tool = str(get("toolName", get("tool_name", "tool")) or "tool")
        status = str(get("status", "") or "").strip() or (
            "started" if event_type == "subagent_tool_start" else "ok"
        )
        if status != "error":
            return
        args_preview = _short_status_text(str(get("argsPreview", get("args_preview", "")) or "").strip(), limit=80)
        result_preview = _short_status_text(
            str(get("resultPreview", get("result_preview", "")) or "").strip(),
            limit=120,
        )
        line = f"subagent {role} {tool} {status}"
        if args_preview:
            line = f"{line} {args_preview}"
        if result_preview:
            line = f"{line} => {result_preview}"
        kind = "warning" if status == "error" else "info"
        self.history.add(StatusLine(line, kind=kind))
        self._refresh_footer()
        self.tui.request_render()

    def _handle_footer_branch_change(self) -> None:
        self._refresh_footer()
        self.tui.request_render()

    def _render_auto_compaction_notice(self, before_compressions: int, before_tokens: int) -> None:
        after_compressions = self.app.compaction.compressor.compression_count
        if after_compressions <= before_compressions:
            compressor = self.app.compaction.compressor
            if getattr(compressor, "_last_compress_aborted", False):
                model = getattr(compressor, "_last_summary_model_requested", None) or compressor.model
                error = getattr(compressor, "_last_summary_error", None) or "unknown error"
                notice_key = (model, error)
                if notice_key != self._last_compaction_failure_notice_key:
                    self._last_compaction_failure_notice_key = notice_key
                    self.history.add(
                        StatusLine(
                            f"Context compaction aborted for '{model}' ({error}); conversation preserved unchanged.",
                            kind="warning",
                        )
                    )
            return
        before = self.app.compaction.last_compression_before_tokens or before_tokens
        after_tokens = self.app.compaction.last_compression_after_tokens or estimate_tokens(self.app.messages)
        self.history.add(
            StatusLine(
                f"Context compacted: ~{before:,} -> ~{after_tokens:,} tokens",
                kind="compact",
            )
        )
        result = self.app.compaction.last_compression_result
        if result is not None and getattr(result, "summary_model_fallback", False):
            requested = getattr(result, "summary_model_requested", None) or "configured compression model"
            used = getattr(result, "summary_model_used", None) or "main model"
            error = getattr(result, "summary_model_error", None) or "unknown error"
            self.history.add(
                StatusLine(
                    f"Compression model '{requested}' failed ({error}); recovered with '{used}'.",
                    kind="warning",
                )
            )

    def _refresh_footer(self) -> None:
        self._ensure_builtin_themes()
        self.theme_controller.sync()
        self.footer.model = self.app.session.model.id
        self.footer.provider = self.app.session.model.provider
        self.footer.thinking_level = self.app.session.thinking_level
        self.footer.session_name = self.app.session.session_name
        self.footer.pending = len(self.app.session.agent.state.pending_tool_calls)
        usage_stats = _footer_usage_stats(self.app.session.messages)
        self.footer.total_input = usage_stats["input"]
        self.footer.total_output = usage_stats["output"]
        self.footer.total_cache_read = usage_stats["cache_read"]
        self.footer.total_cache_write = usage_stats["cache_write"]
        self.footer.latest_cache_hit_rate = usage_stats["latest_cache_hit_rate"]
        self.footer.total_cost = usage_stats["cost"]
        context_usage = self.app.session.get_context_usage()
        self.footer.context_tokens = estimate_tokens(self.app.messages)
        self.footer.context_window = None
        self.footer.context_percent = None
        self.footer.context_percent_unknown = False
        self.footer.context_estimate_rough = False
        if isinstance(context_usage, dict):
            context_tokens = context_usage.get("tokens")
            context_window = context_usage.get("contextWindow", context_usage.get("context_window"))
            context_percent = context_usage.get("percent")
            context_estimated = context_usage.get("estimated")
            if isinstance(context_tokens, (int, float)):
                self.footer.context_tokens = int(context_tokens)
            if isinstance(context_window, (int, float)):
                self.footer.context_window = int(context_window)
            if isinstance(context_percent, (int, float)):
                self.footer.context_percent = float(context_percent)
            elif context_percent is None and "percent" in context_usage:
                self.footer.context_percent_unknown = True
            if context_estimated is True:
                self.footer.context_estimate_rough = True
        if self.app.compaction.awaiting_real_usage_after_compression:
            self.footer.context_percent = None
            self.footer.context_percent_unknown = True
            self.footer.context_estimate_rough = True
        self.footer.context_threshold = self.app.compaction.compressor.threshold_tokens
        self.footer.compression_count = self.app.compaction.compressor.compression_count
        self.footer.extension_statuses = dict(self.extension_statuses)
        self.footer.git_branch = self.footer_data_provider.get_git_branch()
        self.footer.available_provider_count = self.footer_data_provider.get_available_provider_count()
        self.footer.model_reasoning = bool(getattr(self.app.session.model, "reasoning", False))
        self.footer.history_hint = None

    def set_extension_status(self, key: str, text: str | None) -> None:
        if text is None:
            self.extension_statuses.pop(str(key), None)
        else:
            self.extension_statuses[str(key)] = str(text)
        self._refresh_footer()
        self.tui.request_render()

    def set_working_message(self, message: str | None = None) -> None:
        self.status.set_message(message if message is not None else self.default_working_message)
        self.tui.request_render()

    def set_working_visible(self, visible: bool) -> None:
        self.status.set_visible(bool(visible))
        self.tui.request_render()

    def set_working_indicator(self, options: dict | None = None) -> None:
        indicator: str | None = None
        if isinstance(options, dict):
            frames = options.get("frames")
            if isinstance(frames, list) and frames:
                indicator = str(frames[0])
            elif isinstance(frames, tuple) and frames:
                indicator = str(frames[0])
            elif frames == []:
                indicator = ""
        self.status.set_indicator(indicator)
        self.tui.request_render()

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self.hidden_thinking_label = str(label) if label is not None else self.default_hidden_thinking_label
        _apply_hidden_thinking_label(self.history, self.hidden_thinking_label)
        if hasattr(self.app, "renderer") and hasattr(self.app.renderer, "set_hidden_thinking_label"):
            self.app.renderer.set_hidden_thinking_label(self.hidden_thinking_label)
        self.tui.request_render()

    def set_terminal_title(self, title: str) -> None:
        self.tui.terminal.set_title(str(title))

    def set_editor_text(self, text: str) -> None:
        self.editor_text = str(text)
        if self.active_editor is not None:
            self.active_editor.set_value(self.editor_text)
        self.tui.request_render()

    def get_editor_text(self) -> str:
        if self.active_editor is not None:
            return self.active_editor.get_value()
        return self.editor_text

    def paste_to_editor(self, text: str) -> None:
        paste_sequence = f"\x1b[200~{text}\x1b[201~"
        if self.active_editor is not None:
            self.active_editor.handle_input(paste_sequence)
            self.editor_text = self.active_editor.get_value()
        else:
            editor = Input(value=self.editor_text)
            editor.handle_input(paste_sequence)
            self.editor_text = editor.get_value()
        self.tui.request_render()

    def set_extension_footer(self, factory: Callable | None = None) -> None:
        _dispose_extension_widget(self.custom_footer)
        self.custom_footer = None
        self.footer_container.clear()
        if factory is None:
            self.footer_container.add(self.footer)
        else:
            component = factory(self.tui, None, self.footer_data_provider)
            self.custom_footer = component
            self.footer_container.add(_coerce_extension_component(component))
        self.tui.request_render()

    def set_extension_header(self, factory: Callable | None = None) -> None:
        _dispose_extension_widget(self.custom_header)
        self.custom_header = None
        self.header_container.clear()
        if factory is None:
            self.header_container.add(self.built_in_header)
        else:
            component = factory(self.tui, None)
            self.custom_header = component
            self.header_container.add(_coerce_extension_component(component))
        self.header_container.add(Spacer(1))
        self.tui.request_render()

    def set_extension_widget(self, key: str, content: object, options: dict | None = None) -> None:
        widget_key = str(key)
        _dispose_extension_widget(self.extension_widgets_above.pop(widget_key, None))
        _dispose_extension_widget(self.extension_widgets_below.pop(widget_key, None))
        if content is None:
            self._render_widgets()
            return

        placement = options.get("placement") if isinstance(options, dict) else None
        component = _create_extension_widget_component(content, self.tui, self.MAX_WIDGET_LINES)
        target = self.extension_widgets_below if placement == "belowEditor" else self.extension_widgets_above
        target[widget_key] = component
        self._render_widgets()

    def _render_widgets(self, *, request_render: bool = True) -> None:
        self._render_widget_container(
            self.widget_container_above,
            self.extension_widgets_above,
            spacer_when_empty=True,
            leading_spacer=True,
        )
        self._render_widget_container(
            self.widget_container_below,
            self.extension_widgets_below,
            spacer_when_empty=False,
            leading_spacer=False,
        )
        if request_render:
            self.tui.request_render()

    def _render_widget_container(
        self,
        container: Container,
        widgets: dict[str, Component],
        *,
        spacer_when_empty: bool,
        leading_spacer: bool,
    ) -> None:
        container.clear()
        if not widgets:
            if spacer_when_empty:
                container.add(Spacer(1))
            return
        if leading_spacer:
            container.add(Spacer(1))
        for component in widgets.values():
            container.add(component)

    def prompt_extension_input(
        self,
        title: str,
        placeholder: str | None = None,
        options: dict | None = None,
    ) -> str | None:
        if _extension_dialog_aborted(options):
            return None
        is_secret = _extension_dialog_secret(options)
        clean_title = _extension_dialog_label(title)
        prompt = f"{clean_title} ({placeholder}): " if placeholder else f"{clean_title}: "
        self.history.add(StatusLine(clean_title, kind="input"))
        self.tui.request_render()
        if self._line_input_mode:
            try:
                value = self._read_prompt_from_line_input(prompt)
            except EOFError:
                return None
        else:
            value = self._prompt_tui_value(prompt, mask=is_secret)
        if value is None:
            return None
        text = str(value)
        self.history.add(Text("[redacted]" if is_secret else text))
        self.tui.request_render()
        return text

    def prompt_extension_editor(self, title: str, prefill: str | None = None) -> str | None:
        clean_title = _extension_dialog_label(title)
        prompt = f"{clean_title}: "
        self.history.add(StatusLine(clean_title, kind="editor"))
        if prefill:
            self.history.add(Text(str(prefill)))
        self.tui.request_render()
        try:
            value = self.input_fn(prompt)
        except EOFError:
            return None
        if value is None:
            return None
        text = str(value)
        self.history.add(Text(text))
        self.tui.request_render()
        return text

    def prompt_extension_select(
        self,
        title: str,
        choices: list[str] | tuple[str, ...],
        options: dict | None = None,
        *,
        kind: str = "select",
    ) -> str | None:
        if _extension_dialog_aborted(options):
            return None
        normalized_choices = [str(choice) for choice in choices]
        if not normalized_choices:
            return None
        clean_title = _extension_dialog_label(title)
        if kind == "theme" and not self._line_input_mode:
            self.history.add(StatusLine(clean_title, kind=kind))
            self.tui.request_render()
            selected = self._prompt_tui_theme_select(normalized_choices)
            if selected is not None:
                self.history.add(Text(selected))
                self.tui.request_render()
                return selected
            self.history.add(StatusLine("Selection cancelled.", kind=kind))
            self.tui.request_render()
            return None
        self.history.add(StatusLine(clean_title, kind=kind))
        for index, choice in enumerate(normalized_choices, start=1):
            self.history.add(Text(f"{index}. {choice}"))
        self.tui.request_render()
        if self._line_input_mode:
            self._emit_pending_model_picker_trace()
            try:
                value = self._read_prompt_from_line_input(f"{clean_title} [1-{len(normalized_choices)}]: ")
            except EOFError:
                return None
        else:
            value = self._prompt_tui_value(f"{clean_title} [1-{len(normalized_choices)}]: ")
        if value is None:
            return None
        raw_value = str(value)
        selected = _resolve_extension_select_choice(raw_value, normalized_choices)
        if selected is not None:
            self.history.add(Text(selected))
            self.tui.request_render()
            return selected
        if not raw_value.strip():
            self.history.add(StatusLine("Selection cancelled.", kind=kind))
        else:
            clean_value = _extension_dialog_label(raw_value)
            self.history.add(
                StatusLine(
                    f"Invalid selection: {clean_value}. Enter a number from 1 to {len(normalized_choices)}.",
                    kind="error",
                )
            )
        self.tui.request_render()
        return selected

    def _prompt_tui_theme_select(self, choices: list[str]) -> str | None:
        outcome: queue.Queue[tuple[str, str | None]] = queue.Queue()
        selector = SelectList(
            [SelectItem(value=choice, label=choice) for choice in choices],
            max_visible=min(8, len(choices)),
            theme_context=self.theme_context,
        )
        active_name = self.theme_registry.active_name
        if active_name in choices:
            selector.set_selected_index(choices.index(active_name))

        selector.on_selection_change = lambda item: self.theme_controller.preview(item.value)

        def select(item: SelectItem) -> None:
            self.theme_controller.preview(item.value)
            outcome.put(("select", item.value))

        selector.on_select = select
        selector.on_cancel = lambda: outcome.put(("cancel", None))
        handle = self.tui.show_overlay(
            selector,
            {
                "anchor": "center",
                "width": "70%",
                "minWidth": min(44, max(1, self.tui.terminal.columns)),
                "maxHeight": min(10, max(1, self.tui.terminal.rows - 2)),
            },
        )
        try:
            while not self._shutdown_requested:
                try:
                    action, value = outcome.get(timeout=self.tui.time_until_next_work(0.05))
                except queue.Empty:
                    if self.tui.dispatcher.is_owner_thread():
                        self.tui.drain_dispatcher()
                    continue
                if self.tui.dispatcher.is_owner_thread():
                    self.tui.drain_dispatcher()
                if action == "select":
                    return self.theme_controller.commit_preview_result() or value
                self.theme_controller.restore_preview()
                return None
            self.theme_controller.restore_preview()
            return None
        finally:
            self.theme_controller.restore_preview()
            handle.hide()

    def _prompt_tui_value(self, prompt: str, *, mask: bool = False) -> str | None:
        submitted_queue: queue.Queue[str] = queue.Queue()
        prompt_component = Input(prompt=prompt, on_submit=lambda value: submitted_queue.put(value), mask=mask)
        previous_focus = self.tui.focused_component
        self.active_editor = prompt_component
        self.editor_container.add(prompt_component)
        self.tui.set_focus(prompt_component)
        self.tui.request_render()
        self._emit_pending_model_picker_trace()
        try:
            while not self._shutdown_requested:
                try:
                    value = submitted_queue.get(timeout=self.tui.time_until_next_work(0.05))
                    if self.tui.dispatcher.is_owner_thread():
                        self.tui.drain_dispatcher()
                    return value
                except queue.Empty:
                    if self.tui.dispatcher.is_owner_thread():
                        self.tui.drain_dispatcher()
                    continue
            return None
        finally:
            if prompt_component in self.editor_container.children:
                self.editor_container.remove(prompt_component)
            if self.active_editor is prompt_component:
                self.active_editor = None
            self.tui.set_focus(previous_focus)
            self.tui.request_render()

    def prompt_extension_confirm(
        self,
        title: str,
        message: str,
        options: dict | None = None,
    ) -> bool:
        label = _extension_dialog_label(f"{title}\n{message}")
        return self.prompt_extension_select(label, ("Yes", "No"), options, kind="confirm") == "Yes"

    def prompt_extension_custom(self, factory: Callable[..., object], options: dict | None = None) -> object:
        return _prompt_extension_custom(self, factory, options)

    def add_terminal_input_listener(self, handler: Callable[[str], object]):
        self._terminal_input_listeners.append(handler)

        def unsubscribe() -> None:
            if handler in self._terminal_input_listeners:
                self._terminal_input_listeners.remove(handler)

        return unsubscribe

    def _dispatch_terminal_input(self, data: str) -> tuple[bool, str]:
        current = data
        for listener in list(self._terminal_input_listeners):
            result = listener(current)
            if isinstance(result, dict):
                if "data" in result:
                    current = str(result["data"])
                if result.get("consume"):
                    return True, current
        return False, current

__all__ = (
    'InteractiveView',
    '_short_status_text',
)
