"""AgentSession service factory helpers."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from travis.agent.types import AgentMessage
from travis.ai.model_resolver import find_initial_model
from travis.ai.types import Context, ImageContent, Message, Model, SimpleStreamOptions, TextContent
from travis.coding_agent.artifact_manifest import ArtifactManifest
from travis.coding_agent.artifact_store import ArtifactLimits, DurableArtifactStore
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.extensions import ExtensionRunner, apply_extension_flag_values
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.object_utils import call_optional as _call_or_none
from travis.coding_agent.object_utils import first_defined as _first_defined
from travis.coding_agent.operations import OperationRuntime
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_composition import SessionDependencies
from travis.coding_agent.session_contracts import SessionLifecyclePort
from travis.coding_agent.session_options import SessionBootstrapOptions
from travis.coding_agent.session_store import SessionContextSnapshot, SessionStore
from travis.coding_agent.session_types import default_convert_to_llm
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.tools import create_all_tool_definitions


@dataclass
class CreateAgentSessionResult:
    session: SessionLifecyclePort
    extensions_result: dict[str, object]
    model_fallback_message: str | None = None


def create_session_artifact_registry(
    *,
    session_path: str | None,
    agent_dir: str,
    settings_manager: SettingsManager,
) -> ArtifactRegistry:
    if session_path is None:
        return ArtifactRegistry()
    getter = getattr(settings_manager, "get_artifact_limits", None)
    limits = getter() if callable(getter) else ArtifactLimits()
    if not isinstance(limits, ArtifactLimits):
        limits = ArtifactLimits()
    return ArtifactRegistry(
        durable_store=DurableArtifactStore(agent_dir),
        manifest=ArtifactManifest.for_session(session_path, limits=limits),
    )




def _build_session_dependencies(
    raw_options: Mapping[str, object] | SessionBootstrapOptions,
) -> SessionDependencies:
    options = SessionBootstrapOptions.from_mapping(raw_options)
    cwd = str(Path(str(options.cwd)).expanduser().resolve())
    agent_dir = str(Path(str(options.agent_dir or Path.home() / ".travis234" / "agent")).expanduser().resolve())
    settings_manager = options.settings_manager or SettingsManager.create(
        cwd,
        agent_dir,
    )
    configured_session_dir = options.session_dir
    if configured_session_dir is None:
        get_session_dir = getattr(settings_manager, "getSessionDir", None) or getattr(
            settings_manager,
            "get_session_dir",
            None,
        )
        if callable(get_session_dir):
            configured_session_dir = get_session_dir()
    session_catalog = options.session_catalog or SessionCatalog(
        agent_dir,
        session_dir=str(configured_session_dir) if configured_session_dir else None,
    )
    resource_loader = options.resource_loader
    if resource_loader is None:
        resource_loader_options = cast(
            dict[str, Any],
            dict(options.resource_loader_options or {}),
        )
        resource_loader = DefaultResourceLoader(
            cwd=cwd,
            agent_dir=agent_dir,
            settings_manager=settings_manager,
            **resource_loader_options,
        )
        reload_options = dict(
            cast(Mapping[str, object], options.resource_loader_reload_options or {})
        )
        for camel_name, snake_name, value in (
            ("projectTrustOverride", "project_trust_override", options.project_trust_override),
            ("projectTrustContext", "project_trust_context", options.project_trust_context),
            ("trustStore", "trust_store", options.trust_store),
        ):
            if (
                value is not None
                and camel_name not in reload_options
                and snake_name not in reload_options
            ):
                reload_options[camel_name] = value
        resource_loader.reload(reload_options)
    auth_storage = options.auth_storage or AuthStorage.create(
        str(Path(agent_dir) / "auth.json")
    )
    model_registry = options.model_registry or ModelRegistry.create(
        auth_storage,
        str(Path(agent_dir) / "models.json"),
    )
    if not isinstance(model_registry, ModelRegistry):
        raise TypeError("modelRegistry must be a ModelRegistry")
    if model_registry.auth_storage is not auth_storage:
        raise ValueError("modelRegistry and authStorage must share the same AuthStorage")
    session_id = options.session_id
    session_path = options.session_path
    if session_path is None:
        session_path, session_id = session_catalog.new_session_path(cwd, str(session_id) if session_id else None)
    else:
        session_path = str(Path(str(session_path)).expanduser().resolve())
    diagnostics: list[dict[str, object]] = []
    extensions_result = resource_loader.get_extensions()
    runtime = extensions_result.get("runtime")
    if isinstance(runtime, ExtensionRunner):
        diagnostics.extend(_drain_pending_provider_registrations(runtime, model_registry))
        diagnostics.extend(
            apply_extension_flag_values(
                runtime,
                options.extension_flag_values,
            )
        )
    return SessionDependencies(
        cwd=cwd,
        agent_dir=agent_dir,
        settings_manager=settings_manager,
        resource_loader=resource_loader,
        auth_storage=auth_storage,
        model_registry=model_registry,
        session_catalog=session_catalog,
        session_path=str(session_path),
        session_id=str(session_id or ""),
        operation_runtime=options.operation_runtime,
        diagnostics=tuple(diagnostics),
        session_factory=options.session_factory,
    )


def create_agent_session_services(
    options: Mapping[str, object] | SessionBootstrapOptions,
) -> dict[str, Any]:
    return _build_session_dependencies(options).to_legacy_mapping()




def create_agent_session(options: Mapping[str, Any] | None = None, **kwargs: Any) -> CreateAgentSessionResult:
    """SDK factory: create services, resolve model, and return a session result."""

    if options is None:
        resolved_options: dict[str, Any] = {}
    elif isinstance(options, Mapping):
        resolved_options = dict(options)
    else:
        raise TypeError("create_agent_session options must be a mapping")
    resolved_options.update(kwargs)
    bootstrap = SessionBootstrapOptions.from_mapping(resolved_options)
    services = bootstrap.services
    if services is None:
        services = create_agent_session_services(bootstrap)
    return create_agent_session_from_services(replace(bootstrap, services=services))




def create_agent_session_from_services(
    raw_options: Mapping[str, object] | SessionBootstrapOptions,
) -> CreateAgentSessionResult:
    options = SessionBootstrapOptions.from_mapping(raw_options)
    raw_services = options.services
    if raw_services is None:
        raise ValueError("services are required")
    if isinstance(raw_services, SessionDependencies):
        services = raw_services
    elif isinstance(raw_services, Mapping):
        services = SessionDependencies.from_legacy_mapping(raw_services)
    else:
        raise TypeError("services must be SessionDependencies or a mapping")
    operation_runtime = (
        options.operation_runtime
        if options.was_provided("operation_runtime")
        else services.operation_runtime
    )
    owns_operation_runtime = False
    model = options.model
    model_fallback_message: str | None = None
    thinking_level = options.thinking_level
    session_path = (
        options.session_path
        if options.was_provided("session_path")
        else services.session_path
    )
    if session_path is not None:
        session_path = str(Path(str(session_path)).expanduser().resolve())
    session_id = (
        options.session_id
        if options.was_provided("session_id")
        else services.session_id or None
    )
    fresh_session = _is_fresh_session_path(session_path)
    existing_session = _load_existing_session_context(services.cwd, session_path, thinking_level or "off")
    has_existing_session = bool(existing_session and existing_session.messages)
    has_thinking_entry = _has_session_entry_type(session_path, "thinking_level_change")
    if model is None and has_existing_session and existing_session and existing_session.model:
        restored_model = services.model_registry.find(
            existing_session.model.get("provider", ""),
            existing_session.model.get("modelId", ""),
        )
        if restored_model and services.model_registry.has_configured_auth(restored_model):
            model = restored_model
        else:
            model_fallback_message = (
                f"Could not restore model "
                f"{existing_session.model.get('provider', '')}/{existing_session.model.get('modelId', '')}"
            )
    if model is None:
        settings_manager = services.settings_manager
        initial = find_initial_model(
            scoped_models=options.scoped_models or [],
            is_continuing=has_existing_session or bool(options.is_continuing),
            model_registry=services.model_registry,
            cli_provider=options.provider,
            cli_model=options.model_id,
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
    extensions_result = services.resource_loader.get_extensions()
    runtime = extensions_result.get("runtime")
    memory_settings = services.settings_manager.get_memory_settings()
    active_tool_names, allowed_tool_names = _resolve_tool_options(
        options,
        memory_enabled=memory_settings.enabled,
    )
    provider_retry_settings = _provider_retry_settings(services.settings_manager)
    owned_operation_runtime: OperationRuntime | None = None
    if operation_runtime is None:
        owned_operation_runtime = OperationRuntime.from_settings(
            services.agent_dir,
            services.settings_manager.get_operation_settings(),
            heartbeat_interval_seconds=None,
        )
        operation_runtime = owned_operation_runtime
        owns_operation_runtime = True
        diagnostics = list(services.diagnostics)
        recovery_report = getattr(operation_runtime, "recovery_report", None)
        if recovery_report is not None and recovery_report.has_diagnostic:
            diagnostics.append(recovery_report.as_dict())
        services = replace(
            services,
            operation_runtime=operation_runtime,
            diagnostics=tuple(diagnostics),
        )
        if isinstance(raw_services, dict):
            raw_services["operationRuntime"] = operation_runtime
            raw_services["diagnostics"] = [dict(item) for item in diagnostics]
    try:
        session_factory = services.session_factory or _default_session_factory
        session = session_factory(
            cwd=services.cwd,
            agent_dir=services.agent_dir,
            model=model,
            thinking_level=thinking_level or "off",
            scoped_models=options.scoped_models,
            active_tool_names=active_tool_names,
            allowed_tool_names=allowed_tool_names,
            excluded_tool_names=options.exclude_tools,
            transport=_call_or_none(
                services.settings_manager, "getTransport", "get_transport"
            ),
            thinking_budgets=_call_or_none(
                services.settings_manager,
                "getThinkingBudgets",
                "get_thinking_budgets",
            ),
            max_retry_delay_ms=_first_defined(
                provider_retry_settings.get("maxRetryDelayMs"),
                provider_retry_settings.get("max_retry_delay_ms"),
            ),
            tool_definitions=_tool_definitions_for_sdk(services, options),
            convert_to_llm=_convert_to_llm_for_sdk(
                services.settings_manager,
                options.convert_to_llm,
            ),
            resource_loader=services.resource_loader,
            settings_manager=services.settings_manager,
            extension_runner=runtime if isinstance(runtime, ExtensionRunner) else None,
            stream_fn=_stream_fn_for_sdk(
                services.model_registry,
                services.settings_manager,
            ),
            model_registry=services.model_registry,
            session_index=services.session_catalog.index,
            session_path=session_path,
            parent_session_path=options.parent_session_path,
            session_id=str(session_id) if session_id else None,
            session_start_event=options.session_start_event,
            defer_session_start=bool(options.defer_session_start),
            model_role_bindings=options.model_role_bindings,
            model_role_event_sink=options.model_role_event_sink,
            operation_runtime=operation_runtime,
            owns_operation_runtime=owns_operation_runtime,
        )
    except BaseException:
        if owned_operation_runtime is not None:
            owned_operation_runtime.close()
        raise
    _record_initial_session_state(session, model, thinking_level or "off", fresh_session)
    return CreateAgentSessionResult(
        session=session,
        extensions_result=services.resource_loader.get_extensions(),
        model_fallback_message=model_fallback_message,
    )




def _provider_retry_settings(settings_manager: object) -> dict[str, Any]:
    settings = _call_or_none(
        settings_manager,
        "getProviderRetrySettings",
        "get_provider_retry_settings",
    )
    return settings if isinstance(settings, dict) else {}


def _default_session_dir(cwd: str, agent_dir: str) -> Path:
    path = SessionCatalog(agent_dir).workspace_directory(cwd)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_session_path(cwd: str, agent_dir: str, session_id: str | None = None) -> tuple[str, str]:
    return SessionCatalog(agent_dir).new_session_path(cwd, session_id)


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


def _default_session_factory(**kwargs: object) -> SessionLifecyclePort:
    module = importlib.import_module("travis.coding_agent.agent_session")
    factory = vars(module)["AgentSession"]
    return factory(**kwargs)


def _record_initial_session_state(
    session: SessionLifecyclePort,
    model: Model,
    thinking_level: str,
    fresh_session: bool,
) -> None:
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
    model_registry: ModelRegistry,
    settings_manager: object,
):
    def _stream(model: Model, context: Context, options: SimpleStreamOptions | None = None):
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
            getattr(options, "headers", None),
        )
        next_options = replace(
            options or SimpleStreamOptions(),
            timeout_ms=timeout_ms,
            websocket_connect_timeout_ms=websocket_connect_timeout_ms,
            max_retries=max_retries,
            max_retry_delay_ms=max_retry_delay_ms,
            headers=headers,
        )
        return model_registry.stream_simple(model, context, next_options)

    return _stream


_OPENROUTER_HOST = "openrouter.ai"
_NVIDIA_NIM_HOST = "integrate.api.nvidia.com"
_CLOUDFLARE_API_HOST = "api.cloudflare.com"
_CLOUDFLARE_AI_GATEWAY_HOST = "gateway.ai.cloudflare.com"
_OPENCODE_HOST = "opencode.ai"


def _matches_host(base_url: str, expected_host: str) -> bool:
    try:
        return urlparse(base_url).hostname == expected_host
    except Exception:  # noqa: BLE001 - preserves the established defensive URL parsing.
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
    telemetry_env = os.environ.get("TRAVIS234_TELEMETRY")
    if telemetry_env is not None:
        return bool(telemetry_env) and telemetry_env.lower() in {"1", "true", "yes"}
    enabled = _call_or_none(settings_manager, "getEnableInstallTelemetry", "get_enable_install_telemetry")
    return True if enabled is None else bool(enabled)


def _default_attribution_headers(model: Model, settings_manager: object) -> dict[str, str] | None:
    if not _is_install_telemetry_enabled(settings_manager):
        return None
    if _is_openrouter_model(model):
        return {
            "HTTP-Referer": "https://travis.local",
            "X-OpenRouter-Title": "travis",
            "X-OpenRouter-Categories": "cli-agent",
        }
    if _is_nvidia_nim_model(model):
        return {"X-BILLING-INVOKE-ORIGIN": "travis"}
    if _is_cloudflare_model(model):
        return {"User-Agent": "travis-coding-agent"}
    return None


def _session_headers(model: Model, session_id: str | None) -> dict[str, str] | None:
    if not session_id:
        return None
    if model.provider not in {"opencode", "opencode-go"} and not _matches_host(model.base_url, _OPENCODE_HOST):
        return None
    return {"x-opencode-session": session_id, "x-opencode-client": "travis"}


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


def _tool_definitions_for_sdk(
    services: SessionDependencies,
    options: SessionBootstrapOptions,
) -> list[object] | None:
    custom_tools = options.custom_tools
    if custom_tools is None:
        return None
    definitions: list[object] = [
        *create_all_tool_definitions(
            services.cwd,
            _builtin_tool_options(services.settings_manager),
        ),
        *list(custom_tools),
    ]
    return definitions


def _builtin_tool_options(settings_manager: object) -> dict[str, dict[str, object]]:
    auto_resize_images = _call_or_none(settings_manager, "getImageAutoResize", "get_image_auto_resize")
    return {
        "read": {"auto_resize_images": True if auto_resize_images is None else bool(auto_resize_images)},
        "bash": {
            "command_prefix": _call_or_none(settings_manager, "getShellCommandPrefix", "get_shell_command_prefix"),
            "shell_path": _call_or_none(settings_manager, "getShellPath", "get_shell_path"),
        },
    }


def _resolve_tool_options(
    options: SessionBootstrapOptions,
    *,
    memory_enabled: bool = False,
) -> tuple[list[str] | None, list[str] | None]:
    tools = options.tools
    if tools is not None:
        selected = [str(name) for name in tools]
        return selected, selected
    no_tools = options.no_tools
    if no_tools:
        return [], [] if no_tools == "all" else None
    active = ["read", "bash", "edit", "write"]
    if memory_enabled:
        active.append("memory")
    return active, None


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
            model_registry.register_provider(name, config)
        except Exception as error:  # noqa: BLE001 - Travis reports extension registration failures as diagnostics.
            diagnostics.append(
                {
                    "type": "error",
                    "message": f'Extension "{extension_path}" error: {error}',
                }
            )
    return diagnostics
