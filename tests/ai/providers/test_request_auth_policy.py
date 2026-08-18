from __future__ import annotations

from types import SimpleNamespace

import pytest

from travis.ai.env_config import ModelConfig
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.catalog import get_provider_profile
from travis.ai.providers.provider_request import prepare_provider_request
from travis.ai.types import Context, Model, UserMessage


def _prepare_request(
    *,
    provider: str,
    api: str,
    credential: str = "credential-value",
    model_headers: dict[str, str] | None = None,
) -> object:
    base_url = "https://provider.example/v1"
    model = Model(
        id="fixture-model",
        name="Fixture Model",
        api=api,
        provider=provider,
        base_url=base_url,
        context_window=128_000,
        max_tokens=4_096,
        headers=model_headers,
    )
    config = ModelConfig(
        enabled=True,
        api_key=credential,
        model=model.id,
        base_url=base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=provider,
    )
    profile = get_provider_profile(provider) or ProviderProfile(
        name=provider,
        api_mode=api,
        base_url=base_url,
    )
    return prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="reply with ok")]),
        SimpleNamespace(),
        config,
        profile,
    )


@pytest.mark.parametrize(
    ("provider", "api", "expected_header"),
    [
        ("anthropic", "anthropic-messages", "x-api-key"),
        ("minimax", "anthropic-messages", "x-api-key"),
        ("minimax-cn", "anthropic-messages", "x-api-key"),
        ("kimi-coding", "anthropic-messages", "x-api-key"),
        ("opencode", "anthropic-messages", "x-api-key"),
        ("opencode-go", "anthropic-messages", "x-api-key"),
        ("google", "google-generative-ai", "x-goog-api-key"),
        ("opencode", "google-generative-ai", "x-goog-api-key"),
        ("opencode", "openai-responses", "Authorization"),
        ("opencode-go", "openai-completions", "Authorization"),
        ("fireworks", "anthropic-messages", "Authorization"),
        ("vercel-ai-gateway", "anthropic-messages", "Authorization"),
        ("custom-anthropic", "anthropic-messages", "Authorization"),
    ],
)
def test_request_authentication_follows_provider_route_contract(
    provider: str,
    api: str,
    expected_header: str,
) -> None:
    request = _prepare_request(provider=provider, api=api)

    assert request.headers[expected_header] == (
        "Bearer credential-value" if expected_header == "Authorization" else "credential-value"
    )
    for competing_header in {"Authorization", "x-api-key", "x-goog-api-key"} - {expected_header}:
        assert competing_header not in request.headers


def test_anthropic_oauth_token_remains_bearer_authentication() -> None:
    request = _prepare_request(
        provider="anthropic",
        api="anthropic-messages",
        credential="sk-ant-oat-fixture-token",
    )

    assert request.headers["Authorization"] == "Bearer sk-ant-oat-fixture-token"
    assert "x-api-key" not in request.headers


def test_anthropic_compatible_api_key_routes_send_protocol_version() -> None:
    request = _prepare_request(provider="opencode-go", api="anthropic-messages")

    assert request.headers["anthropic-version"] == "2023-06-01"


def test_direct_kimi_replaces_the_catalogs_kimi_cli_identity() -> None:
    request = _prepare_request(
        provider="kimi-coding",
        api="anthropic-messages",
        model_headers={"User-Agent": "KimiCLI/1.5"},
    )

    assert request.headers["User-Agent"].startswith("Travis234/")
    assert request.headers["User-Agent"] != "KimiCLI/1.5"


def test_direct_kimi_alias_uses_the_travis_identity() -> None:
    request = _prepare_request(
        provider="kimi",
        api="anthropic-messages",
        model_headers={"User-Agent": "KimiCLI/1.5"},
    )

    assert request.headers["User-Agent"].startswith("Travis234/")


def test_non_kimi_provider_identity_header_is_not_rewritten() -> None:
    request = _prepare_request(
        provider="custom-anthropic",
        api="anthropic-messages",
        model_headers={"User-Agent": "CustomClient/7"},
    )

    assert request.headers["User-Agent"] == "CustomClient/7"
