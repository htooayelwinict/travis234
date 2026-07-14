"""Factual provider error-body passthrough."""

from __future__ import annotations

import json

import httpx

from travis.ai.types import Model

MAX_PROVIDER_ERROR_BODY_CHARS = 4_000


def _truncate_error_text(text: str, max_chars: int = MAX_PROVIDER_ERROR_BODY_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"


def _read_response_text(response: httpx.Response) -> str:
    try:
        return response.text.strip()
    except httpx.ResponseNotRead:
        try:
            response.read()
            return response.text.strip()
        except Exception:
            return ""
    except Exception:
        return ""


def _normalize_body(text: str) -> str:
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _truncate_error_text(text.strip())
    try:
        compact = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        compact = text.strip()
    return _truncate_error_text(compact)


def _format_provider_exception(
    error: Exception,
    model: Model,
    configured_model: str | None = None,
) -> str:
    del configured_model
    if not isinstance(error, httpx.HTTPStatusError):
        return str(error)
    status = error.response.status_code
    body = _normalize_body(_read_response_text(error.response))
    if not body:
        return str(error)
    if model.api == "openai-responses":
        return f"OpenAI API error ({status}): {body}"
    if model.api == "azure-openai-responses":
        return f"Azure OpenAI API error ({status}): {body}"
    return f"{status}: {body}"


__all__ = ["MAX_PROVIDER_ERROR_BODY_CHARS", "_format_provider_exception"]
