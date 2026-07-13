"""Stream entrypoints + api-registry. Port of stream.ts + api-registry.ts."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Callable

from appv23.ai.event_stream import AssistantMessageEventStream
from appv23.ai.models import get_api_key_and_headers
from appv23.ai.types import (
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


_API_PROVIDERS: dict[str, ApiProvider] = {}
_API_PROVIDER_SOURCES: dict[str, str | None] = {}


def register_api_provider(provider: ApiProvider, source_id: str | None = None) -> None:
    _API_PROVIDERS[provider.api] = provider
    _API_PROVIDER_SOURCES[provider.api] = source_id


def get_api_provider(api: str) -> ApiProvider:
    provider = _API_PROVIDERS.get(api)
    if provider is None:
        raise KeyError(f"No api provider registered for api '{api}'")
    return provider


def get_api_providers() -> list[ApiProvider]:
    return list(_API_PROVIDERS.values())


def unregister_api_providers(source_id: str) -> None:
    for api, registered_source_id in list(_API_PROVIDER_SOURCES.items()):
        if registered_source_id == source_id:
            _API_PROVIDER_SOURCES.pop(api, None)
            _API_PROVIDERS.pop(api, None)


def reset_api_providers() -> None:
    _API_PROVIDERS.clear()
    _API_PROVIDER_SOURCES.clear()


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
