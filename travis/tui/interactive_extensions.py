"""Focused extensions ownership for the TUI."""

from __future__ import annotations

import inspect
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
from travis.ai.providers.model_catalog import get_last_openrouter_live_catalog_error, get_live_openrouter_models
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
from travis.tui.model_loader import ModelCatalogLoader
from travis.tui.user_commands import (
    ResolvedUserCommand,
    UserCommandBinding,
    UserCommandController,
    UserCommandHandle,
)

class _ExtensionShortcutUI:
    def __init__(self, mode: InteractiveMode) -> None:
        self._mode = mode

    def notify(self, message: str) -> None:
        self._mode.history.add(Text(str(message)))

    def show_error(self, message: str) -> None:
        self._mode.history.add(Text(f"error: {message}"))


    def set_status(self, key: str, text: str | None) -> None:
        self._mode.set_extension_status(key, text)


    def set_working_message(self, message: str | None = None) -> None:
        self._mode.set_working_message(message)


    def set_working_visible(self, visible: bool) -> None:
        self._mode.set_working_visible(visible)


    def set_working_indicator(self, options: dict | None = None) -> None:
        self._mode.set_working_indicator(options)


    def input(
        self,
        title: str,
        placeholder: str | None = None,
        options: dict | None = None,
    ) -> str | None:
        return self._mode.prompt_extension_input(title, placeholder, options)

    def select(
        self,
        title: str,
        options: list[str] | tuple[str, ...],
        dialog_options: dict | None = None,
    ) -> str | None:
        return self._mode.prompt_extension_select(title, options, dialog_options)

    def confirm(
        self,
        title: str,
        message: str,
        options: dict | None = None,
    ) -> bool:
        return self._mode.prompt_extension_confirm(title, message, options)

    def on_terminal_input(self, handler: Callable[[str], object]):
        return self._mode.add_terminal_input_listener(handler)


    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self._mode.set_hidden_thinking_label(label)


    def set_title(self, title: str) -> None:
        self._mode.set_terminal_title(title)


    def set_widget(self, key: str, content: object = None, options: dict | None = None) -> None:
        self._mode.set_extension_widget(key, content, options)


    def set_footer(self, factory: Callable | None = None) -> None:
        self._mode.set_extension_footer(factory)


    def set_header(self, factory: Callable | None = None) -> None:
        self._mode.set_extension_header(factory)


    def set_editor_text(self, text: str) -> None:
        self._mode.set_editor_text(text)


    def get_editor_text(self) -> str:
        return self._mode.get_editor_text()


    def paste_to_editor(self, text: str) -> None:
        self._mode.paste_to_editor(str(text))


    def editor(self, title: str, prefill: str | None = None) -> str | None:
        return self._mode.prompt_extension_editor(title, prefill)

    def custom(self, factory: Callable[..., object], options: dict | None = None) -> object:
        return self._mode.prompt_extension_custom(factory, options)

    def add_autocomplete_provider(self, factory: Callable[[object], object]) -> None:
        self._mode.add_autocomplete_provider(factory)

def _dispose_extension_widget(component: Component | None) -> None:
    if component is not None and callable(getattr(component, "dispose", None)):
        component.dispose()


def _autocomplete_trigger_characters(provider: object) -> list[str]:
    if isinstance(provider, dict):
        value = provider.get("triggerCharacters", provider.get("trigger_characters", []))
    else:
        value = getattr(provider, "triggerCharacters", getattr(provider, "trigger_characters", []))
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _set_autocomplete_trigger_characters(provider: object, value: list[str]) -> None:
    if isinstance(provider, dict):
        provider["triggerCharacters"] = list(value)
        return
    if hasattr(provider, "triggerCharacters") or not hasattr(provider, "trigger_characters"):
        setattr(provider, "triggerCharacters", list(value))
    else:
        setattr(provider, "trigger_characters", list(value))

def _coerce_extension_component(component: object) -> Component:
    if isinstance(component, Component):
        return component
    if hasattr(component, "render"):
        return component  # type: ignore[return-value]
    return Text(str(component))


def _create_extension_widget_component(content: object, tui, max_lines: int) -> Component:
    if isinstance(content, Component):
        return content
    if isinstance(content, (list, tuple)):
        container = Container()
        for line in list(content)[:max_lines]:
            container.add(Text(str(line)))
        if len(content) > max_lines:
            container.add(Text("... (widget truncated)"))
        return container
    if callable(content):
        component = content(tui, None)
        if isinstance(component, Component):
            return component
        return Text(str(component))
    return Text(str(content))


def _manual_compression_focus(prompt: str) -> str | None:
    focus, _deep = _manual_compression_options(prompt)
    return focus


def _manual_compression_options(prompt: str) -> tuple[str | None, bool]:
    for command in ("/compress", "/compact"):
        if prompt == command:
            return None, False
        if prompt.startswith(f"{command} "):
            focus = prompt[len(command) :].strip()
            if not focus:
                return None, False
            parts = focus.split(maxsplit=1)
            mode = parts[0].lower()
            if mode == "deep":
                return (parts[1].strip() if len(parts) > 1 and parts[1].strip() else None), True
            return focus, False
    return None, False


def _extension_dialog_aborted(options: dict | None) -> bool:
    if not isinstance(options, dict):
        return False
    signal = options.get("signal")
    if isinstance(signal, dict):
        return bool(signal.get("aborted"))
    return bool(getattr(signal, "aborted", False))


def _extension_dialog_secret(options: dict | None) -> bool:
    if not isinstance(options, dict):
        return False
    return bool(options.get("secret") or options.get("password") or options.get("mask"))


def _extension_dialog_label(value: object) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ").split())


def _resolve_extension_select_choice(value: str, choices: list[str]) -> str | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        index = int(stripped)
        if 1 <= index <= len(choices):
            return choices[index - 1]
    for choice in choices:
        if stripped == choice:
            return choice
    return None


def _apply_hidden_thinking_label(component, label: str) -> None:
    if isinstance(component, AssistantMessageComponent):
        component.set_hidden_thinking_label(label)
        return
    for child in getattr(component, "children", []) or []:
        _apply_hidden_thinking_label(child, label)

class InteractiveExtensions:
    """Owns a focused interactive runtime concern."""

    def _dispatch_extension_shortcut(self, prompt: str) -> bool:
        if not prompt:
            return False
        runner = getattr(self.app.session, "extension_runner", None)
        if runner is None or not hasattr(runner, "get_shortcuts"):
            return False
        shortcut = runner.get_shortcuts({}).get(prompt.lower())
        if shortcut is None:
            return False
        try:
            result = shortcut.handler(self._extension_shortcut_context())
            if inspect.isawaitable(result):
                import asyncio

                asyncio.run(result)
        except Exception as error:  # noqa: BLE001 - extension shortcut failures should not crash the TUI
            self.history.add(Text(f"Shortcut handler error: {error}"))
        return True

    def _dispatch_extension_command(self, prompt: str) -> bool:
        parse_command = getattr(self.app.session, "_parse_extension_command", None)
        execute_command = getattr(self.app.session, "_try_execute_extension_command", None)
        if not callable(parse_command) or not callable(execute_command):
            return False
        parsed = parse_command(prompt)
        if parsed is None:
            return False
        command, _args = parsed
        if getattr(command, "name", "") not in self.IMMEDIATE_EXTENSION_COMMANDS:
            return False
        status = "ok"
        try:
            execute_command(prompt)
        except Exception as error:  # noqa: BLE001 - command failures should render, not crash the TUI
            status = "error"
            self.history.add(StatusLine(f"Command failed: {error}", kind="error"))
        if self.app.event_trace is not None:
            self.app.event_trace.write("extension_command", {"status": status})
        return True

    def _is_registered_extension_command(self, prompt: str) -> bool:
        parse_command = getattr(self.app.session, "_parse_extension_command", None)
        if not callable(parse_command):
            return False
        return parse_command(prompt) is not None

    def _extension_shortcut_context(self) -> dict[str, object]:
        return {
            "ui": _ExtensionShortcutUI(self),
            "mode": "tui",
            "hasUI": True,
            "cwd": str(self.app.cwd),
            "model": self.app.session.model,
            "isIdle": lambda: not self.app.session.is_streaming,
            "abort": self.app.session.agent.abort,
            "hasPendingMessages": lambda: self.app.session.pending_message_count > 0,
            "shutdown": self._request_shutdown,
            "getContextUsage": self.app.session.get_context_usage,
            "compact": self._extension_compact,
            "getSystemPrompt": lambda: self.app.session.system_prompt,
        }

    def _extension_compact(self, options: dict | None = None):
        focus = options.get("customInstructions") if isinstance(options, dict) else None
        try:
            result = self._run_session_command("compact", lambda: self.app.session.compact(focus))
        except Exception as error:  # noqa: BLE001 - preserves the established callback-style compact errors
            if isinstance(options, dict) and callable(options.get("onError")):
                options["onError"](error)
                return None
            raise
        if isinstance(options, dict) and callable(options.get("onComplete")):
            options["onComplete"](result)
        return result

__all__ = (
    'InteractiveExtensions',
    '_ExtensionShortcutUI',
    '_apply_hidden_thinking_label',
    '_autocomplete_trigger_characters',
    '_coerce_extension_component',
    '_create_extension_widget_component',
    '_dispose_extension_widget',
    '_extension_dialog_aborted',
    '_extension_dialog_label',
    '_extension_dialog_secret',
    '_manual_compression_focus',
    '_manual_compression_options',
    '_resolve_extension_select_choice',
    '_set_autocomplete_trigger_characters',
)
