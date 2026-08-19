"""Immutable normalization for session bootstrap compatibility options."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


SESSION_BOOTSTRAP_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "agent_dir": "agentDir",
        "settings_manager": "settingsManager",
        "session_dir": "sessionDir",
        "session_catalog": "sessionCatalog",
        "resource_loader": "resourceLoader",
        "resource_loader_options": "resourceLoaderOptions",
        "resource_loader_reload_options": "resourceLoaderReloadOptions",
        "project_trust_override": "projectTrustOverride",
        "project_trust_context": "projectTrustContext",
        "trust_store": "trustStore",
        "auth_storage": "authStorage",
        "model_registry": "modelRegistry",
        "session_id": "sessionId",
        "session_path": "sessionPath",
        "extension_flag_values": "extensionFlagValues",
        "operation_runtime": "operationRuntime",
        "session_factory": "sessionFactory",
        "thinking_level": "thinkingLevel",
        "scoped_models": "scopedModels",
        "is_continuing": "isContinuing",
        "model_id": "modelId",
        "exclude_tools": "excludeTools",
        "convert_to_llm": "convertToLlm",
        "parent_session_path": "parentSession",
        "session_start_event": "sessionStartEvent",
        "defer_session_start": "deferSessionStart",
        "model_role_bindings": "modelRoleBindings",
        "model_role_event_sink": "modelRoleEventSink",
        "custom_tools": "customTools",
        "no_tools": "noTools",
        "retry_enabled": "retryEnabled",
        "max_retries": "maxRetries",
        "retry_delay_ms": "retryDelayMs",
        "max_retry_delay_ms": "maxRetryDelayMs",
    }
)

_SINGLE_KEYS = ("cwd", "model", "provider", "services", "tools")
_MISSING = object()


def _equal_alias_values(left: object, right: object) -> bool:
    if left is right:
        return True
    try:
        equal = left == right
    except Exception:  # noqa: BLE001 - incompatible compatibility values conflict.
        return False
    return equal if isinstance(equal, bool) else False


@dataclass(frozen=True, slots=True, repr=False)
class SessionBootstrapOptions:
    cwd: object = "."
    model: object | None = None
    provider: object | None = None
    services: object | None = None
    tools: object | None = None
    agent_dir: object | None = None
    settings_manager: object | None = None
    session_dir: object | None = None
    session_catalog: object | None = None
    resource_loader: object | None = None
    resource_loader_options: object | None = None
    resource_loader_reload_options: object | None = None
    project_trust_override: object | None = None
    project_trust_context: object | None = None
    trust_store: object | None = None
    auth_storage: object | None = None
    model_registry: object | None = None
    session_id: object | None = None
    session_path: object | None = None
    extension_flag_values: object | None = None
    operation_runtime: object | None = None
    session_factory: object | None = None
    thinking_level: object | None = None
    scoped_models: object | None = None
    is_continuing: object | None = None
    model_id: object | None = None
    exclude_tools: object | None = None
    convert_to_llm: object | None = None
    parent_session_path: object | None = None
    session_start_event: object | None = None
    defer_session_start: object | None = None
    model_role_bindings: object | None = None
    model_role_event_sink: object | None = None
    custom_tools: object | None = None
    no_tools: object | None = None
    retry_enabled: object | None = None
    max_retries: object | None = None
    retry_delay_ms: object | None = None
    max_retry_delay_ms: object | None = None
    extras: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )
    _provided: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, object] | SessionBootstrapOptions,
    ) -> SessionBootstrapOptions:
        if isinstance(values, SessionBootstrapOptions):
            return values
        source = dict(values)
        normalized: dict[str, object] = {}
        consumed: set[str] = set()
        for snake_name, camel_name in SESSION_BOOTSTRAP_ALIASES.items():
            snake_value = source.get(snake_name, _MISSING)
            camel_value = source.get(camel_name, _MISSING)
            if snake_value is not _MISSING and camel_value is not _MISSING:
                if not _equal_alias_values(snake_value, camel_value):
                    raise ValueError(
                        f"Conflicting bootstrap options: {camel_name} and {snake_name}"
                    )
                normalized[snake_name] = snake_value
            elif snake_value is not _MISSING:
                normalized[snake_name] = snake_value
            elif camel_value is not _MISSING:
                normalized[snake_name] = camel_value
            consumed.update((snake_name, camel_name))
        for name in _SINGLE_KEYS:
            if name in source:
                normalized[name] = source[name]
            consumed.add(name)
        extras: dict[str, object] = {
            name: value
            for name, value in source.items()
            if name not in consumed
        }
        return cls(
            **normalized,
            extras=MappingProxyType(extras),
            _provided=frozenset(normalized),
        )

    def was_provided(self, canonical_name: str) -> bool:
        return canonical_name in self._provided

    def __repr__(self) -> str:
        populated = tuple(
            name
            for name in (*_SINGLE_KEYS, *SESSION_BOOTSTRAP_ALIASES)
            if getattr(self, name) not in (None, ".")
        )
        return (
            "SessionBootstrapOptions("
            f"populated={populated!r}, extras={tuple(sorted(self.extras))!r})"
        )


__all__ = ["SESSION_BOOTSTRAP_ALIASES", "SessionBootstrapOptions"]
