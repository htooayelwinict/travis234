"""Session-scoped policy for selecting registered models by purpose."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Literal, cast

from travis.ai.model_resolver import ScopedModel, parse_model_pattern
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    SimpleStreamOptions,
    UserMessage,
)

ModelRole = Literal["primary", "compression", "worker", "reviewer", "vision"]
MODEL_ROLES: tuple[ModelRole, ...] = (
    "primary",
    "compression",
    "worker",
    "reviewer",
    "vision",
)
CONFIGURABLE_MODEL_ROLES = frozenset(MODEL_ROLES[1:])
_ROLE_FALLBACKS: dict[ModelRole, tuple[ModelRole, ...]] = {
    "reviewer": ("worker",),
}


@dataclass(frozen=True)
class ModelRoleTraceStep:
    role: ModelRole
    source: str
    selector: str | None
    outcome: str
    model_ref: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ModelRoleResolution:
    requested_role: ModelRole
    selected_role: ModelRole | None
    scoped_model: ScopedModel | None
    source: str
    fallback_trace: tuple[ModelRoleTraceStep, ...]

    @property
    def available(self) -> bool:
        return self.scoped_model is not None

    def as_event(self) -> dict[str, object]:
        return {
            "role": self.requested_role,
            "selectedRole": self.selected_role,
            "source": self.source,
            "model": _model_ref(self.scoped_model.model) if self.scoped_model else None,
            "fallbackTrace": [asdict(step) for step in self.fallback_trace],
        }


class ModelRoleRouter:
    """Resolve role selectors without taking ownership of models or credentials."""

    def __init__(
        self,
        model_registry: object,
        settings_manager: object,
        primary: ScopedModel,
        *,
        session_bindings: Mapping[ModelRole, ScopedModel] | None = None,
        event_sink: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.model_registry = model_registry
        self.settings_manager = settings_manager
        self._lock = threading.RLock()
        self._primary = _copy_scope(primary)
        self._session_bindings: dict[ModelRole, ScopedModel] = {}
        for role, binding in (session_bindings or {}).items():
            validated = _validate_role(role)
            if validated == "primary":
                raise ValueError("The primary model role is controlled by the active session model.")
            self._session_bindings[validated] = _copy_scope(binding)
        self._event_sink = event_sink

    def set_primary(self, model: Model, thinking_level: str | None) -> None:
        with self._lock:
            self._primary = ScopedModel(model=model, thinking_level=thinking_level)

    def resolve(
        self,
        role: ModelRole,
        *,
        override: ScopedModel | None = None,
        selector_override: str | None = None,
        required_inputs: Iterable[str] | None = None,
    ) -> ModelRoleResolution:
        requested_role = _validate_role(role)
        if override is not None and selector_override is not None:
            raise ValueError("Provide only one model override.")

        required = frozenset(
            required_inputs
            if required_inputs is not None
            else (("image",) if requested_role == "vision" else ("text",))
        )
        with self._lock:
            primary = _copy_scope(self._primary)
            bindings = {
                binding_role: _copy_scope(binding)
                for binding_role, binding in self._session_bindings.items()
            }

        trace: list[ModelRoleTraceStep] = []
        if override is not None:
            result = self._try_scope(
                requested_role,
                "call_override",
                override,
                required,
                trace,
            )
            if result is not None:
                return self._finish(requested_role, requested_role, result, trace)
        elif selector_override is not None:
            result = self._try_selector(
                requested_role,
                "call_override",
                selector_override,
                required,
                trace,
            )
            if result is not None:
                return self._finish(requested_role, requested_role, result, trace)

        if requested_role != "primary":
            for candidate_role in (requested_role, *_ROLE_FALLBACKS.get(requested_role, ())):
                result = self._try_configured_role(candidate_role, bindings, required, trace)
                if result is not None:
                    return self._finish(requested_role, candidate_role, result, trace)

        result = self._try_scope(
            "primary",
            "active_primary",
            primary,
            required,
            trace,
        )
        if result is not None:
            return self._finish(requested_role, "primary", result, trace)
        return self._finish(requested_role, None, None, trace)

    def _try_configured_role(
        self,
        role: ModelRole,
        bindings: Mapping[ModelRole, ScopedModel],
        required: frozenset[str],
        trace: list[ModelRoleTraceStep],
    ) -> ScopedModel | None:
        attempted = False
        binding = bindings.get(role)
        if binding is not None:
            attempted = True
            result = self._try_scope(role, "session", binding, required, trace)
            if result is not None:
                return result

        selector = self._setting_for(role)
        if selector is not None:
            attempted = True
            source = self._setting_source_for(role) or "settings"
            result = self._try_selector(role, source, selector, required, trace)
            if result is not None:
                return result

        if not attempted:
            trace.append(
                ModelRoleTraceStep(
                    role=role,
                    source="settings",
                    selector=None,
                    outcome="missing",
                )
            )
        return None

    def _try_selector(
        self,
        role: ModelRole,
        source: str,
        selector: str,
        required: frozenset[str],
        trace: list[ModelRoleTraceStep],
    ) -> ScopedModel | None:
        parsed = parse_model_pattern(
            selector,
            list(self.model_registry.get_all()),
            allow_invalid_thinking_level_fallback=False,
        )
        if parsed.model is None:
            trace.append(
                ModelRoleTraceStep(
                    role=role,
                    source=source,
                    selector=selector,
                    outcome="not_found",
                )
            )
            return None
        if not self.model_registry.is_selectable(parsed.model):
            trace.append(
                ModelRoleTraceStep(
                    role=role,
                    source=source,
                    selector=selector,
                    outcome="unavailable",
                    model_ref=_model_ref(parsed.model),
                )
            )
            return None
        return self._try_scope(
            role,
            source,
            ScopedModel(parsed.model, parsed.thinking_level),
            required,
            trace,
            selector=selector,
        )

    @staticmethod
    def _try_scope(
        role: ModelRole,
        source: str,
        scope: ScopedModel,
        required: frozenset[str],
        trace: list[ModelRoleTraceStep],
        *,
        selector: str | None = None,
    ) -> ScopedModel | None:
        missing_inputs = sorted(required.difference(scope.model.input or ()))
        if missing_inputs:
            trace.append(
                ModelRoleTraceStep(
                    role=role,
                    source=source,
                    selector=selector,
                    outcome="incompatible",
                    model_ref=_model_ref(scope.model),
                    detail=f"missing input capability: {', '.join(missing_inputs)}",
                )
            )
            return None
        copied = _copy_scope(scope)
        trace.append(
            ModelRoleTraceStep(
                role=role,
                source=source,
                selector=selector,
                outcome="selected",
                model_ref=_model_ref(scope.model),
            )
        )
        return copied

    def _finish(
        self,
        requested_role: ModelRole,
        selected_role: ModelRole | None,
        scope: ScopedModel | None,
        trace: list[ModelRoleTraceStep],
    ) -> ModelRoleResolution:
        source = trace[-1].source if scope is not None else "unavailable"
        result = ModelRoleResolution(
            requested_role=requested_role,
            selected_role=selected_role,
            scoped_model=_copy_scope(scope) if scope is not None else None,
            source=source,
            fallback_trace=tuple(trace),
        )
        if self._event_sink is not None:
            self._event_sink(result.as_event())
        return result

    def _setting_for(self, role: ModelRole) -> str | None:
        getter = getattr(self.settings_manager, "get_model_role", None)
        value = getter(role) if callable(getter) else None
        return value if isinstance(value, str) and value.strip() else None

    def _setting_source_for(self, role: ModelRole) -> str | None:
        getter = getattr(self.settings_manager, "get_model_role_source", None)
        value = getter(role) if callable(getter) else None
        return value if isinstance(value, str) and value else None


def _copy_scope(scope: ScopedModel) -> ScopedModel:
    return ScopedModel(model=scope.model, thinking_level=scope.thinking_level)


def pending_context_has_images(context: Context) -> bool:
    for message in reversed(context.messages):
        if isinstance(message, AssistantMessage):
            break
        if isinstance(message, UserMessage) and isinstance(message.content, list):
            if any(isinstance(block, ImageContent) for block in message.content):
                return True
    return False


def model_input_has_images(prompt_message: object) -> bool:
    messages = prompt_message if isinstance(prompt_message, list) else [prompt_message]
    return any(
        isinstance(message, UserMessage)
        and isinstance(message.content, list)
        and any(isinstance(block, ImageContent) for block in message.content)
        for message in messages
    )


def model_role_stream_options(
    binding: ScopedModel,
    options: SimpleStreamOptions | None,
) -> SimpleStreamOptions:
    active_options = options or SimpleStreamOptions()
    thinking_level = binding.thinking_level
    return replace(
        active_options,
        max_tokens=binding.model.max_tokens or active_options.max_tokens,
        reasoning=(None if thinking_level == "off" else thinking_level)
        if thinking_level is not None
        else active_options.reasoning,
    )


def _model_ref(model: Model) -> str:
    return f"{model.provider}/{model.id}"


def _validate_role(role: object) -> ModelRole:
    if role not in MODEL_ROLES:
        raise ValueError(f"Unknown model role: {role!r}")
    return cast(ModelRole, role)


__all__ = (
    "CONFIGURABLE_MODEL_ROLES",
    "MODEL_ROLES",
    "ModelRole",
    "ModelRoleResolution",
    "ModelRoleRouter",
    "ModelRoleTraceStep",
)
