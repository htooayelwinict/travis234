"""Pi-style AgentSession service factory helpers."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from appv231.agent.types import AgentMessage
from appv231.ai.model_resolver import find_initial_model
from appv231.ai.stream import _DEFAULT_API_PROVIDER_REGISTRY
from appv231.ai.types import Context, ImageContent, Message, Model, SimpleStreamOptions, TextContent
from appv231.coding_agent.agent_session import AgentSession, default_convert_to_llm
from appv231.coding_agent.auth_storage import AuthStorage
from appv231.coding_agent.extensions import ExtensionRunner
from appv231.coding_agent.model_registry import ModelRegistry
from appv231.coding_agent.provider_control_plane import ProviderControlPlane
from appv231.coding_agent.resource_loader import DefaultResourceLoader
from appv231.coding_agent.session_store import SessionContextSnapshot, SessionStore
from appv231.coding_agent.settings_manager import SettingsManager
from appv231.coding_agent.tools import create_all_tool_definitions


@dataclass
class CreateAgentSessionResult:
    session: AgentSession
    extensions_result: dict[str, object]
    model_fallback_message: str | None = None

    @property
    def extensionsResult(self) -> dict[str, object]:
        return self.extensions_result

    @property
    def modelFallbackMessage(self) -> str | None:
        return self.model_fallback_message


def create_agent_session_services(options: dict[str, Any]) -> dict[str, Any]:
    cwd = str(Path(str(options.get("cwd", "."))).expanduser().resolve())
    agent_dir = str(Path(str(options.get("agentDir", options.get("agent_dir", Path.home() / ".pi" / "agent")))).expanduser().resolve())
    settings_manager = options.get("settingsManager") or options.get("settings_manager") or SettingsManager.create(
        cwd,
        agent_dir,
    )
    resource_loader = options.get("resourceLoader") or options.get("resource_loader")
    if resource_loader is None:
        resource_loader_options = dict(options.get("resourceLoaderOptions") or options.get("resource_loader_options") or {})
        resource_loader = DefaultResourceLoader(
            cwd=cwd,
            agent_dir=agent_dir,
            settings_manager=settings_manager,
            **resource_loader_options,
        )
        resource_loader.reload(options.get("resourceLoaderReloadOptions") or options.get("resource_loader_reload_options"))
    provider_control_plane = options.get("providerControlPlane") or options.get("provider_control_plane")
    if provider_control_plane is not None and not isinstance(provider_control_plane, ProviderControlPlane):
        raise TypeError("providerControlPlane must be a ProviderControlPlane")
    if provider_control_plane is None:
        auth_storage = options.get("authStorage") or options.get("auth_storage") or AuthStorage.create(
            str(Path(agent_dir) / "auth.json")
        )
        model_registry = options.get("modelRegistry") or options.get("model_registry") or ModelRegistry(
            auth_storage,
            str(Path(agent_dir) / "models.json"),
            _DEFAULT_API_PROVIDER_REGISTRY,
        )
        if model_registry.auth_storage is not auth_storage:
            raise ValueError("modelRegistry and authStorage must share the same AuthStorage")
        provider_control_plane = ProviderControlPlane(
            auth=auth_storage,
            models=model_registry,
            api_providers=model_registry.api_providers,
        )
    else:
        auth_storage = provider_control_plane.auth
        model_registry = provider_control_plane.models
    session_id = options.get("sessionId", options.get("session_id"))
    session_path = options.get("sessionPath", options.get("session_path"))
    if session_path is None:
        session_path, session_id = _new_session_path(cwd, agent_dir, str(session_id) if session_id else None)
    else:
        session_path = str(Path(str(session_path)).expanduser().resolve())
    diagnostics: list[dict[str, object]] = []
    extensions_result = resource_loader.get_extensions()
    runtime = extensions_result.get("runtime")
    if isinstance(runtime, ExtensionRunner):
        diagnostics.extend(_drain_pending_provider_registrations(runtime, model_registry))
        diagnostics.extend(
            _apply_extension_flag_values(
                runtime,
                options.get("extensionFlagValues") or options.get("extension_flag_values"),
            )
        )
    return {
        "cwd": cwd,
        "agentDir": agent_dir,
        "settingsManager": settings_manager,
        "resourceLoader": resource_loader,
        "authStorage": auth_storage,
        "modelRegistry": model_registry,
        "providerControlPlane": provider_control_plane,
        "sessionPath": session_path,
        "sessionId": session_id,
        "diagnostics": diagnostics,
    }


createAgentSessionServices = create_agent_session_services


def create_agent_session(options: Mapping[str, Any] | None = None, **kwargs: Any) -> CreateAgentSessionResult:
    """Pi-style SDK factory: create services, resolve model, and return a session result."""

    if options is None:
        resolved_options: dict[str, Any] = {}
    elif isinstance(options, Mapping):
        resolved_options = dict(options)
    else:
        raise TypeError("create_agent_session options must be a mapping")
    resolved_options.update(kwargs)
    services = resolved_options.get("services")
    if services is None:
        services = create_agent_session_services(resolved_options)
    return create_agent_session_from_services({**resolved_options, "services": services})


createAgentSession = create_agent_session


def create_agent_session_from_services(options: dict[str, Any]) -> CreateAgentSessionResult:
    services = options["services"]
    model: Model | None = options.get("model")
    model_fallback_message: str | None = None
    thinking_level = options.get("thinkingLevel", options.get("thinking_level"))
    session_path = options.get("sessionPath", options.get("session_path", services.get("sessionPath")))
    if session_path is not None:
        session_path = str(Path(str(session_path)).expanduser().resolve())
    session_id = options.get("sessionId", options.get("session_id", services.get("sessionId")))
    fresh_session = _is_fresh_session_path(session_path)
    existing_session = _load_existing_session_context(str(services["cwd"]), session_path, thinking_level or "off")
    has_existing_session = bool(existing_session and existing_session.messages)
    has_thinking_entry = _has_session_entry_type(session_path, "thinking_level_change")
    if model is None and has_existing_session and existing_session and existing_session.model:
        restored_model = services["modelRegistry"].find(
            existing_session.model.get("provider", ""),
            existing_session.model.get("modelId", ""),
        )
        if restored_model and services["modelRegistry"].hasConfiguredAuth(restored_model):
            model = restored_model
        else:
            model_fallback_message = (
                f"Could not restore model "
                f"{existing_session.model.get('provider', '')}/{existing_session.model.get('modelId', '')}"
            )
    if model is None:
        settings_manager = services["settingsManager"]
        initial = find_initial_model(
            scoped_models=options.get("scopedModels", options.get("scoped_models")) or [],
            is_continuing=has_existing_session or bool(options.get("isContinuing", options.get("is_continuing", False))),
            model_registry=services["modelRegistry"],
            cli_provider=options.get("provider"),
            cli_model=options.get("modelId", options.get("model_id")),
            default_provider=_call_or_none(settings_manager, "getDefaultProvider", "get_default_provider"),
            default_model_id=_call_or_none(settings_manager, "getDefaultModel", "get_default_model"),
            default_thinking_level=_call_or_none(
                settings_manager,
                "getDefaultThinkingLevel",
                "get_default_thinking_level",
            ),
        )
        model = initial.model
        if initial.fallback_message:
            model_fallback_message = initial.fallback_message
        elif model_fallback_message and model is not None:
            model_fallback_message = f"{model_fallback_message}. Using {model.provider}/{model.id}"
        if thinking_level is None and not has_existing_session:
            thinking_level = initial.thinking_level
    if model is None:
        raise RuntimeError(_format_no_models_available_message())
    if thinking_level is None and has_existing_session and existing_session:
        thinking_level = existing_session.thinking_level if has_thinking_entry else None
    extensions_result = services["resourceLoader"].get_extensions()
    runtime = extensions_result.get("runtime")
    active_tool_names, allowed_tool_names = _resolve_pi_tool_options(options)
    provider_retry_settings = _provider_retry_settings(services["settingsManager"])
    session = AgentSession(
        cwd=services["cwd"],
        agent_dir=services.get("agentDir"),
        model=model,
        thinking_level=thinking_level or "off",
        scoped_models=options.get("scopedModels", options.get("scoped_models")),
        active_tool_names=active_tool_names,
        allowed_tool_names=allowed_tool_names,
        excluded_tool_names=options.get("excludeTools", options.get("exclude_tools")),
        transport=_call_or_none(services["settingsManager"], "getTransport", "get_transport"),
        thinking_budgets=_call_or_none(services["settingsManager"], "getThinkingBudgets", "get_thinking_budgets"),
        max_retry_delay_ms=_first_defined(
            provider_retry_settings.get("maxRetryDelayMs"),
            provider_retry_settings.get("max_retry_delay_ms"),
        ),
        tool_definitions=_tool_definitions_for_sdk(services, options),
        convert_to_llm=_convert_to_llm_for_sdk(
            services["settingsManager"],
            options.get("convertToLlm", options.get("convert_to_llm")),
        ),
        resource_loader=services["resourceLoader"],
        settings_manager=services["settingsManager"],
        extension_runner=runtime if isinstance(runtime, ExtensionRunner) else None,
        stream_fn=_stream_fn_for_sdk(
            services["modelRegistry"],
            services["settingsManager"],
            services["providerControlPlane"],
        ),
        provider_control_plane=services["providerControlPlane"],
        session_path=session_path,
        parent_session_path=options.get("parentSession", options.get("parent_session_path")),
        session_id=str(session_id) if session_id else None,
        session_start_event=options.get("sessionStartEvent", options.get("session_start_event")),
    )
    _record_initial_session_state(session, model, thinking_level or "off", fresh_session)
    return CreateAgentSessionResult(
        session=session,
        extensions_result=services["resourceLoader"].get_extensions(),
        model_fallback_message=model_fallback_message,
    )


createAgentSessionFromServices = create_agent_session_from_services


def _call_or_none(target: object, *names: str) -> Any:
    for name in names:
        method = getattr(target, name, None)
        if callable(method):
            return method()
    return None


def _provider_retry_settings(settings_manager: object) -> dict[str, Any]:
    settings = _call_or_none(
        settings_manager,
        "getProviderRetrySettings",
        "get_provider_retry_settings",
    )
    return settings if isinstance(settings, dict) else {}


def _default_session_dir(cwd: str, agent_dir: str) -> Path:
    resolved_cwd = str(Path(cwd).expanduser().resolve())
    safe_path = resolved_cwd.lstrip("/\\")
    for separator in ("/", "\\", ":"):
        safe_path = safe_path.replace(separator, "-")
    path = Path(agent_dir).expanduser().resolve() / "sessions" / f"--{safe_path}--"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_session_path(cwd: str, agent_dir: str, session_id: str | None = None) -> tuple[str, str]:
    resolved_session_id = session_id or uuid.uuid4().hex
    session_dir = _default_session_dir(cwd, agent_dir)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    file_timestamp = timestamp.replace(":", "-").replace(".", "-")
    path = session_dir / f"{file_timestamp}_{resolved_session_id}.jsonl"
    while path.exists():
        resolved_session_id = session_id or uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        file_timestamp = timestamp.replace(":", "-").replace(".", "-")
        path = session_dir / f"{file_timestamp}_{resolved_session_id}.jsonl"
    return str(path), resolved_session_id


def _is_fresh_session_path(session_path: str | None) -> bool:
    if not session_path:
        return False
    path = Path(session_path)
    return not path.exists() or path.stat().st_size == 0


def _load_existing_session_context(
    cwd: str,
    session_path: str | None,
    thinking_level: str,
) -> SessionContextSnapshot | None:
    if not session_path:
        return None
    path = Path(session_path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    return SessionStore(str(path), cwd=cwd).build_context(default_thinking_level=thinking_level)


def _has_session_entry_type(session_path: str | None, entry_type: str) -> bool:
    if not session_path:
        return False
    path = Path(session_path)
    if not path.exists() or path.stat().st_size == 0:
        return False
    store = SessionStore(str(path), cwd=str(path.parent))
    return any(entry.get("type") == entry_type for entry in store.entries)


def _record_initial_session_state(session: AgentSession, model: Model, thinking_level: str, fresh_session: bool) -> None:
    store = getattr(session, "_session_store", None)
    if store is None:
        return
    if fresh_session:
        store.append_model_change(model.provider, model.id)
        store.append_thinking_level_change(thinking_level)
        return
    if not any(entry.get("type") == "thinking_level_change" for entry in store.entries):
        store.append_thinking_level_change(thinking_level)


def _stream_fn_for_sdk(
    model_registry: object,
    settings_manager: object,
    provider_control_plane: ProviderControlPlane,
):
    def _stream(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        auth_method = getattr(model_registry, "getApiKeyAndHeaders", None) or getattr(
            model_registry,
            "get_api_key_and_headers",
            None,
        )
        if not callable(auth_method):
            raise RuntimeError("Model registry does not support request auth resolution.")
        auth = auth_method(model)
        if not isinstance(auth, dict) or auth.get("ok") is False:
            raise RuntimeError(str(auth.get("error") if isinstance(auth, dict) else "Failed to resolve request auth"))

        provider_retry_settings = _call_or_none(
            settings_manager,
            "getProviderRetrySettings",
            "get_provider_retry_settings",
        )
        if not isinstance(provider_retry_settings, dict):
            provider_retry_settings = {}

        http_idle_timeout_ms = _call_or_none(
            settings_manager,
            "getHttpIdleTimeoutMs",
            "get_http_idle_timeout_ms",
        )
        effective_timeout_ms = 2147483647 if http_idle_timeout_ms == 0 else http_idle_timeout_ms
        timeout_ms = _first_defined(
            getattr(options, "timeout_ms", None),
            provider_retry_settings.get("timeoutMs"),
            provider_retry_settings.get("timeout_ms"),
            effective_timeout_ms,
        )
        websocket_connect_timeout_ms = _first_defined(
            getattr(options, "websocket_connect_timeout_ms", None),
            _call_or_none(
                settings_manager,
                "getWebSocketConnectTimeoutMs",
                "get_websocket_connect_timeout_ms",
            ),
        )
        max_retries = _first_defined(
            getattr(options, "max_retries", None),
            provider_retry_settings.get("maxRetries"),
            provider_retry_settings.get("max_retries"),
        )
        max_retry_delay_ms = _first_defined(
            getattr(options, "max_retry_delay_ms", None),
            provider_retry_settings.get("maxRetryDelayMs"),
            provider_retry_settings.get("max_retry_delay_ms"),
        )
        headers = merge_provider_attribution_headers(
            model,
            settings_manager,
            getattr(options, "session_id", None),
            auth.get("headers"),
            getattr(options, "headers", None),
        )
        next_options = replace(
            options or SimpleStreamOptions(),
            api_key=auth.get("apiKey"),
            timeout_ms=timeout_ms,
            websocket_connect_timeout_ms=websocket_connect_timeout_ms,
            max_retries=max_retries,
            max_retry_delay_ms=max_retry_delay_ms,
            headers=headers,
        )
        return provider_control_plane.api_providers.require(model.api).stream_simple(model, context, next_options)

    return _stream


def _first_defined(*values):
    for value in values:
        if value is not None:
            return value
    return None


_OPENROUTER_HOST = "openrouter.ai"
_NVIDIA_NIM_HOST = "integrate.api.nvidia.com"
_CLOUDFLARE_API_HOST = "api.cloudflare.com"
_CLOUDFLARE_AI_GATEWAY_HOST = "gateway.ai.cloudflare.com"
_OPENCODE_HOST = "opencode.ai"


def _matches_host(base_url: str, expected_host: str) -> bool:
    try:
        return urlparse(base_url).hostname == expected_host
    except Exception:  # noqa: BLE001 - mirrors Pi's defensive URL parsing.
        return False


def _is_openrouter_model(model: Model) -> bool:
    return model.provider == "openrouter" or _OPENROUTER_HOST in model.base_url


def _is_nvidia_nim_model(model: Model) -> bool:
    return model.provider == "nvidia" or _matches_host(model.base_url, _NVIDIA_NIM_HOST)


def _is_cloudflare_model(model: Model) -> bool:
    return (
        model.provider in {"cloudflare-workers-ai", "cloudflare-ai-gateway"}
        or _matches_host(model.base_url, _CLOUDFLARE_API_HOST)
        or _matches_host(model.base_url, _CLOUDFLARE_AI_GATEWAY_HOST)
    )


def _is_install_telemetry_enabled(settings_manager: object) -> bool:
    telemetry_env = os.environ.get("APPV231_TELEMETRY")
    if telemetry_env is not None:
        return bool(telemetry_env) and telemetry_env.lower() in {"1", "true", "yes"}
    enabled = _call_or_none(settings_manager, "getEnableInstallTelemetry", "get_enable_install_telemetry")
    return True if enabled is None else bool(enabled)


def _default_attribution_headers(model: Model, settings_manager: object) -> dict[str, str] | None:
    if not _is_install_telemetry_enabled(settings_manager):
        return None
    if _is_openrouter_model(model):
        return {
            "HTTP-Referer": "https://appv231.local",
            "X-OpenRouter-Title": "appv231",
            "X-OpenRouter-Categories": "cli-agent",
        }
    if _is_nvidia_nim_model(model):
        return {"X-BILLING-INVOKE-ORIGIN": "appv231"}
    if _is_cloudflare_model(model):
        return {"User-Agent": "appv231-coding-agent"}
    return None


def _session_headers(model: Model, session_id: str | None) -> dict[str, str] | None:
    if not session_id:
        return None
    if model.provider not in {"opencode", "opencode-go"} and not _matches_host(model.base_url, _OPENCODE_HOST):
        return None
    return {"x-opencode-session": session_id, "x-opencode-client": "pi"}


def merge_provider_attribution_headers(
    model: Model,
    settings_manager: object,
    session_id: str | None,
    *header_sources: object,
) -> dict[str, str] | None:
    merged: dict[str, str] = {}
    for source in (
        _session_headers(model, session_id),
        _default_attribution_headers(model, settings_manager),
        *header_sources,
    ):
        if isinstance(source, dict):
            merged.update({str(key): str(value) for key, value in source.items()})
    return merged or None


def _format_no_models_available_message() -> str:
    return "No models available. Check your installation or add models to models.json."


def _tool_definitions_for_sdk(services: Mapping[str, Any], options: Mapping[str, Any]) -> list[object] | None:
    custom_tools = options.get("customTools", options.get("custom_tools"))
    if custom_tools is None:
        return None
    return [
        *create_all_tool_definitions(
            str(services["cwd"]),
            _builtin_tool_options(services["settingsManager"]),
        ),
        *list(custom_tools),
    ]


def _builtin_tool_options(settings_manager: object) -> dict[str, dict[str, object]]:
    auto_resize_images = _call_or_none(settings_manager, "getImageAutoResize", "get_image_auto_resize")
    return {
        "read": {"auto_resize_images": True if auto_resize_images is None else bool(auto_resize_images)},
        "bash": {
            "command_prefix": _call_or_none(settings_manager, "getShellCommandPrefix", "get_shell_command_prefix"),
            "shell_path": _call_or_none(settings_manager, "getShellPath", "get_shell_path"),
        },
    }


def _resolve_pi_tool_options(options: Mapping[str, Any]) -> tuple[list[str] | None, list[str] | None]:
    tools = options.get("tools")
    if tools is not None:
        selected = [str(name) for name in tools]
        return selected, selected
    no_tools = options.get("noTools", options.get("no_tools"))
    if no_tools:
        return [], [] if no_tools == "all" else None
    return ["read", "bash", "edit", "write"], None


_IMAGE_READING_DISABLED_TEXT = "Image reading is disabled."


def _convert_to_llm_for_sdk(
    settings_manager: object,
    converter: Callable[[list[AgentMessage]], list[Message]] | None,
) -> Callable[[list[AgentMessage]], list[Message]]:
    convert = converter or default_convert_to_llm

    def convert_to_llm_with_block_images(messages: list[AgentMessage]) -> list[Message]:
        converted = convert(messages)
        if not _call_or_none(settings_manager, "getBlockImages", "get_block_images"):
            return converted
        return [_replace_images_for_block_images(message) for message in converted]

    return convert_to_llm_with_block_images


def _replace_images_for_block_images(message: Message) -> Message:
    if getattr(message, "role", None) not in {"user", "toolResult"}:
        return message
    content = getattr(message, "content", None)
    if not isinstance(content, list) or not any(_is_image_content(block) for block in content):
        return message

    filtered_content: list[Any] = []
    for block in content:
        if _is_image_content(block):
            if filtered_content and _is_image_disabled_placeholder(filtered_content[-1]):
                continue
            filtered_content.append(TextContent(text=_IMAGE_READING_DISABLED_TEXT))
            continue
        filtered_content.append(block)
    return replace(message, content=filtered_content)


def _is_image_content(block: object) -> bool:
    if isinstance(block, ImageContent):
        return True
    if isinstance(block, Mapping):
        return block.get("type") == "image"
    return getattr(block, "type", None) == "image"


def _is_image_disabled_placeholder(block: object) -> bool:
    if isinstance(block, TextContent):
        return block.text == _IMAGE_READING_DISABLED_TEXT
    if isinstance(block, Mapping):
        return block.get("type") == "text" and block.get("text") == _IMAGE_READING_DISABLED_TEXT
    return getattr(block, "type", None) == "text" and getattr(block, "text", None) == _IMAGE_READING_DISABLED_TEXT


def _drain_pending_provider_registrations(
    runtime: ExtensionRunner,
    model_registry: ModelRegistry,
) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    pending = runtime.pending_provider_registrations
    runtime.clear_pending_provider_registrations()
    for name, config, extension_path in pending:
        try:
            model_registry.registerProvider(name, config)
        except Exception as error:  # noqa: BLE001 - Pi reports extension registration failures as diagnostics.
            diagnostics.append(
                {
                    "type": "error",
                    "message": f'Extension "{extension_path}" error: {error}',
                }
            )
    return diagnostics


def _apply_extension_flag_values(runtime: ExtensionRunner, raw_values: object) -> list[dict[str, object]]:
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
