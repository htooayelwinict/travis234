"""OpenAI Codex OAuth request headers."""

from __future__ import annotations

import base64
import json
import platform
from collections.abc import Mapping

_JWT_CLAIM_PATH = "https://api.openai.com/auth"


def extract_codex_account_id(token: str) -> str:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid token")
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        account_id = payload[_JWT_CLAIM_PATH]["chatgpt_account_id"]
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("No account ID in token")
        return account_id
    except Exception as exc:
        raise ValueError("Failed to extract accountId from token") from exc


def _set_header(headers: dict[str, str], name: str, value: str) -> None:
    lowered = name.lower()
    for key in tuple(headers):
        if key.lower() == lowered:
            del headers[key]
    headers[name] = value


def build_codex_sse_headers(
    initial_headers: Mapping[str, str],
    token: str,
    session_id: str | None,
) -> dict[str, str]:
    headers = dict(initial_headers)
    account_id = extract_codex_account_id(token)
    _set_header(headers, "Authorization", f"Bearer {token}")
    _set_header(headers, "chatgpt-account-id", account_id)
    _set_header(headers, "originator", "travis234")
    user_agent = (
        f"travis234 ({platform.system().lower()} {platform.release()}; "
        f"{platform.machine()})"
    )
    _set_header(headers, "User-Agent", user_agent)
    _set_header(headers, "OpenAI-Beta", "responses=experimental")
    _set_header(headers, "accept", "text/event-stream")
    _set_header(headers, "content-type", "application/json")
    if session_id:
        _set_header(headers, "session-id", session_id)
        _set_header(headers, "x-client-request-id", session_id)
    return headers
