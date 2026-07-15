from __future__ import annotations

import asyncio
import threading

import pytest

from travis.ai.auth import (
    ApiKeyAuth,
    AuthResult,
    InMemoryCredentialStore,
    ModelAuth,
    ProviderAuth,
    ModelsError,
)
from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.models import Models, Provider, ProviderStreams
from travis.ai.providers.faux import text_response_events
from travis.ai.types import Context, Model, SimpleStreamOptions, UserMessage


def _model(provider: str = "fixture", api: str = "fixture") -> Model:
    return Model(
        id="model",
        name="Model",
        api=api,
        provider=provider,
        base_url="https://provider.test/v1",
        context_window=32_000,
        max_tokens=4_096,
    )


def _streams(capture: list[tuple[Model, object]] | None = None) -> ProviderStreams:
    def stream(model, _context, options=None):
        if capture is not None:
            capture.append((model, options))
        result = create_assistant_message_event_stream()
        for event in text_response_events(model, "ok"):
            result.push(event)
        return result

    return ProviderStreams(stream=stream, stream_simple=stream)


def _auth(value: str = "ambient") -> ProviderAuth:
    return ProviderAuth(
        api_key=ApiKeyAuth(
            name="Fixture key",
            resolve=lambda _model, _context, credential: AuthResult(
                auth=ModelAuth(api_key=str(credential.get("key") if credential else value)),
                source="fixture",
            ),
        )
    )


def test_provider_collections_are_isolated() -> None:
    left = Models()
    right = Models()
    left.set_provider(
        Provider(id="fixture", auth=_auth(), models=[_model()], api=_streams())
    )

    assert left.get_model("fixture", "model") is not None
    assert right.get_model("fixture", "model") is None


def test_provider_owns_model_auth_and_stream_dispatch() -> None:
    captured: list[tuple[Model, object]] = []
    runtime = Models()
    runtime.set_provider(
        Provider(
            id="fixture",
            auth=_auth(),
            models=[_model()],
            api=_streams(captured),
            headers={"x-provider": "provider"},
        )
    )

    response = runtime.stream_simple(
        _model(),
        Context(messages=[UserMessage(content="hello")]),
        SimpleStreamOptions(
            api_key="request-key",
            headers={"x-request": "request"},
            env={"REQUEST_ENV": "yes"},
        ),
    ).result_sync()

    assert response.stop_reason == "stop"
    request_model, options = captured[0]
    assert request_model.provider == "fixture"
    assert options.api_key == "request-key"
    assert options.headers == {
        "x-provider": "provider",
        "x-request": "request",
    }
    assert options.env == {"REQUEST_ENV": "yes"}


def test_stored_credential_type_owns_provider_without_ambient_fallback() -> None:
    credentials = InMemoryCredentialStore(
        {"fixture": {"type": "oauth", "access": "not-applicable"}}
    )
    runtime = Models(credentials=credentials)
    runtime.set_provider(
        Provider(id="fixture", auth=_auth("ambient-key"), models=[_model()], api=_streams())
    )

    assert runtime.get_auth(_model()) is None


def test_concurrent_dynamic_refresh_is_coalesced() -> None:
    release = threading.Event()
    started = threading.Event()
    calls = 0

    def refresh():
        nonlocal calls
        calls += 1
        started.set()
        release.wait(2)
        return [_model()]

    provider = Provider(
        id="fixture",
        auth=_auth(),
        models=[],
        api=_streams(),
        refresh_models=refresh,
    )
    runtime = Models()
    runtime.set_provider(provider)
    threads = [threading.Thread(target=lambda: runtime.refresh("fixture")) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert started.wait(1)
    release.set()
    for thread in threads:
        thread.join(2)

    assert calls == 1
    assert runtime.get_model("fixture", "model") is not None


def test_waiters_receive_the_error_from_the_refresh_they_joined() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0

    def refresh():
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            release_first.wait(2)
            raise RuntimeError("first refresh failed")
        return [_model()]

    provider = Provider(
        id="fixture",
        auth=_auth(),
        models=[],
        api=_streams(),
        refresh_models=refresh,
    )
    errors: list[str] = []

    def run_refresh() -> None:
        try:
            provider.refresh()
        except RuntimeError as error:
            errors.append(str(error))

    owner = threading.Thread(target=run_refresh)
    waiter = threading.Thread(target=run_refresh)
    owner.start()
    assert first_started.wait(1)
    waiter.start()
    release_first.set()
    owner.join(2)
    waiter.join(2)

    provider.refresh()

    assert errors == ["first refresh failed", "first refresh failed"]
    assert calls == 2
    assert provider.get_models() == (_model(),)


def test_unknown_provider_is_a_protocol_error_not_a_setup_exception() -> None:
    response = Models().stream_simple(
        _model(provider="missing"),
        Context(messages=[UserMessage(content="hello")]),
    ).result_sync()

    assert response.stop_reason == "error"
    assert response.error_message == "Unknown provider: missing"


def test_async_models_refreshes_and_finds_inside_a_running_loop() -> None:
    calls = 0

    async def refresh():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return [_model()]

    runtime = Models()
    runtime.set_provider(
        Provider(
            id="fixture",
            auth=_auth(),
            models=[],
            api=_streams(),
            refresh_models=refresh,
        )
    )

    async def scenario():
        refreshed = await runtime.async_api().refresh("fixture")
        found = await runtime.async_api().find("fixture", "model")
        return refreshed, found

    refreshed, found = asyncio.run(scenario())

    assert refreshed == (_model(),)
    assert found == _model()
    assert calls == 1


def test_sync_model_refresh_in_running_loop_points_to_async_api() -> None:
    runtime = Models()

    async def scenario() -> None:
        with pytest.raises(ModelsError, match=r"await models\.async_api\(\)\.refresh"):
            runtime.refresh()

    asyncio.run(scenario())
