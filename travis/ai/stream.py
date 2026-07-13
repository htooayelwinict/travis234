"""Streaming entry points and the provider API registry."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import threading
from typing import Callable

from travis.ai.event_stream import AssistantMessageEventStream
from travis.ai.models import get_api_key_and_headers
from travis.ai.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)

StreamFn = Callable[[Model, Context, "StreamOptions | None"], AssistantMessageEventStream]
SimpleStreamFn = Callable[[Model, Context, "SimpleStreamOptions | None"], AssistantMessageEventStream]


@dataclass
class ApiProvider:
    api: str
    stream: StreamFn
    stream_simple: SimpleStreamFn

    @property
    def streamSimple(self) -> SimpleStreamFn:
        return self.stream_simple


class ProviderRegistration:
    def __init__(self, close: Callable[[], None]) -> None:
        self._close = close
        self._closed = False
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._close()


class ApiProviderRegistry:
    def __init__(self) -> None:
        self._stacks: dict[str, list[tuple[object, str | None, ApiProvider]]] = {}
        self._lock = threading.RLock()

    def register(self, provider: ApiProvider, source_id: str | None = None) -> ProviderRegistration:
        token = object()
        with self._lock:
            self._stacks.setdefault(provider.api, []).append((token, source_id, provider))

        def close() -> None:
            with self._lock:
                stack = self._stacks.get(provider.api, [])
                stack[:] = [entry for entry in stack if entry[0] is not token]
                if not stack:
                    self._stacks.pop(provider.api, None)

        return ProviderRegistration(close)

    def get(self, api: str) -> ApiProvider | None:
        with self._lock:
            stack = self._stacks.get(api)
            return stack[-1][2] if stack else None

    def require(self, api: str) -> ApiProvider:
        provider = self.get(api)
        if provider is None:
            raise KeyError(f"No api provider registered for api '{api}'")
        return provider

    def all(self) -> list[ApiProvider]:
        with self._lock:
            return [stack[-1][2] for stack in self._stacks.values() if stack]

    def unregister_source(self, source_id: str) -> None:
        with self._lock:
            for api, stack in list(self._stacks.items()):
                stack[:] = [entry for entry in stack if entry[1] != source_id]
                if not stack:
                    self._stacks.pop(api, None)

    def clear(self) -> None:
        with self._lock:
            self._stacks.clear()


_DEFAULT_API_PROVIDER_REGISTRY = ApiProviderRegistry()


def register_api_provider(provider: ApiProvider, source_id: str | None = None) -> ProviderRegistration:
    return _DEFAULT_API_PROVIDER_REGISTRY.register(provider, source_id)


def get_api_provider(api: str) -> ApiProvider:
    return _DEFAULT_API_PROVIDER_REGISTRY.require(api)


def get_api_providers() -> list[ApiProvider]:
    return _DEFAULT_API_PROVIDER_REGISTRY.all()


def unregister_api_providers(source_id: str) -> None:
    _DEFAULT_API_PROVIDER_REGISTRY.unregister_source(source_id)


def reset_api_providers() -> None:
    _DEFAULT_API_PROVIDER_REGISTRY.clear()


clear_api_providers = reset_api_providers
registerApiProvider = register_api_provider
getApiProvider = get_api_provider
getApiProviders = get_api_providers
unregisterApiProviders = unregister_api_providers
clearApiProviders = clear_api_providers


def _with_model_auth(model: Model, options, options_type=StreamOptions):
    auth = get_api_key_and_headers(model)
    if auth.get("ok") is False:
        raise RuntimeError(str(auth.get("error") or "Failed to resolve request auth"))

    auth_api_key = auth.get("apiKey")
    explicit_api_key = getattr(options, "api_key", None)
    api_key = explicit_api_key if isinstance(explicit_api_key, str) and explicit_api_key.strip() else auth_api_key

    headers: dict[str, str] = {}
    auth_headers = auth.get("headers")
    if isinstance(auth_headers, dict):
        headers.update({str(key): str(value) for key, value in auth_headers.items()})
    explicit_headers = getattr(options, "headers", None)
    if isinstance(explicit_headers, dict):
        headers.update({str(key): str(value) for key, value in explicit_headers.items()})
    next_headers = headers or None

    if options is None:
        if api_key or next_headers:
            return options_type(api_key=str(api_key) if api_key is not None else None, headers=next_headers)
        return None
    return replace(options, api_key=str(api_key) if api_key is not None else None, headers=next_headers)


def stream(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessageEventStream:
    return get_api_provider(model.api).stream(model, context, _with_model_auth(model, options, StreamOptions))


def stream_simple(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessageEventStream:
    next_options = _with_model_auth(model, options, SimpleStreamOptions)
    if next_options is not None and not isinstance(next_options, SimpleStreamOptions):
        next_options = SimpleStreamOptions(api_key=next_options.api_key, headers=next_options.headers)
    return get_api_provider(model.api).stream_simple(model, context, next_options)


async def complete(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
    return await stream(model, context, options).result()


async def complete_simple(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessage:
    return await stream_simple(model, context, options).result()


def complete_sync(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
    return stream(model, context, options).result_sync()


def complete_simple_sync(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessage:
    return stream_simple(model, context, options).result_sync()


streamSimple = stream_simple
completeSimple = complete_simple
