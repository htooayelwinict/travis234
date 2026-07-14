from __future__ import annotations

import asyncio
import json

import httpx

from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.models import Provider, ProviderStreams
from travis.ai.providers.faux import text_response_events
from travis.ai.providers.github_copilot_oauth import (
    get_github_copilot_base_url,
    login_github_copilot,
    normalize_domain,
    refresh_github_copilot_token,
)
from travis.ai.types import Context, UserMessage
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry


def test_copilot_base_url_uses_the_authenticated_proxy_endpoint() -> None:
    token = "tid=test;exp=9999999999;proxy-ep=proxy.business.githubcopilot.com;"

    assert get_github_copilot_base_url(token) == "https://api.business.githubcopilot.com"
    assert get_github_copilot_base_url(None, "company.ghe.com") == "https://copilot-api.company.ghe.com"
    assert normalize_domain("https://company.ghe.com/some/path") == "company.ghe.com"


def test_copilot_refresh_exchanges_the_github_token_and_filters_account_models() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url == httpx.URL("https://api.github.com/copilot_internal/v2/token"):
            assert request.headers["Authorization"] == "Bearer github-refresh-token"
            return httpx.Response(
                200,
                json={
                    "token": "tid=test;exp=9999999999;proxy-ep=proxy.individual.githubcopilot.com;",
                    "expires_at": 9_999_999_999,
                },
            )
        if request.url == httpx.URL("https://api.individual.githubcopilot.com/models"):
            assert request.headers["X-GitHub-Api-Version"] == "2026-06-01"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "gpt-4.1",
                            "model_picker_enabled": True,
                            "capabilities": {"supports": {"tool_calls": True}},
                        },
                        {
                            "id": "disabled",
                            "model_picker_enabled": True,
                            "policy": {"state": "disabled"},
                            "capabilities": {"supports": {"tool_calls": True}},
                        },
                        {
                            "id": "no-tools",
                            "model_picker_enabled": True,
                            "capabilities": {"supports": {"tool_calls": False}},
                        },
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def run() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_github_copilot_token("github-refresh-token", client=client)

    credential = asyncio.run(run())

    assert credential == {
        "refresh": "github-refresh-token",
        "access": "tid=test;exp=9999999999;proxy-ep=proxy.individual.githubcopilot.com;",
        "expires": 9_999_999_999_000 - 300_000,
        "enterpriseUrl": None,
        "availableModelIds": ["gpt-4.1"],
    }
    assert [str(request.url) for request in requests] == [
        "https://api.github.com/copilot_internal/v2/token",
        "https://api.individual.githubcopilot.com/models",
    ]


def test_copilot_device_login_rejects_an_untrusted_verification_uri() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://github.com/login/device/code")
        return httpx.Response(
            200,
            json={
                "device_code": "device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "file:///tmp/credential-stealer",
                "interval": 1,
                "expires_in": 900,
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await login_github_copilot(
                {
                    "onPrompt": lambda _prompt: "",
                    "onDeviceCode": lambda _info: None,
                },
                client=client,
            )

    try:
        asyncio.run(run())
    except RuntimeError as error:
        assert str(error) == "Untrusted verification_uri in device code response"
    else:
        raise AssertionError("login accepted an untrusted verification URI")


def test_builtin_registration_exposes_copilot_oauth_and_credential_base_url(tmp_path) -> None:
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    providers = registry.get_oauth_providers()

    assert {provider["id"] for provider in providers} >= {"github-copilot"}

    access = "tid=test;exp=9999999999;proxy-ep=proxy.business.githubcopilot.com;"
    auth = AuthStorage.in_memory(
        {
            "github-copilot": {
                "type": "oauth",
                "refresh": "github-token",
                "access": access,
                "expires": 9_999_999_999_000,
                "availableModelIds": ["gpt-4.1"],
            }
        }
    )
    registry = ModelRegistry.in_memory(auth)
    model = registry.find("github-copilot", "gpt-4.1")
    assert model is not None

    resolved = registry.get_api_key_and_headers(model)

    assert resolved["apiKey"] == access
    assert resolved["baseUrl"] == "https://api.business.githubcopilot.com"
    assert {provider["id"] for provider in registry.get_oauth_providers()} >= {"github-copilot"}


def test_provider_runtime_uses_the_oauth_credential_base_url(tmp_path) -> None:
    access = "tid=test;exp=9999999999;proxy-ep=proxy.business.githubcopilot.com;"
    auth = AuthStorage.in_memory(
        {
            "github-copilot": {
                "type": "oauth",
                "refresh": "github-token",
                "access": access,
                "expires": 9_999_999_999_000,
                "availableModelIds": ["gpt-4.1"],
            }
        }
    )
    registry = ModelRegistry.in_memory(auth)
    model = registry.find("github-copilot", "gpt-4.1")
    assert model is not None
    captured = {}

    def capture(active_model, _context, options):
        captured["model"] = active_model
        captured["options"] = options
        stream = create_assistant_message_event_stream()
        for event in text_response_events(active_model, "captured"):
            stream.push(event)
        return stream

    existing = registry.runtime.get_provider("github-copilot")
    assert existing is not None
    registry.runtime.set_provider(
        Provider(
            id=existing.id,
            name=existing.name,
            base_url=existing.base_url,
            auth=existing.auth,
            models=existing.get_models(),
            api=ProviderStreams(stream=capture, stream_simple=capture),
        )
    )

    result = registry.stream_simple(
        model,
        Context(messages=[UserMessage(content="hello")]),
    ).result_sync()

    assert result.stop_reason == "stop"
    assert captured["model"].base_url == "https://api.business.githubcopilot.com"
    assert captured["options"].api_key == access


def test_copilot_model_picker_uses_provider_owned_catalog(tmp_path) -> None:
    access = "tid=test;exp=9999999999;proxy-ep=proxy.business.githubcopilot.com;"
    auth = AuthStorage.in_memory(
        {
            "github-copilot": {
                "type": "oauth",
                "refresh": "github-token",
                "access": access,
                "expires": 9_999_999_999_000,
                "availableModelIds": ["gpt-4.1"],
            }
        }
    )
    registry = ModelRegistry.in_memory(auth)

    selected = [
        model.id
        for model in registry.get_selectable()
        if model.provider == "github-copilot"
    ]

    assert "gpt-4.1" in selected
    assert len(selected) > 1
