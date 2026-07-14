"""Isolated provider wiring for deterministic tests.

Production has no global registry. This module preserves concise test setup
while binding every registered fake to the per-test ModelRegistry fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from travis.agent import (
    agent_loop as _agent_loop,
    agent_loop_continue as _agent_loop_continue,
    run_agent_loop as _run_agent_loop,
)
from travis.ai.auth import ApiKeyAuth, AuthResult, ModelAuth, ProviderAuth
from travis.ai.models import Provider, ProviderStreams
from travis.ai.types import Context, Model, SimpleStreamOptions, StreamOptions
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry


@dataclass(frozen=True)
class ApiProvider:
    api: str
    stream: Callable
    stream_simple: Callable


_registry: ModelRegistry | None = None
_api_providers: dict[str, ApiProvider] = {}


def use_registry(registry: ModelRegistry | None) -> None:
    global _registry
    _registry = registry
    _api_providers.clear()


def current_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    return _registry


def register_api_provider(provider, source_id: str | None = None):
    del source_id
    if isinstance(provider, Provider):
        current_registry().runtime.set_provider(provider)
        for model in provider.get_models():
            _api_providers[model.api] = ApiProvider(
                api=model.api,
                stream=provider.stream,
                stream_simple=provider.stream_simple,
            )
        return _Registration(lambda: current_registry().runtime.delete_provider(provider.id))
    if not isinstance(provider, ApiProvider):
        raise TypeError("test provider must be Provider or ApiProvider")
    previous = _api_providers.get(provider.api)
    _api_providers[provider.api] = provider

    def close() -> None:
        if previous is None:
            _api_providers.pop(provider.api, None)
        else:
            _api_providers[provider.api] = previous

    return _Registration(close)


def bind_model(registry: ModelRegistry, model: Model) -> None:
    test_provider = _api_providers.get(model.api)
    if test_provider is None:
        return
    existing = registry.runtime.get_provider(model.provider)
    models = list(existing.get_models()) if existing is not None else []
    if not any(item.id == model.id for item in models):
        models.append(model)
    registry.runtime.set_provider(
        Provider(
            id=model.provider,
            name=existing.name if existing is not None else model.provider,
            base_url=model.base_url or None,
            auth=existing.auth if existing is not None else _keyless_auth(),
            models=models,
            api=ProviderStreams(
                stream=test_provider.stream,
                stream_simple=test_provider.stream_simple,
            ),
        )
    )


def get_api_provider(api: str) -> ApiProvider:
    try:
        return _api_providers[api]
    except KeyError as error:
        raise KeyError(f"No api provider registered for api '{api}'") from error


def get_api_providers() -> list[ApiProvider]:
    return list(_api_providers.values())


def unregister_api_providers(source_id: str) -> None:
    del source_id


def reset_api_providers() -> None:
    _api_providers.clear()


clear_api_providers = reset_api_providers


def register_model(model: Model) -> None:
    current_registry().replace_model(model)
    bind_model(current_registry(), model)


def get_model(provider: str, model_id: str) -> Model | None:
    return current_registry().find(provider, model_id)


def get_models(provider: str) -> list[Model]:
    return list(current_registry().runtime.get_models(provider))


def get_providers() -> list[str]:
    return current_registry().get_providers()


def reset_models() -> None:
    current_registry().load_models()


def set_auth_credential(provider: str, credential: dict[str, object]) -> None:
    current_registry().set_auth_credential(provider, credential)


def get_api_key_for_provider(provider: str) -> str | None:
    return current_registry().get_api_key_for_provider(provider)


def get_provider_auth_status(provider: str) -> dict[str, object]:
    return current_registry().get_provider_auth_status(provider)


def stream(model: Model, context: Context, options: StreamOptions | None = None):
    provider = get_api_provider(model.api)
    return provider.stream(model, context, options)


def stream_simple(model: Model, context: Context, options: SimpleStreamOptions | None = None):
    provider = get_api_provider(model.api)
    return provider.stream_simple(model, context, options)


async def complete(model: Model, context: Context, options: StreamOptions | None = None):
    return await stream(model, context, options).result()


async def complete_simple(model: Model, context: Context, options: SimpleStreamOptions | None = None):
    return await stream_simple(model, context, options).result()


def complete_sync(model: Model, context: Context, options: StreamOptions | None = None):
    return stream(model, context, options).result_sync()


def complete_simple_sync(model: Model, context: Context, options: SimpleStreamOptions | None = None):
    return stream_simple(model, context, options).result_sync()


def run_agent_loop(*args, stream_fn=None, **kwargs):
    return _run_agent_loop(*args, stream_fn=stream_fn or stream_simple, **kwargs)


def agent_loop(*args, stream_fn=None, **kwargs):
    return _agent_loop(*args, stream_fn=stream_fn or stream_simple, **kwargs)


def agent_loop_continue(*args, stream_fn=None, **kwargs):
    return _agent_loop_continue(*args, stream_fn=stream_fn or stream_simple, **kwargs)


class _Registration:
    def __init__(self, close) -> None:
        self._close = close
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close()


def _keyless_auth() -> ProviderAuth:
    return ProviderAuth(
        api_key=ApiKeyAuth(
            name="Test provider",
            resolve=lambda _model, _context, _credential: AuthResult(
                auth=ModelAuth(),
                source="test",
            ),
        )
    )


__all__ = [name for name in globals() if not name.startswith("_")]
