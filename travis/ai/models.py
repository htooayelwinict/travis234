"""Provider-owned model runtime and cost helpers.

The runtime authority is an injected :class:`Models` collection. Providers
own their models, authentication behavior, and stream implementations; there
is no process-global provider or model registry.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from travis.ai.auth import (
    AuthContext,
    AuthResult,
    CredentialStore,
    InMemoryCredentialStore,
    ModelsError,
    ProviderAuth,
    default_auth_context,
    resolve_provider_auth,
)
from travis.ai.event_stream import AssistantMessageEventStream
from travis.ai.lazy_stream import lazy_stream
from travis.ai.types import (
    AssistantMessage,
    Context,
    Cost,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)

StreamFunction = Callable[[Model, Context, StreamOptions | None], AssistantMessageEventStream]
SimpleStreamFunction = Callable[[Model, Context, SimpleStreamOptions | None], AssistantMessageEventStream]
_EXTENDED_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ProviderStreams:
    stream: StreamFunction
    stream_simple: SimpleStreamFunction


@dataclass
class _RefreshFlight:
    event: threading.Event
    error: BaseException | None = None


class Provider:
    """Concrete provider runtime: metadata, auth, models, and streams."""

    def __init__(
        self,
        *,
        id: str,
        auth: ProviderAuth,
        models: Sequence[Model],
        api: ProviderStreams | Mapping[str, ProviderStreams],
        name: str | None = None,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
        refresh_models: Callable[[], Sequence[Model]] | None = None,
    ) -> None:
        if not id:
            raise ValueError("provider id is required")
        self.id = id
        self.name = name or id
        self.base_url = base_url
        self.headers = dict(headers) if headers else None
        self.auth = auth
        self._models = tuple(models)
        self._api = api
        self._refresh_models = refresh_models
        self._refresh_lock = threading.Lock()
        self._refresh_flight: _RefreshFlight | None = None

    def get_models(self) -> tuple[Model, ...]:
        with self._refresh_lock:
            return self._models

    def with_models(self, models: Sequence[Model]) -> "Provider":
        return Provider(
            id=self.id,
            name=self.name,
            base_url=self.base_url,
            headers=self.headers,
            auth=self.auth,
            models=models,
            api=self._api,
            refresh_models=self._refresh_models,
        )

    def with_base_url(self, base_url: str) -> "Provider":
        """Clone provider metadata without wrapping its stream dispatch."""
        return Provider(
            id=self.id,
            name=self.name,
            base_url=base_url,
            headers=self.headers,
            auth=self.auth,
            models=[replace(model, base_url=base_url) for model in self.get_models()],
            api=self._api,
            refresh_models=self._refresh_models,
        )

    def reconfigured(
        self,
        *,
        name: str,
        base_url: str | None,
        auth: ProviderAuth,
        models: Sequence[Model],
        api: ProviderStreams | Mapping[str, ProviderStreams] | None = None,
    ) -> "Provider":
        """Create a configured provider while retaining owned stream dispatch."""
        return Provider(
            id=self.id,
            name=name,
            base_url=base_url,
            headers=self.headers,
            auth=auth,
            models=models,
            api=api if api is not None else self._api,
            refresh_models=self._refresh_models,
        )

    def refresh(self) -> None:
        if self._refresh_models is None:
            return
        with self._refresh_lock:
            flight = self._refresh_flight
            owner = flight is None
            if owner:
                flight = _RefreshFlight(threading.Event())
                self._refresh_flight = flight
        assert flight is not None
        if not owner:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return
        try:
            refreshed = _settle_runtime_value(self._refresh_models())
            with self._refresh_lock:
                self._models = tuple(refreshed)
        except BaseException as error:
            with self._refresh_lock:
                flight.error = error
            raise
        finally:
            with self._refresh_lock:
                if self._refresh_flight is flight:
                    self._refresh_flight = None
                flight.event.set()

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        return _settle_runtime_value(self._streams_for(model).stream(model, context, options))

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        return _settle_runtime_value(self._streams_for(model).stream_simple(model, context, options))

    def _streams_for(self, model: Model) -> ProviderStreams:
        if isinstance(self._api, ProviderStreams):
            return self._api
        streams = self._api.get(model.api)
        if streams is None:
            raise ModelsError(
                "stream",
                f'Provider {self.id} has no API implementation for "{model.api}"',
            )
        return streams


class Models:
    """Isolated provider collection used for every model and request lookup."""

    def __init__(
        self,
        *,
        credentials: CredentialStore | None = None,
        auth_context: AuthContext | None = None,
        offline: bool = False,
    ) -> None:
        self.credentials = credentials or InMemoryCredentialStore()
        self.auth_context = auth_context or default_auth_context()
        self.offline = bool(offline)
        self._providers: dict[str, Provider] = {}
        self._lock = threading.RLock()

    def set_provider(self, provider: Provider) -> None:
        with self._lock:
            self._providers[provider.id] = provider

    def delete_provider(self, provider_id: str) -> None:
        with self._lock:
            self._providers.pop(provider_id, None)

    def clear_providers(self) -> None:
        with self._lock:
            self._providers.clear()

    def get_providers(self) -> tuple[Provider, ...]:
        with self._lock:
            return tuple(self._providers.values())

    def get_provider(self, provider_id: str) -> Provider | None:
        with self._lock:
            return self._providers.get(provider_id)

    def get_models(self, provider: str | None = None) -> tuple[Model, ...]:
        if provider is not None:
            entry = self.get_provider(provider)
            if entry is None:
                return ()
            try:
                return entry.get_models()
            except Exception:  # noqa: BLE001 - one provider cannot poison discovery.
                return ()
        collected: list[Model] = []
        for entry in self.get_providers():
            try:
                collected.extend(entry.get_models())
            except Exception:  # noqa: BLE001 - discovery is best-effort per provider.
                continue
        return tuple(collected)

    def get_model(self, provider: str, model_id: str) -> Model | None:
        return next((model for model in self.get_models(provider) if model.id == model_id), None)

    def async_api(self) -> "AsyncModels":
        return AsyncModels(self)

    def refresh(self, provider: str | None = None) -> None:
        if _has_running_event_loop():
            raise ModelsError(
                "async_context",
                "Synchronous model refresh cannot run on an active event loop; "
                "use await models.async_api().refresh(...)",
            )
        if self.offline:
            return
        if provider is not None:
            entry = self.get_provider(provider)
            if entry is None:
                return
            try:
                entry.refresh()
            except ModelsError:
                raise
            except Exception as error:  # noqa: BLE001
                raise ModelsError(
                    "model_source",
                    f"Model refresh failed for {provider}",
                    cause=error,
                ) from error
            return
        threads = [
            threading.Thread(target=_best_effort_refresh, args=(entry,))
            for entry in self.get_providers()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def get_auth(self, model: Model) -> AuthResult | None:
        provider = self.get_provider(model.provider)
        if provider is None:
            return None
        return resolve_provider_auth(
            provider.id,
            provider.auth,
            model,
            self.credentials,
            self.auth_context,
            offline=self.offline,
        )

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        return lazy_stream(
            model,
            lambda: self._stream_after_auth(
                model,
                context,
                options,
                simple=False,
                options_type=StreamOptions,
            ),
        )

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        return lazy_stream(
            model,
            lambda: self._stream_after_auth(
                model,
                context,
                options,
                simple=True,
                options_type=SimpleStreamOptions,
            ),
        )

    def complete(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessage:
        return self.stream(model, context, options).result_sync()

    def complete_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessage:
        return self.stream_simple(model, context, options).result_sync()

    def _stream_after_auth(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None,
        *,
        simple: bool,
        options_type: type[StreamOptions],
    ) -> AssistantMessageEventStream:
        provider = self.get_provider(model.provider)
        if provider is None:
            raise ModelsError("provider", f"Unknown provider: {model.provider}")
        request_model, request_options = self._apply_auth(
            provider,
            model,
            options,
            options_type,
        )
        if simple:
            return provider.stream_simple(request_model, context, request_options)
        return provider.stream(request_model, context, request_options)

    def _apply_auth(
        self,
        provider: Provider,
        model: Model,
        options: StreamOptions | None,
        options_type: type[StreamOptions],
    ) -> tuple[Model, StreamOptions | None]:
        explicit_api_key = getattr(options, "api_key", None)
        explicit_env = dict(getattr(options, "env", None) or {})
        resolution = resolve_provider_auth(
            provider.id,
            provider.auth,
            model,
            self.credentials,
            self.auth_context,
            api_key=explicit_api_key,
            env=explicit_env,
            offline=self.offline,
        )
        auth = resolution.auth if resolution is not None else None
        request_model = replace(model, base_url=auth.base_url) if auth and auth.base_url else model
        api_key = explicit_api_key if explicit_api_key is not None else (auth.api_key if auth else None)
        headers = {
            **(provider.headers or {}),
            **(dict(auth.headers) if auth and auth.headers else {}),
            **dict(getattr(options, "headers", None) or {}),
        }
        env = {
            **(dict(resolution.env) if resolution and resolution.env else {}),
            **explicit_env,
        }
        if options is None:
            if not api_key and not headers and not env:
                return request_model, None
            return request_model, options_type(
                api_key=api_key,
                headers=headers or None,
                env=env or None,
            )
        return request_model, replace(
            options,
            api_key=api_key,
            headers=headers or None,
            env=env or None,
        )


class AsyncModels:
    """Async facade over the provider-owned model collection."""

    def __init__(self, owner: Models) -> None:
        self._owner = owner

    async def refresh(self, provider: str | None = None) -> tuple[Model, ...]:
        if not self._owner.offline:
            await asyncio.to_thread(self._owner.refresh, provider)
        return self._owner.get_models(provider)

    async def find(self, provider: str, model_id: str) -> Model | None:
        if (
            not self._owner.offline
            and self._owner.get_provider(provider) is not None
            and not self._owner.get_models(provider)
        ):
            await self.refresh(provider)
        return self._owner.get_model(provider, model_id)

    async def get_models(self, provider: str | None = None) -> tuple[Model, ...]:
        return self._owner.get_models(provider)


def create_models(
    *,
    credentials: CredentialStore | None = None,
    auth_context: AuthContext | None = None,
    offline: bool = False,
) -> Models:
    return Models(credentials=credentials, auth_context=auth_context, offline=offline)


def create_provider(**kwargs: Any) -> Provider:
    return Provider(**kwargs)


def calculate_cost(model: Model, usage_tokens: dict[str, int]) -> Cost:
    """Calculate provider cost from per-million-token model rates."""

    def per_million(tokens: int, rate: float) -> float:
        return (tokens / 1_000_000.0) * rate

    input_tokens = (
        usage_tokens.get("input", 0)
        + usage_tokens.get("cache_read", 0)
        + usage_tokens.get("cache_write", 0)
    )
    rates = model.cost
    matched_threshold = -1
    for tier in model.cost.tiers:
        if input_tokens > tier.input_tokens_above and tier.input_tokens_above > matched_threshold:
            rates = tier
            matched_threshold = tier.input_tokens_above

    long_cache_write = usage_tokens.get("cache_write_1h", 0)
    short_cache_write = usage_tokens.get("cache_write", 0) - long_cache_write
    cost = Cost(
        input=per_million(usage_tokens.get("input", 0), rates.input),
        output=per_million(usage_tokens.get("output", 0), rates.output),
        cache_read=per_million(usage_tokens.get("cache_read", 0), rates.cache_read),
        cache_write=(
            rates.cache_write * short_cache_write + rates.input * 2 * long_cache_write
        ) / 1_000_000.0,
    )
    cost.total = cost.input + cost.output + cost.cache_read + cost.cache_write
    return cost


def get_supported_thinking_levels(model: Model) -> list[str]:
    if not model.reasoning:
        return ["off"]
    thinking_level_map = model.thinking_level_map or {}
    return [
        level
        for level in _EXTENDED_THINKING_LEVELS
        if thinking_level_map.get(level, level) is not None
        and (level not in {"xhigh", "max"} or level in thinking_level_map)
    ]


def clamp_thinking_level(model: Model, level: str) -> str:
    available = get_supported_thinking_levels(model)
    if level in available:
        return level
    try:
        requested_index = _EXTENDED_THINKING_LEVELS.index(level)
    except ValueError:
        return available[0] if available else "off"
    for candidate in _EXTENDED_THINKING_LEVELS[requested_index:]:
        if candidate in available:
            return candidate
    for candidate in reversed(_EXTENDED_THINKING_LEVELS[:requested_index]):
        if candidate in available:
            return candidate
    return available[0] if available else "off"


def models_are_equal(left: Model | None, right: Model | None) -> bool:
    return bool(left and right and left.id == right.id and left.provider == right.provider)


def _best_effort_refresh(provider: Provider) -> None:
    try:
        provider.refresh()
    except Exception:  # noqa: BLE001 - refresh-all isolates provider failures.
        return


def _settle_runtime_value(value: Any) -> Any:
    return asyncio.run(value) if inspect.isawaitable(value) else value


def _has_running_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


__all__ = [
    "AsyncModels",
    "Models",
    "Provider",
    "ProviderStreams",
    "calculate_cost",
    "clamp_thinking_level",
    "create_models",
    "create_provider",
    "get_supported_thinking_levels",
    "models_are_equal",
]
