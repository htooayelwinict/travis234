"""GitHub Copilot device authorization and request authentication."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit

import httpx

from travis.ai.providers._shared import signal_aborted


_CLIENT_ID = "Iv1.b507a08c87ecfe98"
_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}
_COPILOT_API_VERSION = "2026-06-01"
_DEFAULT_POLL_INTERVAL_SECONDS = 5.0
_MINIMUM_POLL_INTERVAL_SECONDS = 1.0
_SLOW_DOWN_INCREMENT_SECONDS = 5.0


def normalize_domain(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    except ValueError:
        return None
    hostname = parsed.hostname
    if not hostname or any(character.isspace() for character in hostname):
        return None
    return hostname


def get_github_copilot_base_url(token: str | None = None, enterprise_domain: str | None = None) -> str:
    if token:
        match = re.search(r"(?:^|;)proxy-ep=([^;]+)", token)
        if match:
            proxy_host = match.group(1)
            api_host = re.sub(r"^proxy\.", "api.", proxy_host)
            return f"https://{api_host}"
    if enterprise_domain:
        return f"https://copilot-api.{enterprise_domain}"
    return "https://api.individual.githubcopilot.com"


def _urls(domain: str) -> dict[str, str]:
    return {
        "device_code": f"https://{domain}/login/device/code",
        "access_token": f"https://{domain}/login/oauth/access_token",
        "copilot_token": f"https://api.{domain}/copilot_internal/v2/token",
    }


@asynccontextmanager
async def _use_client(client: httpx.AsyncClient | None) -> AsyncIterator[httpx.AsyncClient]:
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=5.0) as owned_client:
        yield owned_client


async def _fetch_json(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> object:
    response = await client.request(method, url, **kwargs)
    if not response.is_success:
        body = response.text
        suffix = f": {body}" if body else ""
        raise RuntimeError(f"{response.status_code} {response.reason_phrase}{suffix}")
    try:
        return response.json()
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON response from {url}") from error


def _record(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


async def _invoke(callback: object, argument: object) -> object:
    if not callable(callback):
        return None
    result = callback(argument)
    return await result if inspect.isawaitable(result) else result


async def _sleep(seconds: float, signal: object) -> None:
    if signal_aborted(signal):
        raise RuntimeError("Login cancelled")
    await asyncio.sleep(max(0.0, seconds))
    if signal_aborted(signal):
        raise RuntimeError("Login cancelled")


async def _start_device_flow(client: httpx.AsyncClient, domain: str) -> dict[str, object]:
    raw = _record(
        await _fetch_json(
            client,
            "POST",
            _urls(domain)["device_code"],
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _COPILOT_HEADERS["User-Agent"],
            },
            data={"client_id": _CLIENT_ID, "scope": "read:user"},
        )
    )
    if raw is None:
        raise RuntimeError("Invalid device code response")
    device_code = raw.get("device_code")
    user_code = raw.get("user_code")
    verification_uri = raw.get("verification_uri")
    interval = _number(raw.get("interval")) if "interval" in raw else None
    expires_in = _number(raw.get("expires_in"))
    if (
        not isinstance(device_code, str)
        or not isinstance(user_code, str)
        or not isinstance(verification_uri, str)
        or ("interval" in raw and interval is None)
        or expires_in is None
    ):
        raise RuntimeError("Invalid device code response fields")
    try:
        normalized_uri = httpx.URL(verification_uri)
    except Exception as error:
        raise RuntimeError("Untrusted verification_uri in device code response") from error
    if normalized_uri.scheme not in {"http", "https"} or not normalized_uri.host:
        raise RuntimeError("Untrusted verification_uri in device code response")
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": str(normalized_uri),
        "interval": interval,
        "expires_in": expires_in,
    }


async def _poll_for_github_access_token(
    client: httpx.AsyncClient,
    domain: str,
    device: Mapping[str, object],
    signal: object,
) -> str:
    expires_in = _number(device.get("expires_in"))
    deadline = time.monotonic() + expires_in if expires_in is not None else math.inf
    interval = max(
        _MINIMUM_POLL_INTERVAL_SECONDS,
        _number(device.get("interval")) or _DEFAULT_POLL_INTERVAL_SECONDS,
    )
    slow_down_responses = 0
    await _sleep(min(interval, max(0.0, deadline - time.monotonic())), signal)
    while time.monotonic() < deadline:
        if signal_aborted(signal):
            raise RuntimeError("Login cancelled")
        raw = _record(
            await _fetch_json(
                client,
                "POST",
                _urls(domain)["access_token"],
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": _COPILOT_HEADERS["User-Agent"],
                },
                data={
                    "client_id": _CLIENT_ID,
                    "device_code": str(device["device_code"]),
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        )
        if raw is None:
            raise RuntimeError("Invalid device token response")
        access_token = raw.get("access_token")
        if isinstance(access_token, str):
            return access_token
        error = raw.get("error")
        if not isinstance(error, str):
            raise RuntimeError("Invalid device token response")
        if error == "slow_down":
            slow_down_responses += 1
            server_interval = _number(raw.get("interval"))
            interval = (
                max(_MINIMUM_POLL_INTERVAL_SECONDS, server_interval)
                if server_interval is not None and server_interval > 0
                else max(_MINIMUM_POLL_INTERVAL_SECONDS, interval + _SLOW_DOWN_INCREMENT_SECONDS)
            )
        elif error != "authorization_pending":
            description = raw.get("error_description")
            suffix = f": {description}" if isinstance(description, str) and description else ""
            raise RuntimeError(f"Device flow failed: {error}{suffix}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await _sleep(min(interval, remaining), signal)
    if slow_down_responses:
        raise RuntimeError(
            "Device flow timed out after one or more slow_down responses. This is often caused by clock drift "
            "in WSL or VM environments. Please sync or restart the VM clock and try again."
        )
    raise RuntimeError("Device flow timed out")


async def _refresh_access_token(
    client: httpx.AsyncClient,
    refresh_token: str,
    enterprise_domain: str | None,
) -> dict[str, object]:
    domain = enterprise_domain or "github.com"
    raw = _record(
        await _fetch_json(
            client,
            "GET",
            _urls(domain)["copilot_token"],
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {refresh_token}",
                **_COPILOT_HEADERS,
            },
        )
    )
    if raw is None:
        raise RuntimeError("Invalid Copilot token response")
    token = raw.get("token")
    expires_at = _number(raw.get("expires_at"))
    if not isinstance(token, str) or expires_at is None:
        raise RuntimeError("Invalid Copilot token response fields")
    return {
        "refresh": refresh_token,
        "access": token,
        "expires": int(expires_at * 1000 - 5 * 60 * 1000),
        "enterpriseUrl": enterprise_domain,
    }


def _is_selectable_model(item: Mapping[str, object]) -> bool:
    policy = _record(item.get("policy")) or {}
    capabilities = _record(item.get("capabilities")) or {}
    supports = _record(capabilities.get("supports")) or {}
    return (
        item.get("model_picker_enabled") is True
        and policy.get("state") != "disabled"
        and supports.get("tool_calls") is not False
    )


async def _fetch_available_model_ids(
    client: httpx.AsyncClient,
    copilot_token: str,
    enterprise_domain: str | None,
) -> list[str]:
    base_url = get_github_copilot_base_url(copilot_token, enterprise_domain)
    raw = _record(
        await _fetch_json(
            client,
            "GET",
            f"{base_url}/models",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {copilot_token}",
                **_COPILOT_HEADERS,
                "X-GitHub-Api-Version": _COPILOT_API_VERSION,
            },
            timeout=5.0,
        )
    )
    data = raw.get("data") if raw else None
    if not isinstance(data, list):
        raise RuntimeError("Invalid Copilot models response")
    return [
        str(item["id"])
        for item in data
        if isinstance(item, Mapping) and isinstance(item.get("id"), str) and _is_selectable_model(item)
    ]


async def refresh_github_copilot_token(
    refresh_token: str,
    enterprise_domain: str | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    async with _use_client(client) as active_client:
        credential = await _refresh_access_token(active_client, refresh_token, enterprise_domain)
        credential["availableModelIds"] = await _fetch_available_model_ids(
            active_client,
            str(credential["access"]),
            enterprise_domain,
        )
        return credential


async def _enable_model(
    client: httpx.AsyncClient,
    token: str,
    model_id: str,
    enterprise_domain: str | None,
) -> bool:
    base_url = get_github_copilot_base_url(token, enterprise_domain)
    try:
        response = await client.post(
            f"{base_url}/models/{model_id}/policy",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                **_COPILOT_HEADERS,
                "openai-intent": "chat-policy",
                "x-interaction-type": "chat-policy",
            },
            json={"state": "enabled"},
        )
        return response.is_success
    except Exception:
        return False


async def _enable_all_models(client: httpx.AsyncClient, token: str, enterprise_domain: str | None) -> None:
    from travis.ai.builtin_models import load_builtin_models

    model_ids = [model.id for model in load_builtin_models() if model.provider == "github-copilot"]
    await asyncio.gather(
        *(_enable_model(client, token, model_id, enterprise_domain) for model_id in model_ids)
    )


async def login_github_copilot(
    callbacks: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    entered = await _invoke(
        callbacks.get("onPrompt") or callbacks.get("on_prompt"),
        {
            "message": "GitHub Enterprise URL/domain (blank for github.com)",
            "placeholder": "company.ghe.com",
            "allowEmpty": True,
        },
    )
    signal = callbacks.get("signal")
    if signal_aborted(signal):
        raise RuntimeError("Login cancelled")
    raw_enterprise = str(entered or "").strip()
    enterprise_domain = normalize_domain(raw_enterprise)
    if raw_enterprise and not enterprise_domain:
        raise RuntimeError("Invalid GitHub Enterprise URL/domain")
    domain = enterprise_domain or "github.com"
    async with _use_client(client) as active_client:
        device = await _start_device_flow(active_client, domain)
        await _invoke(
            callbacks.get("onDeviceCode") or callbacks.get("on_device_code"),
            {
                "userCode": device["user_code"],
                "verificationUri": device["verification_uri"],
                "intervalSeconds": device.get("interval"),
                "expiresInSeconds": device.get("expires_in"),
            },
        )
        github_access_token = await _poll_for_github_access_token(active_client, domain, device, signal)
        credential = await _refresh_access_token(active_client, github_access_token, enterprise_domain)
        await _invoke(callbacks.get("onProgress") or callbacks.get("on_progress"), "Enabling models...")
        await _enable_all_models(active_client, str(credential["access"]), enterprise_domain)
        credential["availableModelIds"] = await _fetch_available_model_ids(
            active_client,
            str(credential["access"]),
            enterprise_domain,
        )
        return credential


async def _refresh_stored_credential(credential: Mapping[str, object]) -> dict[str, object]:
    enterprise = credential.get("enterpriseUrl")
    domain = normalize_domain(enterprise) if isinstance(enterprise, str) else None
    return await refresh_github_copilot_token(str(credential.get("refresh") or ""), domain)


def _credential_api_key(credential: Mapping[str, object]) -> str:
    return str(credential.get("access") or "")


def _credential_auth(credential: Mapping[str, object]) -> dict[str, str]:
    access = _credential_api_key(credential)
    enterprise = credential.get("enterpriseUrl")
    domain = normalize_domain(enterprise) if isinstance(enterprise, str) else None
    return {
        "apiKey": access,
        "baseUrl": get_github_copilot_base_url(access, domain),
    }


def _modify_models(models: list[object], credential: Mapping[str, object]) -> list[object]:
    access = _credential_api_key(credential)
    enterprise = credential.get("enterpriseUrl")
    domain = normalize_domain(enterprise) if isinstance(enterprise, str) else None
    base_url = get_github_copilot_base_url(access, domain)
    available_raw = credential.get("availableModelIds")
    available = (
        {str(model_id) for model_id in available_raw}
        if isinstance(available_raw, list)
        else None
    )
    result: list[object] = []
    for model in models:
        if getattr(model, "provider", None) != "github-copilot":
            result.append(model)
            continue
        if available is not None and str(getattr(model, "id", "")) not in available:
            continue
        result.append(replace(model, base_url=base_url))
    return result


def github_copilot_oauth_config() -> dict[str, object]:
    return {
        "name": "GitHub Copilot",
        "login": login_github_copilot,
        "refreshToken": _refresh_stored_credential,
        "getApiKey": _credential_api_key,
        "toAuth": _credential_auth,
        "modifyModels": _modify_models,
    }


__all__ = [
    "get_github_copilot_base_url",
    "github_copilot_oauth_config",
    "login_github_copilot",
    "normalize_domain",
    "refresh_github_copilot_token",
]
