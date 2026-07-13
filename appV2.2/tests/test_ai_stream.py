from __future__ import annotations

import pytest

from appv22.ai.event_stream import create_assistant_message_event_stream
from appv22.ai.models import register_provider_auth_config, reset_models
from appv22.ai.stream import (
    ApiProvider,
    complete_simple_sync,
    get_api_provider,
    register_api_provider,
    reset_api_providers,
    stream,
    stream_simple,
)
from appv22.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    SimpleStreamOptions,
    StartEvent,
    TextContent,
    UserMessage,
    empty_usage,
    now_ms,
)


def _model(api: str = "faux") -> Model:
    return Model(id="m", name="m", api=api, provider="faux", base_url="")


def _provider(api: str = "faux") -> ApiProvider:
    def _stream(model, context, options=None):
        s = create_assistant_message_event_stream()
        msg = AssistantMessage(
            content=[TextContent(text="ok")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        )
        s.push(StartEvent(partial=msg))
        s.push(DoneEvent(reason="stop", message=msg))
        return s

    return ApiProvider(api=api, stream=_stream, stream_simple=_stream)


def setup_function() -> None:
    reset_api_providers()
    reset_models()


def test_register_and_get_provider() -> None:
    p = _provider()
    register_api_provider(p)
    assert get_api_provider("faux") is p


def test_get_unknown_provider_raises() -> None:
    with pytest.raises(KeyError):
        get_api_provider("nope")


def test_stream_routes_to_provider_by_model_api() -> None:
    register_api_provider(_provider())
    result = stream(_model(), Context(messages=[UserMessage(content="q", timestamp=now_ms())])).result_sync()
    assert result.content[0].text == "ok"


def test_complete_simple_sync() -> None:
    register_api_provider(_provider())
    msg = complete_simple_sync(_model(), Context(messages=[UserMessage(content="q", timestamp=now_ms())]))
    assert msg.stop_reason == "stop"
    _ = stream_simple  # referenced for import coverage


def test_stream_simple_injects_env_api_key_when_missing(monkeypatch) -> None:
    seen = {}

    def _stream(model, context, options=None):
        seen["options"] = options
        return _provider().stream_simple(model, context, options)

    register_api_provider(ApiProvider(api="faux", stream=_stream, stream_simple=_stream))
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    model = Model(id="m", name="m", api="faux", provider="openrouter", base_url="")

    stream_simple(model, Context(messages=[UserMessage(content="q", timestamp=now_ms())])).result_sync()

    assert seen["options"].api_key == "env-key"


def test_stream_simple_preserves_explicit_api_key_over_env(monkeypatch) -> None:
    seen = {}

    def _stream(model, context, options=None):
        seen["options"] = options
        return _provider().stream_simple(model, context, options)

    register_api_provider(ApiProvider(api="faux", stream=_stream, stream_simple=_stream))
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    model = Model(id="m", name="m", api="faux", provider="openrouter", base_url="")

    stream_simple(
        model,
        Context(messages=[UserMessage(content="q", timestamp=now_ms())]),
        SimpleStreamOptions(api_key="explicit-key"),
    ).result_sync()

    assert seen["options"].api_key == "explicit-key"


def test_stream_simple_passes_registry_headers_and_auth_header() -> None:
    seen = {}

    def _stream(model, context, options=None):
        seen["options"] = options
        return _provider().stream_simple(model, context, options)

    register_api_provider(ApiProvider(api="faux", stream=_stream, stream_simple=_stream))
    register_provider_auth_config(
        "proxy",
        {"apiKey": "literal-key", "headers": {"X-Provider": "provider"}, "authHeader": True},
    )
    model = Model(
        id="m",
        name="m",
        api="faux",
        provider="proxy",
        base_url="",
        headers={"X-Model": "model"},
    )

    stream_simple(model, Context(messages=[UserMessage(content="q", timestamp=now_ms())])).result_sync()

    assert seen["options"].api_key == "literal-key"
    assert seen["options"].headers == {
        "X-Model": "model",
        "X-Provider": "provider",
        "Authorization": "Bearer literal-key",
    }


def test_model_registry_register_provider_registers_and_unregisters_pi_stream_provider() -> None:
    from appv22.coding_agent import AuthStorage, ModelRegistry

    seen: dict[str, str] = {}

    def dynamic_stream(model, context, options=None):
        seen["api"] = model.api
        seen["provider"] = model.provider
        return _provider("dynamic-api").stream_simple(model, context, options)

    registry = ModelRegistry.inMemory(AuthStorage.inMemory())
    registry.registerProvider("dynamic", {"api": "dynamic-api", "streamSimple": dynamic_stream})

    assert get_api_provider("dynamic-api").api == "dynamic-api"

    stream_simple(
        Model(id="m", name="m", api="dynamic-api", provider="dynamic", base_url=""),
        Context(messages=[UserMessage(content="q", timestamp=now_ms())]),
    ).result_sync()

    assert seen == {"api": "dynamic-api", "provider": "dynamic"}

    registry.unregisterProvider("dynamic")

    with pytest.raises(KeyError):
        get_api_provider("dynamic-api")
