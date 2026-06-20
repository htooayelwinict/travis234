"""Interactive TUI entry loop.

Small Python port of pi's InteractiveMode shape: initialize the UI, render
startup context, accept line-oriented user input, and feed prompts into the
agent session while live agent events update the TUI.
"""

from __future__ import annotations

import inspect
from typing import Callable

from appv22.ai import (
    get_auth_credential,
    get_oauth_providers,
    get_provider_auth_status,
    get_provider_display_name,
    get_providers,
    list_auth_providers,
    login_oauth_provider,
    logout_provider,
    set_auth_credential,
)
from appv22.compaction import estimate_tokens
from appv22.tui.component import (
    Component,
    Container,
    FooterComponent,
    Input,
    SimpleAutocompleteProvider,
    Spacer,
    StatusLine,
    Text,
    _call_autocomplete_method,
    _settle_autocomplete_result,
)
from appv22.tui.interactive import (
    AssistantMessageComponent,
    BashExecutionComponent,
    message_to_component,
    user_message_to_component,
)


InputFn = Callable[[str], str]


class InteractiveMode:
    """Owns the real user-facing TUI loop for a CodingApp."""

    MAX_WIDGET_LINES = 10

    def __init__(
        self,
        app,
        *,
        input_fn: InputFn | None = None,
        prompt_label: str = "appv22> ",
    ) -> None:
        self.app = app
        self.tui = app.tui
        self.input_fn = input_fn or input
        self.prompt_label = prompt_label
        self.history = Container()
        self.status = StatusLine("Idle")
        self.default_working_message = "Idle"
        self.default_hidden_thinking_label = "Thinking..."
        self.hidden_thinking_label = self.default_hidden_thinking_label
        self.hide_thinking_block = False
        self.editor_text = ""
        self.active_editor: Input | None = None
        self.extension_statuses: dict[str, str] = {}
        self.extension_widgets_above: dict[str, Component] = {}
        self.extension_widgets_below: dict[str, Component] = {}
        self._terminal_input_listeners: list[Callable[[str], object]] = []
        self.autocomplete_provider_wrappers: list[Callable[[object], object]] = []
        self.autocomplete_provider: object | None = None
        self.built_in_header = Text(self._startup_text())
        self.header_container = Container([self.built_in_header, Spacer(1)])
        self.custom_header: object | None = None
        self.widget_container_above = Container()
        self.editor_container = Container()
        self.widget_container_below = Container()
        self.footer = FooterComponent(
            cwd=str(app.cwd),
            model=app.session.model.name,
            thinking_level=app.session.thinking_level,
            extension_statuses=self.extension_statuses,
        )
        self.footer_container = Container([self.footer])
        self.footer_data_provider = _ExtensionFooterDataProvider(self)
        self.custom_footer: object | None = None
        if hasattr(app, "renderer") and hasattr(app.renderer, "set_output_container"):
            app.renderer.set_output_container(self.history)
        if hasattr(app, "renderer") and hasattr(app.renderer, "set_hidden_thinking_label"):
            app.renderer.set_hidden_thinking_label(self.hidden_thinking_label)
        if hasattr(app, "renderer") and hasattr(app.renderer, "set_hide_thinking_block"):
            app.renderer.set_hide_thinking_block(self.hide_thinking_block)
        self._initialized = False
        self._history_populated = False
        self._shutdown_requested = False
        self.setup_autocomplete_provider()

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
        self._refresh_footer()
        self.tui.request_render(force=True)
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

    def create_base_autocomplete_provider(self) -> SimpleAutocompleteProvider:
        commands = [
            {"name": "compact", "description": "Compress conversation context"},
            {"name": "compress", "description": "Compress conversation context"},
            {"name": "exit", "description": "Exit the interactive session"},
            {"name": "login", "description": "Configure provider authentication"},
            {"name": "logout", "description": "Remove provider authentication"},
            {"name": "quit", "description": "Exit the interactive session"},
        ]
        runner = getattr(self.app.session, "extension_runner", None)
        if runner is not None and hasattr(runner, "get_all_registered_commands"):
            for command in runner.get_all_registered_commands():
                command_info = {"name": command.name, "description": command.description}
                get_argument_completions = getattr(command, "get_argument_completions", None)
                if callable(get_argument_completions):
                    command_info["getArgumentCompletions"] = get_argument_completions
                commands.append(command_info)
        return SimpleAutocompleteProvider(commands)

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

    def run(self) -> int:
        self.init()
        while True:
            submitted: list[str] = []
            prompt_component = Input(value=self.editor_text, prompt=self.prompt_label, on_submit=submitted.append)
            prompt_component.set_autocomplete_provider(self.autocomplete_provider)
            self.active_editor = prompt_component
            prompt_component.focused = True
            self.editor_container.add(prompt_component)
            self.tui.request_render()
            try:
                prompt_text = self.input_fn("")
            except EOFError:
                return 0
            dispatch_result = self._dispatch_terminal_input(prompt_text)
            if dispatch_result[0]:
                prompt_component.focused = False
                self.editor_container.remove(prompt_component)
                self.editor_text = prompt_component.get_value()
                self.active_editor = None
                self.tui.request_render()
                continue
            prompt_text = dispatch_result[1]

            prompt_component.handle_input(f"{prompt_text}\r")
            prompt = (submitted[0] if submitted else prompt_component.get_value()).strip()
            prompt_component.focused = False
            self.editor_container.remove(prompt_component)
            self.active_editor = None
            if self._dispatch_extension_shortcut(prompt):
                if self._shutdown_requested:
                    self.status.set_message("Exiting")
                    self._refresh_footer()
                    self.tui.request_render()
                    return 0
                self._refresh_footer()
                self.tui.request_render()
                continue
            self.editor_text = ""
            if prompt:
                self.history.add(user_message_to_component(prompt))
            else:
                self.history.add(Text(""))
            self.tui.request_render()

            if prompt in {"/exit", "/quit", "exit", "quit"}:
                self.status.set_message("Exiting")
                self._refresh_footer()
                self.tui.request_render()
                return 0
            if not prompt:
                continue
            bash_command = _parse_bash_command(prompt)
            if bash_command:
                self._run_bash_command(bash_command[0], exclude_from_context=bash_command[1])
                continue
            if _is_manual_compression_command(prompt):
                self._run_manual_compress(prompt)
                continue
            auth_command = _parse_auth_command(prompt)
            if auth_command:
                self._run_auth_command(auth_command[0], auth_command[1])
                continue
            self.status.set_message("Running")
            before_compressions = self.app.compaction.compressor.compression_count
            before_tokens = estimate_tokens(self.app.messages)
            self._refresh_footer()
            self.tui.request_render()
            self.app.run_turn(prompt)
            self._render_auto_compaction_notice(before_compressions, before_tokens)
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()

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

    def _request_shutdown(self) -> None:
        self._shutdown_requested = True

    def _extension_compact(self, options: dict | None = None):
        focus = options.get("customInstructions") if isinstance(options, dict) else None
        try:
            result = self.app.session.compact(focus)
        except Exception as error:  # noqa: BLE001 - mirrors Pi callback-style compact errors
            if isinstance(options, dict) and callable(options.get("onError")):
                options["onError"](error)
                return None
            raise
        if isinstance(options, dict) and callable(options.get("onComplete")):
            options["onComplete"](result)
        return result

    def _startup_text(self) -> str:
        cwd = str(self.app.cwd).replace("\\", "/")
        return (
            "appv22 pi+hermes TUI\n"
            "Current working directory: "
            f"{cwd}\n"
            "Type /exit or /quit to leave."
        )

    def _run_manual_compress(self, prompt: str) -> None:
        focus = _manual_compression_focus(prompt)
        self.status.set_message("Compressing")
        self._refresh_footer()
        self.tui.request_render()

        status = self.app.compaction.compress_manual_with_status(
            self.app.messages,
            focus=focus,
        )
        self.app.session.agent.state.messages = status.messages
        self.history.add(StatusLine(status.headline, kind="compact"))
        self.history.add(Text(status.token_line))
        if status.note:
            self.history.add(StatusLine(status.note, kind="note"))
        if status.warning:
            self.history.add(StatusLine(status.warning, kind="warning"))
        if status.info:
            self.history.add(StatusLine(status.info, kind="info"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _run_auth_command(self, command: str, provider_query: str | None) -> None:
        if command == "login":
            self._run_login(provider_query)
        else:
            self._run_logout(provider_query)

    def _run_login(self, provider_query: str | None) -> None:
        if provider_query:
            self._show_status("Usage: /login", kind="error")
            return
        subscription_label = "Use a subscription"
        api_key_label = "Use an API key"
        selected = self.prompt_extension_select(
            "Select authentication method:",
            (subscription_label, api_key_label),
            kind="auth",
        )
        if selected == subscription_label:
            self._run_oauth_login(None)
        elif selected == api_key_label:
            self._run_api_key_login(None)

    def _run_oauth_login(self, provider_query: str | None) -> None:
        provider = self._select_oauth_provider(
            "Select provider to configure:",
            _oauth_provider_options(),
            provider_query,
            empty_message="No subscription providers available.",
        )
        if provider is None:
            return
        try:
            login_oauth_provider(provider["id"], self._oauth_login_callbacks())
        except Exception as error:  # noqa: BLE001 - local auth command should render errors, not crash the TUI
            self._show_status(f"Failed to login to {provider['name']}: {error}", kind="error")
            return
        self._show_status(f"Logged in to {provider['name']}", kind="auth")
        self._refresh_footer()
        self.tui.request_render()

    def _run_api_key_login(self, provider_query: str | None) -> None:
        provider = self._select_oauth_provider(
            "Select provider to configure:",
            _api_key_provider_options(),
            provider_query,
            empty_message="No API key providers available.",
        )
        if provider is None:
            return
        api_key = self.prompt_extension_input("Enter API key")
        if not api_key or not api_key.strip():
            self._show_status(f"Failed to save API key for {provider['name']}: API key cannot be empty.", kind="error")
            return
        set_auth_credential(provider["id"], {"type": "api_key", "key": api_key.strip()})
        self._show_status(f"Saved API key for {provider['name']}", kind="auth")
        self._refresh_footer()
        self.tui.request_render()

    def _run_logout(self, provider_query: str | None) -> None:
        provider = self._select_oauth_provider(
            "Select provider to logout:",
            _stored_auth_provider_options(),
            provider_query,
            empty_message=(
                "No stored credentials to remove. /logout only removes credentials saved by /login; "
                "environment variables and models.json config are unchanged."
            ),
        )
        if provider is None:
            return
        try:
            logout_provider(provider["id"])
        except Exception as error:  # noqa: BLE001
            self._show_status(f"Logout failed: {error}", kind="error")
            return
        if provider.get("authType") == "oauth":
            message = f"Logged out of {provider['name']}"
        else:
            message = (
                f"Removed stored API key for {provider['name']}. "
                "Environment variables and models.json config are unchanged."
            )
        self._show_status(message, kind="auth")
        self._refresh_footer()
        self.tui.request_render()

    def _select_oauth_provider(
        self,
        title: str,
        providers: list[dict[str, str]],
        provider_query: str | None,
        *,
        empty_message: str,
    ) -> dict[str, str] | None:
        if not providers:
            self._show_status(empty_message, kind="auth")
            return None
        if provider_query:
            matched = _match_oauth_provider(providers, provider_query)
            if matched is not None:
                return matched
            self._show_status(f"Unknown provider: {provider_query}", kind="error")
            return None
        labels = [provider["name"] for provider in providers]
        selected = self.prompt_extension_select(title, labels, kind="auth")
        if selected is None:
            return None
        return next((provider for provider in providers if provider["name"] == selected), None)

    def _oauth_login_callbacks(self) -> dict[str, object]:
        return {
            "onAuth": self._show_oauth_auth,
            "onDeviceCode": self._show_oauth_device_code,
            "onPrompt": lambda prompt: self.prompt_extension_input(
                str(prompt.get("message", "OAuth prompt")) if isinstance(prompt, dict) else str(prompt),
                str(prompt.get("placeholder")) if isinstance(prompt, dict) and prompt.get("placeholder") else None,
            )
            or "",
            "onProgress": lambda message: self._show_status(str(message), kind="auth"),
            "onManualCodeInput": lambda: self.prompt_extension_input("Paste redirect URL below, or complete login in browser:") or "",
            "onSelect": self._show_oauth_select,
            "signal": {"aborted": False},
        }

    def _show_oauth_auth(self, info: object) -> None:
        if isinstance(info, dict):
            url = str(info.get("url", ""))
            instructions = info.get("instructions")
            if instructions:
                self._show_status(str(instructions), kind="auth")
            if url:
                self.history.add(Text(url))
                self.tui.request_render()
            return
        self._show_status(str(info), kind="auth")

    def _show_oauth_device_code(self, info: object) -> None:
        if isinstance(info, dict):
            user_code = info.get("userCode", info.get("user_code", ""))
            uri = info.get("verificationUri", info.get("verification_uri", ""))
            self._show_status(f"Device code: {user_code}", kind="auth")
            if uri:
                self.history.add(Text(str(uri)))
                self.tui.request_render()
            return
        self._show_status(str(info), kind="auth")

    def _show_oauth_select(self, prompt: object) -> str | None:
        if not isinstance(prompt, dict):
            return None
        options = prompt.get("options")
        if not isinstance(options, list):
            return None
        choices = [
            str(option.get("label", option.get("id", "")))
            for option in options
            if isinstance(option, dict)
        ]
        selected = self.prompt_extension_select(str(prompt.get("message", "Select option:")), choices, kind="auth")
        if selected is None:
            return None
        for option in options:
            if isinstance(option, dict) and str(option.get("label", option.get("id", ""))) == selected:
                return str(option.get("id", selected))
        return selected

    def _show_status(self, message: str, *, kind: str = "status") -> None:
        self.history.add(StatusLine(message, kind=kind))
        self.tui.request_render()

    def _run_bash_command(self, command: str, *, exclude_from_context: bool) -> None:
        extension_result = self.app.session.extension_runner.emit_user_bash(
            {
                "type": "user_bash",
                "command": command,
                "excludeFromContext": exclude_from_context,
                "cwd": str(self.app.cwd),
            }
        )
        component = BashExecutionComponent(command, exclude_from_context=exclude_from_context)
        self.history.add(component)
        self.status.set_message("Running bash")
        self._refresh_footer()
        self.tui.request_render()

        if isinstance(extension_result, dict) and extension_result.get("result") is not None:
            result = extension_result["result"]
            if getattr(result, "output", None):
                component.append_output(result.output)
            component.set_complete(result.exit_code, result.cancelled, result.truncated, result.full_output_path)
            self.app.session.record_bash_result(
                command,
                result,
                {"excludeFromContext": exclude_from_context},
            )
            self.status.set_message("Idle")
            self._refresh_footer()
            self.tui.request_render()
            return

        def on_chunk(chunk: str) -> None:
            component.append_output(chunk)
            self.tui.request_render()

        try:
            options = {"excludeFromContext": exclude_from_context}
            if isinstance(extension_result, dict) and extension_result.get("operations") is not None:
                options["operations"] = extension_result["operations"]
            result = self.app.session.execute_bash(
                command,
                on_chunk,
                options,
            )
            component.set_complete(result.exit_code, result.cancelled, result.truncated, result.full_output_path)
        except Exception as error:  # noqa: BLE001 - user bash errors are rendered in the TUI
            component.set_complete(None, False)
            self.history.add(StatusLine(f"Bash command failed: {error}", kind="error"))
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _render_auto_compaction_notice(self, before_compressions: int, before_tokens: int) -> None:
        after_compressions = self.app.compaction.compressor.compression_count
        if after_compressions <= before_compressions:
            return
        after_tokens = estimate_tokens(self.app.messages)
        self.history.add(
            StatusLine(
                f"Context compacted: ~{before_tokens:,} -> ~{after_tokens:,} tokens",
                kind="compact",
            )
        )

    def _refresh_footer(self) -> None:
        self.footer.model = self.app.session.model.name
        self.footer.thinking_level = self.app.session.thinking_level
        self.footer.pending = len(self.app.session.agent.state.pending_tool_calls)
        self.footer.context_tokens = estimate_tokens(self.app.messages)
        self.footer.context_threshold = self.app.compaction.compressor.threshold_tokens
        self.footer.compression_count = self.app.compaction.compressor.compression_count
        self.footer.extension_statuses = dict(self.extension_statuses)

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
        clean_title = _extension_dialog_label(title)
        prompt = f"{clean_title} ({placeholder}): " if placeholder else f"{clean_title}: "
        self.history.add(StatusLine(clean_title, kind="input"))
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
        self.history.add(StatusLine(clean_title, kind=kind))
        for index, choice in enumerate(normalized_choices, start=1):
            self.history.add(Text(f"{index}. {choice}"))
        self.tui.request_render()
        try:
            value = self.input_fn(f"{clean_title} [1-{len(normalized_choices)}]: ")
        except EOFError:
            return None
        if value is None:
            return None
        selected = _resolve_extension_select_choice(str(value), normalized_choices)
        if selected is not None:
            self.history.add(Text(selected))
            self.tui.request_render()
        return selected

    def prompt_extension_confirm(
        self,
        title: str,
        message: str,
        options: dict | None = None,
    ) -> bool:
        label = _extension_dialog_label(f"{title}\n{message}")
        return self.prompt_extension_select(label, ("Yes", "No"), options, kind="confirm") == "Yes"

    def prompt_extension_custom(self, factory: Callable[..., object], options: dict | None = None) -> object:
        previous_children = list(self.editor_container.children)
        saved_editor = self.active_editor
        saved_text = saved_editor.get_value() if saved_editor is not None else self.editor_text
        result: dict[str, object] = {"closed": False, "value": None}
        component_holder: dict[str, object] = {"component": None}

        def restore_editor() -> None:
            self.editor_container.clear()
            if saved_editor is not None:
                saved_editor.set_value(saved_text)
                self.active_editor = saved_editor
            else:
                self.editor_text = saved_text
            for child in previous_children:
                self.editor_container.add(child)
            self.tui.request_render()

        def close(value: object = None) -> None:
            if result["closed"]:
                return
            result["closed"] = True
            result["value"] = value
            restore_editor()
            _dispose_extension_widget(component_holder["component"])

        try:
            component = factory(self.tui, None, None, close)
            if inspect.isawaitable(component):
                import asyncio

                component = asyncio.run(component)
        except Exception:
            restore_editor()
            raise

        component_holder["component"] = component
        if result["closed"]:
            _dispose_extension_widget(component)
            return result["value"]

        self.editor_container.clear()
        self.editor_container.add(_coerce_extension_component(component))
        self.tui.request_render()

        while not result["closed"]:
            try:
                data = self.input_fn("")
            except EOFError:
                close(None)
                break
            handle_result = getattr(component, "handle_input", lambda _data: None)(data)
            if inspect.isawaitable(handle_result):
                import asyncio

                asyncio.run(handle_result)
            if not result["closed"]:
                self.tui.request_render()
        return result["value"]

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


class _ExtensionShortcutUI:
    def __init__(self, mode: InteractiveMode) -> None:
        self._mode = mode

    def notify(self, message: str) -> None:
        self._mode.history.add(Text(str(message)))

    def show_error(self, message: str) -> None:
        self._mode.history.add(Text(f"error: {message}"))

    showError = show_error

    def set_status(self, key: str, text: str | None) -> None:
        self._mode.set_extension_status(key, text)

    setStatus = set_status

    def set_working_message(self, message: str | None = None) -> None:
        self._mode.set_working_message(message)

    setWorkingMessage = set_working_message

    def set_working_visible(self, visible: bool) -> None:
        self._mode.set_working_visible(visible)

    setWorkingVisible = set_working_visible

    def set_working_indicator(self, options: dict | None = None) -> None:
        self._mode.set_working_indicator(options)

    setWorkingIndicator = set_working_indicator

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

    onTerminalInput = on_terminal_input

    def set_hidden_thinking_label(self, label: str | None = None) -> None:
        self._mode.set_hidden_thinking_label(label)

    setHiddenThinkingLabel = set_hidden_thinking_label

    def set_title(self, title: str) -> None:
        self._mode.set_terminal_title(title)

    setTitle = set_title

    def set_widget(self, key: str, content: object = None, options: dict | None = None) -> None:
        self._mode.set_extension_widget(key, content, options)

    setWidget = set_widget

    def set_footer(self, factory: Callable | None = None) -> None:
        self._mode.set_extension_footer(factory)

    setFooter = set_footer

    def set_header(self, factory: Callable | None = None) -> None:
        self._mode.set_extension_header(factory)

    setHeader = set_header

    def set_editor_text(self, text: str) -> None:
        self._mode.set_editor_text(text)

    setEditorText = set_editor_text

    def get_editor_text(self) -> str:
        return self._mode.get_editor_text()

    getEditorText = get_editor_text

    def paste_to_editor(self, text: str) -> None:
        self._mode.paste_to_editor(str(text))

    pasteToEditor = paste_to_editor

    def editor(self, title: str, prefill: str | None = None) -> str | None:
        return self._mode.prompt_extension_editor(title, prefill)

    def custom(self, factory: Callable[..., object], options: dict | None = None) -> object:
        return self._mode.prompt_extension_custom(factory, options)

    def add_autocomplete_provider(self, factory: Callable[[object], object]) -> None:
        self._mode.add_autocomplete_provider(factory)

    addAutocompleteProvider = add_autocomplete_provider


def _is_manual_compression_command(prompt: str) -> bool:
    return prompt in {"/compress", "/compact"} or prompt.startswith("/compress ") or prompt.startswith("/compact ")


def _parse_auth_command(prompt: str) -> tuple[str, str | None] | None:
    if prompt == "/login":
        return "login", None
    if prompt == "/logout":
        return "logout", None
    return None


def _oauth_provider_options() -> list[dict[str, str]]:
    providers = [
        {"id": str(provider.get("id", "")), "name": str(provider.get("name") or provider.get("id", ""))}
        for provider in get_oauth_providers()
        if provider.get("id")
    ]
    return sorted(providers, key=lambda provider: provider["name"].lower())


def _api_key_provider_options() -> list[dict[str, str]]:
    oauth_provider_ids = {provider["id"] for provider in _oauth_provider_options()}
    providers = [
        {"id": provider_id, "name": _provider_display_name(provider_id)}
        for provider_id in get_providers()
        if provider_id not in oauth_provider_ids
    ]
    return sorted(providers, key=lambda provider: provider["name"].lower())


def _stored_auth_provider_options() -> list[dict[str, str]]:
    providers: list[dict[str, str]] = []
    for provider_id in list_auth_providers():
        credential = get_auth_credential(provider_id)
        if not credential:
            continue
        providers.append(
            {
                "id": provider_id,
                "name": _provider_display_name(provider_id),
                "authType": str(credential.get("type", "")),
            }
        )
    return sorted(providers, key=lambda provider: provider["name"].lower())


def _provider_display_name(provider_id: str) -> str:
    return get_provider_display_name(provider_id)


def _match_oauth_provider(providers: list[dict[str, str]], query: str) -> dict[str, str] | None:
    normalized_query = query.strip().lower()
    for provider in providers:
        if normalized_query in {provider["id"].lower(), provider["name"].lower()}:
            return provider
    return None


def _parse_bash_command(prompt: str) -> tuple[str, bool] | None:
    if not prompt.startswith("!"):
        return None
    excluded = prompt.startswith("!!")
    command = prompt[2:].strip() if excluded else prompt[1:].strip()
    if not command:
        return None
    return command, excluded


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


class _ExtensionFooterDataProvider:
    def __init__(self, mode: InteractiveMode) -> None:
        self._mode = mode

    def get_git_branch(self) -> None:
        return None

    getGitBranch = get_git_branch

    def get_extension_statuses(self) -> dict[str, str]:
        return dict(self._mode.extension_statuses)

    getExtensionStatuses = get_extension_statuses

    def get_available_provider_count(self) -> int:
        return 1

    getAvailableProviderCount = get_available_provider_count

    def on_branch_change(self, handler: Callable[[], object]):
        def unsubscribe() -> None:
            return None

        return unsubscribe

    onBranchChange = on_branch_change


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
    for command in ("/compress", "/compact"):
        if prompt == command:
            return None
        if prompt.startswith(f"{command} "):
            focus = prompt[len(command) :].strip()
            return focus or None
    return None


def _extension_dialog_aborted(options: dict | None) -> bool:
    if not isinstance(options, dict):
        return False
    signal = options.get("signal")
    if isinstance(signal, dict):
        return bool(signal.get("aborted"))
    return bool(getattr(signal, "aborted", False))


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
