"""Built-in subscription OAuth flows for Travis234."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from travis.ai.providers._shared import signal_aborted
from travis.ai.providers.codex_auth import extract_codex_account_id

_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_AUTH_BASE_URL = "https://auth.openai.com"
_CODEX_AUTHORIZE_URL = f"{_CODEX_AUTH_BASE_URL}/oauth/authorize"
_CODEX_TOKEN_URL = f"{_CODEX_AUTH_BASE_URL}/oauth/token"
_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
_CODEX_DEVICE_USER_CODE_URL = f"{_CODEX_AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
_CODEX_DEVICE_TOKEN_URL = f"{_CODEX_AUTH_BASE_URL}/api/accounts/deviceauth/token"
_CODEX_DEVICE_VERIFICATION_URI = f"{_CODEX_AUTH_BASE_URL}/codex/device"
_CODEX_DEVICE_REDIRECT_URI = f"{_CODEX_AUTH_BASE_URL}/deviceauth/callback"
_CODEX_SCOPE = "openid profile email offline_access"
_CODEX_DEVICE_TIMEOUT_SECONDS = 15 * 60

_ANTHROPIC_CLIENT_ID = base64.b64decode(
    "OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl"
).decode("utf-8")
_ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_ANTHROPIC_REDIRECT_URI = "http://localhost:53692/callback"
_ANTHROPIC_SCOPE = (
    "org:create_api_key user:profile user:inference user:sessions:claude_code "
    "user:mcp_servers user:file_upload"
)


async def _invoke(callback: object, *args: object) -> object:
    if not callable(callback):
        return None
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


async def _request_manual_authorization_input(
    callbacks: Mapping[str, object],
    message: str,
) -> object:
    """Request pasted OAuth input using the callback's documented signature."""
    manual_callback = callbacks.get("onManualCodeInput") or callbacks.get("on_manual_code_input")
    if callable(manual_callback):
        return await _invoke(manual_callback)
    return await _invoke(
        callbacks.get("onPrompt") or callbacks.get("on_prompt"),
        {"message": message},
    )


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _parse_authorization_input(value: object) -> tuple[str | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, None
    try:
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc:
            params = parse_qs(parsed.query)
            return (params.get("code") or [None])[0], (params.get("state") or [None])[0]
    except ValueError:
        pass
    if "#" in text:
        code, state = text.split("#", 1)
        return code or None, state or None
    if "code=" in text:
        params = parse_qs(text)
        return (params.get("code") or [None])[0], (params.get("state") or [None])[0]
    return text, None


def _auth_page(title: str, message: str) -> bytes:
    return (
        "<!doctype html><meta charset=utf-8><title>Travis234 authentication</title>"
        f"<h1>{title}</h1><p>{message}</p>"
    ).encode("utf-8")


class _CallbackServer:
    def __init__(self, *, host: str, port: int, path: str, state: str, provider: str) -> None:
        self._result: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=1)
        result_queue = self._result

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                code = (params.get("code") or [None])[0]
                returned_state = (params.get("state") or [None])[0]
                error = (params.get("error") or [None])[0]
                if parsed.path != path:
                    self._send(404, _auth_page("Authentication failed", "Callback route not found."))
                    return
                if error:
                    self._send(400, _auth_page("Authentication failed", str(error)))
                    return
                if not code or returned_state != state:
                    self._send(400, _auth_page("Authentication failed", "Missing code or state mismatch."))
                    return
                self._send(200, _auth_page(f"{provider} authentication complete", "You can close this window."))
                try:
                    result_queue.put_nowait((str(code), str(returned_state)))
                except queue.Full:
                    pass

            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    async def wait(self, timeout_seconds: float) -> tuple[str, str] | None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            try:
                return self._result.get_nowait()
            except queue.Empty:
                await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return None

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)


def _start_callback_server(*, port: int, path: str, state: str, provider: str) -> _CallbackServer | None:
    host = os.environ.get("TRAVIS234_OAUTH_CALLBACK_HOST", "127.0.0.1")
    try:
        return _CallbackServer(host=host, port=port, path=path, state=state, provider=provider)
    except OSError:
        return None


async def _read_codex_token_response(response: httpx.Response, operation: str) -> dict[str, object]:
    if not response.is_success:
        body = response.text.strip()
        raise RuntimeError(
            f"OpenAI Codex token {operation} failed ({response.status_code}): "
            f"{body or response.reason_phrase}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"OpenAI Codex token {operation} returned invalid JSON")
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires_in, (int, float)):
        raise RuntimeError(f"OpenAI Codex token {operation} response missing fields")
    return {
        "access": access,
        "refresh": refresh,
        "expires": int(time.time() * 1000 + float(expires_in) * 1000),
        "accountId": extract_codex_account_id(access),
    }


async def _exchange_codex_code(
    client: httpx.AsyncClient,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict[str, object]:
    response = await client.post(
        _CODEX_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": _CODEX_CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return await _read_codex_token_response(response, "exchange")


async def _codex_device_login(callbacks: Mapping[str, object], client: httpx.AsyncClient) -> dict[str, object]:
    signal = callbacks.get("signal")
    response = await client.post(
        _CODEX_DEVICE_USER_CODE_URL,
        json={"client_id": _CODEX_CLIENT_ID},
        headers={"Content-Type": "application/json"},
    )
    if not response.is_success:
        raise RuntimeError(f"OpenAI Codex device code request failed ({response.status_code}): {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid OpenAI Codex device code response")
    device_id = payload.get("device_auth_id")
    user_code = payload.get("user_code")
    interval = float(payload.get("interval") or 5)
    if not isinstance(device_id, str) or not isinstance(user_code, str) or interval < 0:
        raise RuntimeError("Invalid OpenAI Codex device code response")
    await _invoke(
        callbacks.get("onDeviceCode") or callbacks.get("on_device_code"),
        {
            "userCode": user_code,
            "verificationUri": _CODEX_DEVICE_VERIFICATION_URI,
            "intervalSeconds": interval,
            "expiresInSeconds": _CODEX_DEVICE_TIMEOUT_SECONDS,
        },
    )
    deadline = time.monotonic() + _CODEX_DEVICE_TIMEOUT_SECONDS
    delay = interval
    while time.monotonic() < deadline:
        if signal_aborted(signal):
            raise RuntimeError("Login cancelled")
        await asyncio.sleep(delay)
        response = await client.post(
            _CODEX_DEVICE_TOKEN_URL,
            json={"device_auth_id": device_id, "user_code": user_code},
            headers={"Content-Type": "application/json"},
        )
        if response.is_success:
            token = response.json()
            if not isinstance(token, dict) or not isinstance(token.get("authorization_code"), str) or not isinstance(token.get("code_verifier"), str):
                raise RuntimeError("Invalid OpenAI Codex device authorization response")
            return await _exchange_codex_code(
                client,
                str(token["authorization_code"]),
                str(token["code_verifier"]),
                _CODEX_DEVICE_REDIRECT_URI,
            )
        try:
            parsed = response.json()
        except ValueError:
            parsed = {}
        error_value = parsed.get("error") if isinstance(parsed, dict) else None
        error_code = error_value.get("code") if isinstance(error_value, dict) else error_value
        if response.status_code in {403, 404} or error_code == "deviceauth_authorization_pending":
            continue
        if error_code == "slow_down":
            delay += 5
            continue
        raise RuntimeError(f"OpenAI Codex device authorization failed ({response.status_code}): {response.text}")
    raise TimeoutError("OpenAI Codex device authorization timed out")


async def login_openai_codex(
    callbacks: Mapping[str, object],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    selected = await _invoke(
        callbacks.get("onSelect") or callbacks.get("on_select"),
        {
            "message": "Select OpenAI Codex login method:",
            "options": [
                {"id": "browser", "label": "Browser login (default)"},
                {"id": "device_code", "label": "Device code login (headless)"},
            ],
        },
    )
    method = str(selected or "browser")
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=30)
    try:
        if method == "device_code":
            return await _codex_device_login(callbacks, active_client)
        if method != "browser":
            raise RuntimeError(f"Unknown OpenAI Codex login method: {method}")

        verifier, challenge = _pkce()
        state = secrets.token_hex(16)
        params = {
            "response_type": "code",
            "client_id": _CODEX_CLIENT_ID,
            "redirect_uri": _CODEX_REDIRECT_URI,
            "scope": _CODEX_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "travis234",
        }
        server = _start_callback_server(port=1455, path="/auth/callback", state=state, provider="OpenAI")
        await _invoke(
            callbacks.get("onAuth") or callbacks.get("on_auth"),
            {
                "url": f"{_CODEX_AUTHORIZE_URL}?{urlencode(params)}",
                "instructions": "Complete login in your browser to finish.",
            },
        )
        try:
            result = await server.wait(300) if server is not None else None
        finally:
            if server is not None:
                server.close()
        if result is None:
            manual = await _request_manual_authorization_input(
                callbacks,
                "Paste the authorization code or full redirect URL:",
            )
            code, returned_state = _parse_authorization_input(manual)
        else:
            code, returned_state = result
        if returned_state and returned_state != state:
            raise RuntimeError("OAuth state mismatch")
        if not code:
            raise RuntimeError("Missing authorization code")
        return await _exchange_codex_code(active_client, code, verifier, _CODEX_REDIRECT_URI)
    finally:
        if owns_client:
            await active_client.aclose()


async def refresh_openai_codex(credential: Mapping[str, object]) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _CODEX_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": str(credential.get("refresh") or ""),
                "client_id": _CODEX_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return await _read_codex_token_response(response, "refresh")


async def _anthropic_token_request(body: Mapping[str, object]) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            _ANTHROPIC_TOKEN_URL,
            json=dict(body),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    if not response.is_success:
        raise RuntimeError(f"Anthropic token request failed ({response.status_code}): {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Anthropic token request returned invalid JSON")
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires_in, (int, float)):
        raise RuntimeError("Anthropic token response missing fields")
    return {
        "access": access,
        "refresh": refresh,
        "expires": int(time.time() * 1000 + float(expires_in) * 1000 - 5 * 60 * 1000),
    }


async def login_anthropic(callbacks: Mapping[str, object]) -> dict[str, object]:
    verifier, challenge = _pkce()
    state = verifier
    params = {
        "code": "true",
        "client_id": _ANTHROPIC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _ANTHROPIC_REDIRECT_URI,
        "scope": _ANTHROPIC_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    server = _start_callback_server(port=53692, path="/callback", state=state, provider="Anthropic")
    await _invoke(
        callbacks.get("onAuth") or callbacks.get("on_auth"),
        {
            "url": f"{_ANTHROPIC_AUTHORIZE_URL}?{urlencode(params)}",
            "instructions": "Complete login in your browser to finish.",
        },
    )
    try:
        result = await server.wait(300) if server is not None else None
    finally:
        if server is not None:
            server.close()
    if result is None:
        manual = await _request_manual_authorization_input(
            callbacks,
            "Paste the authorization code or full redirect URL:",
        )
        code, returned_state = _parse_authorization_input(manual)
    else:
        code, returned_state = result
    returned_state = returned_state or state
    if returned_state != state:
        raise RuntimeError("OAuth state mismatch")
    if not code:
        raise RuntimeError("Missing authorization code")
    await _invoke(callbacks.get("onProgress") or callbacks.get("on_progress"), "Exchanging authorization code for tokens...")
    return await _anthropic_token_request(
        {
            "grant_type": "authorization_code",
            "client_id": _ANTHROPIC_CLIENT_ID,
            "code": code,
            "state": returned_state,
            "redirect_uri": _ANTHROPIC_REDIRECT_URI,
            "code_verifier": verifier,
        }
    )


async def refresh_anthropic(credential: Mapping[str, object]) -> dict[str, object]:
    return await _anthropic_token_request(
        {
            "grant_type": "refresh_token",
            "client_id": _ANTHROPIC_CLIENT_ID,
            "refresh_token": str(credential.get("refresh") or ""),
        }
    )


def _access_auth(credential: Mapping[str, object]) -> dict[str, object]:
    return {"apiKey": str(credential.get("access") or "")}


def openai_codex_oauth_config() -> dict[str, object]:
    return {
        "name": "OpenAI (ChatGPT Plus/Pro)",
        "login": login_openai_codex,
        "refreshToken": refresh_openai_codex,
        "toAuth": _access_auth,
    }


def anthropic_oauth_config() -> dict[str, object]:
    return {
        "name": "Anthropic (Claude Pro/Max)",
        "login": login_anthropic,
        "refreshToken": refresh_anthropic,
        "toAuth": _access_auth,
    }


__all__ = [
    "anthropic_oauth_config",
    "login_anthropic",
    "login_openai_codex",
    "openai_codex_oauth_config",
    "refresh_anthropic",
    "refresh_openai_codex",
]
