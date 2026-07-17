"""Focused model auth ownership for the TUI."""

from __future__ import annotations

import inspect
import json
import os
import queue
import signal as signal_module
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

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

from travis.tui.interactive_shutdown import OPENROUTER_MODEL_PICKER_LIMIT

def _dedupe_models(models) -> list:
    seen: set[tuple[str, str]] = set()
    result = []
    for model in models:
        key = (str(getattr(model, "provider", "")), str(getattr(model, "id", "")))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(model)
    return result


def _filter_model_candidates(models, query: str | None = None) -> list:
    if query:
        normalized = query.strip().lower()
        models = [
            model
            for model in models
            if normalized in f"{model.provider}/{model.id}".lower()
            or normalized in model.id.lower()
            or normalized in model.name.lower()
        ]
    return list(models[:OPENROUTER_MODEL_PICKER_LIMIT])


def _model_label(model, active_model=None) -> str:
    label = f"{model.provider}/{model.id}"
    if (
        active_model is not None
        and getattr(model, "provider", None) == getattr(active_model, "provider", None)
        and getattr(model, "id", None) == getattr(active_model, "id", None)
    ):
        return f"{label} (current)"
    return label


def _resolve_model_query(query: str, candidates, active_model):
    normalized = query.strip()
    normalized_lower = normalized.lower()
    for model in candidates:
        label = f"{model.provider}/{model.id}"
        if normalized_lower in {label.lower(), model.id.lower(), model.name.lower()}:
            return model
    active_provider = getattr(active_model, "provider", "")
    if not active_provider:
        return None
    if "/" not in normalized:
        return None
    candidate_providers = {getattr(model, "provider", "") for model in candidates}
    model_id = normalized
    provider = active_provider
    if "/" in normalized:
        possible_provider, possible_model_id = normalized.split("/", 1)
        if possible_provider in candidate_providers or possible_provider == active_provider:
            provider = possible_provider
            model_id = possible_model_id
    if provider != active_provider or not model_id:
        return None
    return replace(active_model, id=model_id, name=model_id)


def _match_oauth_provider(providers: list[dict[str, str]], query: str) -> dict[str, str] | None:
    normalized_query = query.strip().lower()
    for provider in providers:
        if normalized_query in {provider["id"].lower(), provider["name"].lower()}:
            return provider
    return None

class InteractiveModelAuth:
    """Owns a focused interactive runtime concern."""

    def _get_model_candidates(self, *, fetch_remote: bool = False, query: str | None = None):
        del fetch_remote
        scoped_models = getattr(self.app.session, "scoped_models", [])
        if scoped_models:
            models = [scoped.model for scoped in scoped_models]
            return _filter_model_candidates(models, query)
        models = self.app.session.model_registry.get_selectable(self.app.session.model)
        return _filter_model_candidates(models, query)

    def _update_available_provider_count(self) -> None:
        providers = {model.provider for model in self._get_model_candidates() if getattr(model, "provider", None)}
        self.footer_data_provider.set_available_provider_count(len(providers))

    def _run_auth_command(self, command: str, provider_query: str | None) -> None:
        if command == "login":
            self._run_login(provider_query)
        else:
            self._run_logout(provider_query)

    def _run_model_command(self, command: str, query: str | None) -> None:
        local_candidates = self._get_model_candidates()
        query = (query or "").strip()
        if query.lower() in {"list", "ls"}:
            command = "list"
            query = ""
        if query.lower() in {"next", "forward"}:
            self._cycle_model("forward")
            return
        if query.lower() in {"previous", "prev", "back", "backward"}:
            self._cycle_model("backward")
            return
        if query:
            model = _resolve_model_query(query, local_candidates, self.app.session.model)
            if model is not None:
                self._switch_model(model)
                return

        self._complete_model_command(command, query)

    def _complete_model_command(
        self,
        command: str,
        query: str,
    ) -> None:
        scoped_models = getattr(self.app.session, "scoped_models", [])
        if scoped_models:
            candidates = [
                scoped.model
                for scoped in scoped_models
                if self.app.session.model_registry.is_selectable(scoped.model)
            ]
        else:
            candidates = self.app.session.model_registry.get_selectable(self.app.session.model)
        candidates = _filter_model_candidates(candidates, query or None)
        if command == "list":
            self._trace_model_picker_ready(len(candidates), query)
            self._show_model_list(candidates)
            return
        if not candidates:
            self._show_status("No models available. Configure TRAVIS234_WORKER_LLM_MODEL or models.json.", kind="error")
            return
        labels = [_model_label(model, self.app.session.model) for model in candidates]
        self._pending_model_picker_trace = (len(candidates), query)
        selected = self.prompt_extension_select("Select model:", labels, kind="model")
        if selected is None:
            return
        label_to_model = dict(zip(labels, candidates))
        model = label_to_model.get(selected)
        if model is not None:
            self._switch_model(model)

    def _show_model_list(self, models) -> None:
        if not models:
            self._show_status("No models available. Configure TRAVIS234_WORKER_LLM_MODEL or models.json.", kind="error")
            return
        self.history.add(StatusLine("Available models", kind="model"))
        for model in models:
            self.history.add(Text(_model_label(model, self.app.session.model)))
        self.tui.request_render()

    def _cycle_model(self, direction: str) -> None:
        result = self._run_session_command("cycle-model", lambda: self.app.session.cycle_model(direction))
        if result is None:
            self._show_status("No alternate models available. Configure --models or models.json to enable switching.", kind="model")
            return
        self._show_model_switched(result.model)

    def _switch_model(self, model) -> None:
        self._run_session_command("set-model", lambda: self.app.session.set_model(model))
        self._show_model_switched(model)

    def _show_model_switched(self, model) -> None:
        if self.app.event_trace is not None:
            self.app.event_trace.write(
                "model_selected",
                {"provider": model.provider, "model": model.id},
            )
        self._show_status(f"Switched model to {model.provider}/{model.id}", kind="model")
        self._refresh_generation_param_state()
        self._update_available_provider_count()
        self._refresh_footer()
        self.tui.request_render()

    def _trace_model_picker_ready(self, count: int, query: str) -> None:
        if self.app.event_trace is not None:
            self.app.event_trace.write(
                "model_picker_ready",
                {"model_count": count, "picker_query": query},
            )

    def _emit_pending_model_picker_trace(self) -> None:
        pending = self._pending_model_picker_trace
        if pending is None:
            return
        self._pending_model_picker_trace = None
        self._trace_model_picker_ready(*pending)

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
            self._oauth_provider_options(),
            provider_query,
            empty_message="No subscription providers available.",
        )
        if provider is None:
            return
        try:
            self.app.session.model_registry.login_oauth_provider(
                provider["id"],
                self._oauth_login_callbacks(),
            )
        except Exception as error:  # noqa: BLE001 - local auth command should render errors, not crash the TUI
            self._show_status(f"Failed to login to {provider['name']}: {error}", kind="error")
            return
        self._show_status(f"Logged in to {provider['name']}", kind="auth")
        self._refresh_footer()
        self.tui.request_render()

    def _run_api_key_login(self, provider_query: str | None) -> None:
        provider = self._select_oauth_provider(
            "Select provider to configure:",
            self._api_key_provider_options(),
            provider_query,
            empty_message="No API key providers available.",
        )
        if provider is None:
            return
        api_key = self.prompt_extension_input("Enter API key", options={"secret": True})
        if not api_key or not api_key.strip():
            self._show_status(f"Failed to save API key for {provider['name']}: API key cannot be empty.", kind="error")
            return
        env: dict[str, str] = {}
        cloudflare_fields = {
            "cloudflare-workers-ai": (
                ("CLOUDFLARE_ACCOUNT_ID", "Enter Cloudflare account ID"),
            ),
            "cloudflare-ai-gateway": (
                ("CLOUDFLARE_ACCOUNT_ID", "Enter Cloudflare account ID"),
                ("CLOUDFLARE_GATEWAY_ID", "Enter Cloudflare gateway ID"),
            ),
        }.get(provider["id"], ())
        for variable, prompt in cloudflare_fields:
            value = self.prompt_extension_input(prompt)
            if not value or not value.strip():
                self._show_status(
                    f"Failed to save API key for {provider['name']}: {variable} cannot be empty.",
                    kind="error",
                )
                return
            env[variable] = value.strip()
        credential: dict[str, object] = {"type": "api_key", "key": api_key.strip()}
        if env:
            credential["env"] = env
        self._run_session_command(
            "set-auth",
            lambda: self.app.session.model_registry.set_auth_credential(provider["id"], credential),
        )
        self._show_status(f"Saved API key for {provider['name']}", kind="auth")
        self._refresh_footer()
        self.tui.request_render()

    def _run_logout(self, provider_query: str | None) -> None:
        provider = self._select_oauth_provider(
            "Select provider to logout:",
            self._stored_auth_provider_options(),
            provider_query,
            empty_message=(
                "No stored credentials to remove. /logout only removes credentials saved by /login; "
                "environment variables and models.json config are unchanged."
            ),
        )
        if provider is None:
            return
        try:
            self._run_session_command(
                "logout",
                lambda: self.app.session.model_registry.logout_provider(provider["id"]),
            )
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

    def _api_key_provider_options(self) -> list[dict[str, str]]:
        registry = self.app.session.model_registry
        oauth_provider_ids = {provider["id"] for provider in self._oauth_provider_options()}
        providers = [
            {"id": provider_id, "name": registry.get_provider_display_name(provider_id)}
            for provider_id in registry.get_api_key_providers()
            if provider_id not in oauth_provider_ids
        ]
        return sorted(providers, key=lambda provider: provider["name"].lower())

    def _oauth_provider_options(self) -> list[dict[str, str]]:
        providers = [
            {"id": str(provider.get("id", "")), "name": str(provider.get("name") or provider.get("id", ""))}
            for provider in self.app.session.model_registry.get_oauth_providers()
            if provider.get("id")
        ]
        return sorted(providers, key=lambda provider: provider["name"].lower())

    def _stored_auth_provider_options(self) -> list[dict[str, str]]:
        registry = self.app.session.model_registry
        providers: list[dict[str, str]] = []
        for provider_id in self.app.session.auth_storage.list():
            credential = self.app.session.auth_storage.get(provider_id)
            if not credential:
                continue
            providers.append(
                {
                    "id": provider_id,
                    "name": registry.get_provider_display_name(provider_id),
                    "authType": str(credential.get("type", "")),
                }
            )
        return sorted(providers, key=lambda provider: provider["name"].lower())

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

__all__ = (
    'InteractiveModelAuth',
    '_dedupe_models',
    '_filter_model_candidates',
    '_match_oauth_provider',
    '_model_label',
    '_resolve_model_query',
)
