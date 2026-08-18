from __future__ import annotations

from types import SimpleNamespace

import httpx

from travis.ai.env_config import ModelConfig
from travis.ai.providers import travis_env as travis_env_module
from travis.ai.providers.provider_errors import _format_provider_exception
from travis.ai.providers.travis_env import TravisProvider
from travis.ai.types import Context, Model, UserMessage


def _model() -> Model:
    return Model(
        id="fixture-model",
        name="Fixture Model",
        api="openai-completions",
        provider="custom",
        base_url="https://provider.example/v1",
        context_window=32_000,
        max_tokens=1_024,
    )


def test_provider_http_error_redacts_sensitive_json_and_message_values() -> None:
    model = _model()
    request = httpx.Request("POST", model.base_url)
    response = httpx.Response(
        401,
        request=request,
        json={
            "error": {
                "message": "rejected ordinary-provider-secret and sk-audit-not-real-123456",
                "api_key": "ordinary-provider-secret",
                "access_token": "reflected-access-token-value",
                "details": {"authorization": "Bearer reflected-token-value"},
            }
        },
    )
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)

    formatted = _format_provider_exception(
        error,
        model,
        secrets=("ordinary-provider-secret",),
    )

    assert "ordinary-provider-secret" not in formatted
    assert "sk-audit-not-real-123456" not in formatted
    assert "reflected-access-token-value" not in formatted
    assert "reflected-token-value" not in formatted
    assert "[REDACTED]" in formatted


def test_non_http_provider_error_redacts_active_credentials() -> None:
    formatted = _format_provider_exception(
        RuntimeError("failed with Bearer reflected-token and ordinary-provider-secret"),
        _model(),
        secrets=("ordinary-provider-secret",),
    )

    assert formatted == "failed with Bearer [REDACTED] and [REDACTED]"


def test_safe_provider_error_detail_remains_factual() -> None:
    model = _model()
    request = httpx.Request("POST", model.base_url)
    response = httpx.Response(
        403,
        request=request,
        json={"error": {"message": "prompt policy rejected this request"}},
    )
    error = httpx.HTTPStatusError("forbidden", request=request, response=response)

    assert _format_provider_exception(error, model) == (
        '403: {"error":{"message":"prompt policy rejected this request"}}'
    )


def test_provider_runtime_redacts_the_active_request_key_from_error_events(monkeypatch) -> None:
    secret = "ordinary-provider-secret"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": f"credential {secret} was rejected"}},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        travis_env_module.httpx,
        "Client",
        lambda timeout: real_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )
    model = _model()
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key=secret,
            model=model.id,
            base_url=model.base_url,
            timeout_seconds=5,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider=model.provider,
        )
    )

    message = provider.stream(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(api_key=secret),
    ).result_sync()

    assert message.stop_reason == "error"
    assert secret not in str(message.error_message)
    assert "[REDACTED]" in str(message.error_message)


def test_provider_runtime_redacts_custom_sensitive_request_headers(monkeypatch) -> None:
    reflected_secret = "custom-client-secret-value"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"rejected {reflected_secret}")

    real_client = httpx.Client
    monkeypatch.setattr(
        travis_env_module.httpx,
        "Client",
        lambda timeout: real_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )
    model = _model()
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="ordinary-provider-secret",
            model=model.id,
            base_url=model.base_url,
            timeout_seconds=5,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider=model.provider,
        )
    )

    message = provider.stream(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(headers={"X-Client-Secret": reflected_secret}),
    ).result_sync()

    assert reflected_secret not in str(message.error_message)
    assert "[REDACTED]" in str(message.error_message)
