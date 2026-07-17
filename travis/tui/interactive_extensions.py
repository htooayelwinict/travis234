"""Focused extensions ownership for the TUI."""

from __future__ import annotations

import inspect
import json
import os
import queue
import signal as signal_module
import shlex
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
from travis.tui.motion import MotionState


_PACKAGE_COMMANDS = frozenset({"/install", "/remove", "/update", "/packages"})


class _ExtensionShortcutUI:
    def __init__(self, mode: InteractiveMode) -> None:
        self._mode = mode

    def notify(self, message: str) -> None:
        self._mode.history.add(Text(str(message)))

    def show_error(self, message: str) -> None:
        self._mode.history.add(Text(f"error: {message}"))

    def set_theme(self, name: str) -> dict[str, object]:
        try:
            self._mode.theme_registry.select(str(name))
        except ValueError as error:
            return {"success": False, "error": str(error)}
        settings = getattr(self._mode.app.session, "settings_manager", None)
        persist = getattr(settings, "set_theme", None) or getattr(settings, "setTheme", None)
        if callable(persist):
            persist(str(name))
        self._mode.tui.request_render()
        return {"success": True}

    def __getattr__(self, name: str) -> object:
        if name == "setTheme":  # Pi extension API spelling.
            return self.set_theme
        raise AttributeError(name)


    def set_status(self, key: str, text: str | None, options: dict | None = None) -> None:
        self._mode.set_extension_status(key, text, options)


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

    def _extension_bindings(self) -> dict[str, object]:
        def report_error(error: dict[str, object]) -> None:
            path = str(error.get("extensionPath") or "<extension>")
            message = str(error.get("error") or "unknown extension error")
            self.history.add(StatusLine(f"Extension error ({path}): {message}", kind="error"))
            self.tui.request_render()

        return {
            "uiContext": _ExtensionShortcutUI(self),
            "mode": "tui",
            "abortHandler": self.app.session.agent.abort,
            "shutdownHandler": self._request_shutdown,
            "onError": report_error,
            "commandContextActions": {
                "waitForIdle": self.app.session.agent.wait_for_idle,
                "newSession": lambda options=None: self.app.session_runtime.new_session(options),
                "fork": lambda entry_id, options=None: self.app.session_runtime.fork(entry_id, options),
                "navigateTree": lambda target_id, options=None: self.app.session.navigate_tree(target_id, options),
                "switchSession": lambda session_path, options=None: self.app.session_runtime.switch_session(
                    session_path,
                    options,
                ),
                "reload": self._run_reload_command,
            },
        }

    def _reset_extension_ui(self) -> None:
        self._terminal_input_listeners.clear()
        self.set_extension_footer(None)
        self.set_extension_header(None)
        for component in [*self.extension_widgets_above.values(), *self.extension_widgets_below.values()]:
            _dispose_extension_widget(component)
        self.extension_widgets_above.clear()
        self.extension_widgets_below.clear()
        self._render_widgets()
        self.extension_statuses.clear()
        self.extension_status_states.clear()
        self.extension_working_active = False
        self._refresh_extension_motion_signal()
        self.autocomplete_provider_wrappers.clear()
        self.set_working_message()
        self.set_working_visible(True)
        self.set_working_indicator()
        self.set_hidden_thinking_label()
        self.set_terminal_title("Travis234")
        self.setup_autocomplete_provider()

    def _run_reload_command(self) -> None:
        if self.app.session.is_streaming:
            self.history.add(StatusLine("Wait for the current response to finish before reloading.", kind="warning"))
            return
        if self.app.session.is_compacting:
            self.history.add(StatusLine("Wait for compaction to finish before reloading.", kind="warning"))
            return
        self._reset_extension_ui()
        self._set_motion_signal("maintenance", MotionState.MAINTENANCE)
        self.status.set_message("Reloading extensions")
        try:
            self._run_reload_body()
        finally:
            self._clear_motion_signal("maintenance")

    def _run_reload_body(self) -> None:
        try:
            self.app.session.reload()
            self.setup_autocomplete_provider()
        except Exception as error:  # noqa: BLE001 - reload failures render without ending the TUI.
            self.history.add(StatusLine(f"Extension reload failed: {error}", kind="error"))
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()
            return
        loader = self.app.session.resource_loader
        theme_fallback = self.theme_registry.reload(
            [theme for theme in loader.get_themes()["themes"] if hasattr(theme, "name")]
        )
        if theme_fallback:
            self.history.add(StatusLine(theme_fallback, kind="warning"))

        extension_errors = list(loader.get_extensions().get("errors", []))
        diagnostics_by_kind = {
            "skills": list(loader.get_skills().get("diagnostics", [])),
            "prompts": list(loader.get_prompts().get("diagnostics", [])),
            "themes": list(loader.get_themes().get("diagnostics", [])),
        }
        counts = {
            "extensions": len(extension_errors),
            **{kind: len(diagnostics) for kind, diagnostics in diagnostics_by_kind.items()},
        }
        total_diagnostics = sum(counts.values())
        status_kind = "warning" if total_diagnostics else "success"
        self.history.add(
            StatusLine(
                "Extensions reloaded "
                f"(extensions: {counts['extensions']}; skills: {counts['skills']}; "
                f"prompts: {counts['prompts']}; themes: {counts['themes']})",
                kind=status_kind,
            )
        )
        for error in extension_errors:
            self.history.add(Text(f"extension: {error.get('path', '<extension>')}: {error.get('error', 'unknown error')}"))
        for kind, diagnostics in diagnostics_by_kind.items():
            for diagnostic in diagnostics:
                self.history.add(
                    Text(
                        f"{kind[:-1]}: {getattr(diagnostic, 'path', '<resource>')}: "
                        f"{getattr(diagnostic, 'message', 'unknown diagnostic')}"
                    )
                )
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _run_package_command(self, prompt: str) -> bool:
        prompt_parts = prompt.split(maxsplit=1)
        first_token = prompt_parts[0] if prompt_parts else ""
        if first_token not in _PACKAGE_COMMANDS:
            return False
        try:
            parts = shlex.split(prompt)
        except ValueError as error:
            self.history.add(StatusLine(f"Invalid package command: {error}", kind="error"))
            return True
        if not parts or parts[0] not in _PACKAGE_COMMANDS:
            return False
        action = parts[0][1:]
        local = "--local" in parts[1:]
        arguments = [part for part in parts[1:] if part != "--local"]
        scope = "project" if local else "global"
        manager = self.app.session.resource_loader.package_manager

        if action == "packages":
            if arguments:
                self.history.add(StatusLine("Usage: /packages [--local]", kind="error"))
                return True
            installed = manager.list_installed(scope=scope)
            if not installed:
                self.history.add(StatusLine(f"No {scope} packages installed.", kind="warning"))
            else:
                self.history.add(StatusLine(f"Installed {scope} packages", kind="success"))
                for item in installed:
                    version = f" ({item.version})" if item.version else ""
                    self.history.add(Text(f"{item.source.raw}{version}: {item.install_path}"))
            self.tui.request_render()
            return True

        if action in {"install", "remove"} and len(arguments) != 1:
            self.history.add(StatusLine(f"Usage: /{action} <source> [--local]", kind="error"))
            return True
        if action == "update" and len(arguments) > 1:
            self.history.add(StatusLine("Usage: /update [source] [--local]", kind="error"))
            return True
        source = arguments[0] if arguments else None
        title = f"{action.capitalize()} package"
        target = source or f"all {scope} packages"
        if not self.prompt_extension_confirm(title, f"{action.capitalize()} {target}?"):
            self.history.add(StatusLine(f"Package {action} cancelled.", kind="warning"))
            self.tui.request_render()
            return True

        self.status.set_message(f"{action.capitalize()}ing package")
        self._set_motion_signal("package", MotionState.MAINTENANCE)
        self._refresh_footer()
        self.tui.request_render()
        try:
            if action == "install":
                result = self._run_session_command(
                    "package-install",
                    lambda: manager.install(source, scope=scope),
                )
                self.history.add(StatusLine(f"Installed package: {result.source.raw}", kind="success"))
                changed = True
            elif action == "remove":
                changed = self._run_session_command(
                    "package-remove",
                    lambda: manager.remove(source, scope=scope),
                )
                if not changed:
                    raise KeyError(f"Installed package not found: {source}")
                self.history.add(StatusLine(f"Removed package: {source}", kind="success"))
            else:
                results = self._run_session_command(
                    "package-update",
                    lambda: manager.update(source, scope=scope),
                )
                changed = bool(results)
                suffix = "" if len(results) == 1 else "s"
                self.history.add(StatusLine(f"Updated {len(results)} package{suffix}.", kind="success"))
        except Exception as error:  # noqa: BLE001 - package errors render without ending the TUI.
            detail = error.args[0] if isinstance(error, KeyError) and error.args else str(error)
            self.history.add(StatusLine(f"Package {action} failed: {detail}", kind="error"))
            self._clear_motion_signal("package")
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()
            return True
        if changed:
            try:
                self._run_reload_command()
            finally:
                self._clear_motion_signal("package")
        else:
            self._clear_motion_signal("package")
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()
        return True

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
