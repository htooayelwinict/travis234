from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping

import httpx

import travis.ai.providers.subscription_oauth as subscription_oauth
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry


def _codex_access_token(account_id: str = "account-1") -> str:
    payload = {
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
        }
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_builtin_registration_exposes_subscription_oauth_providers(tmp_path) -> None:
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    providers = {provider["id"] for provider in registry.get_oauth_providers()}

    assert providers >= {"anthropic", "github-copilot", "openai-codex"}


def test_codex_browser_login_accepts_zero_argument_manual_callback(monkeypatch) -> None:
    access_token = _codex_access_token()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url == httpx.URL("https://auth.openai.com/oauth/token")
        return httpx.Response(
            200,
            json={
                "access_token": access_token,
                "refresh_token": "refresh-1",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(subscription_oauth, "_start_callback_server", lambda **_kwargs: None)

    async def run() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await subscription_oauth.login_openai_codex(
                {
                    "onSelect": lambda _prompt: "browser",
                    "onAuth": lambda _info: None,
                    "onManualCodeInput": lambda: "authorization-code",
                },
                client=client,
            )

    credential = asyncio.run(run())

    assert credential["access"] == access_token
    assert credential["refresh"] == "refresh-1"
    assert credential["accountId"] == "account-1"
    assert len(requests) == 1
    assert "grant_type=authorization_code" in requests[0].content.decode()


def test_codex_device_login_uses_device_authorization_contract() -> None:
    access_token = _codex_access_token("device-account")
    requests: list[httpx.Request] = []
    device_events: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url == httpx.URL("https://auth.openai.com/api/accounts/deviceauth/usercode"):
            return httpx.Response(
                200,
                json={"device_auth_id": "device-1", "user_code": "ABCD-EFGH", "interval": 0},
            )
        if request.url == httpx.URL("https://auth.openai.com/api/accounts/deviceauth/token"):
            return httpx.Response(
                200,
                json={"authorization_code": "authorization-code", "code_verifier": "verifier"},
            )
        if request.url == httpx.URL("https://auth.openai.com/oauth/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": access_token,
                    "refresh_token": "refresh-device",
                    "expires_in": 3600,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def run() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await subscription_oauth.login_openai_codex(
                {
                    "onSelect": lambda _prompt: "device_code",
                    "onDeviceCode": device_events.append,
                },
                client=client,
            )

    credential = asyncio.run(run())

    assert credential["accountId"] == "device-account"
    assert isinstance(device_events[0], Mapping)
    assert device_events[0]["userCode"] == "ABCD-EFGH"
    assert [str(request.url) for request in requests] == [
        "https://auth.openai.com/api/accounts/deviceauth/usercode",
        "https://auth.openai.com/api/accounts/deviceauth/token",
        "https://auth.openai.com/oauth/token",
    ]


def test_anthropic_manual_login_preserves_pkce_state(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def token_request(body: Mapping[str, object]) -> dict[str, object]:
        captured.update(body)
        return {"access": "sk-ant-oat-test", "refresh": "refresh", "expires": 123}

    monkeypatch.setattr(subscription_oauth, "_start_callback_server", lambda **_kwargs: None)
    monkeypatch.setattr(subscription_oauth, "_anthropic_token_request", token_request)

    credential = asyncio.run(
        subscription_oauth.login_anthropic(
            {
                "onAuth": lambda _info: None,
                "onManualCodeInput": lambda: "authorization-code",
                "onProgress": lambda _message: None,
            }
        )
    )

    assert credential["access"] == "sk-ant-oat-test"
    assert captured["grant_type"] == "authorization_code"
    assert captured["code"] == "authorization-code"
    assert captured["state"] == captured["code_verifier"]
