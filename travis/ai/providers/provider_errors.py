"""OpenAI-compatible provider streaming over HTTP server-sent events."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterable, Iterator

import httpx

from travis.ai.env_config import ModelConfig, load_model_config
from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers._shared import blank_assistant_message as _blank
from travis.ai.providers.capabilities import build_generation_payload
from travis.ai.providers.catalog import get_provider_profile, resolve_provider_runtime
from travis.ai.providers.message_sanitization import repair_tool_call_arguments
from travis.ai.providers.params import GenerationParams, merge_generation_params
from travis.ai.providers.transports import get_transport
from travis.ai.stream import ApiProvider
from travis.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    Tool,
    ToolCall,
    ToolResultMessage,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)
from travis.ai.validation import ToolValidationError, validate_tool_arguments
PROVIDER_API = "openai-completions"
PARTIAL_STREAM_STUB_ID = "partial-stream-stub"
PARTIAL_STREAM_DROPPED_TOOL_CALLS_CODE = "partial_stream_dropped_tool_calls"
MALFORMED_STREAMED_TOOL_CALL_ARGUMENTS_CODE = "malformed_streamed_tool_call_arguments"
LEAKED_TOOL_PROTOCOL_TEXT_CODE = "leaked_tool_protocol_text"
_MUTATING_TOOL_REQUIRED_ARGUMENTS = {"write": ("path", "content")}
_VALID_JSON_ESCAPES = {'"', "\\", "/", "b", "f", "n", "r", "t", "u"}
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")
_BILLING_PATTERNS = (
    "key limit exceeded",
    "spending limit",
    "insufficient credits",
    "insufficient_quota",
    "insufficient balance",
    "credit balance",
    "credits exhausted",
    "no usable credits",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "plan does not include",
    "out of funds",
    "run out of funds",
)
_OPENROUTER_POLICY_PATTERNS = (
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
)
_OPENROUTER_PROMPT_GUARDRAIL_PATTERNS = (
    "prompt injection patterns detected",
    "system_prefix_spoofing",
)
_TOOL_CALL_LEAK_PATTERN = re.compile(r"(?:^|[\s>|])to=functions\.[A-Za-z_][\w.]*", re.IGNORECASE)
_TOOL_PROTOCOL_XML_BLOCK_PATTERN = re.compile(
    r"(?is)"
    r"<(?:tool_call|tool_calls|tool_response|function_call|function_calls)\b[^>]*>.*?"
    r"</(?:tool_call|tool_calls|tool_response|function_call|function_calls)>"
    r"|<function\s+name\s*=\s*[\"'][^\"']+[\"'][^>]*>.*?</function>"
)
_TOOL_PROTOCOL_XML_LINE_PATTERN = re.compile(
    r"(?im)^[ \t]*</?(?:parameter|function|tool_call|tool_calls|tool_response|function_call|function_calls)"
    r"(?:[\s=>][^>]*)?>[ \t]*(?:\r?\n|$)"
)
_TOOL_PROTOCOL_XML_PREFIX_PATTERN = re.compile(
    r"(?is)(?:^|[\s>])(?:"
    r"</?(?:tool_call|tool_calls|tool_response|function_call|function_calls|parameter)(?:$|[\s=>_/])"
    r"|<tool(?:$|_)"
    r"|</tool(?:$|[_>\s])"
    r"|<function\s+(?:$|name\s*=)"
    r"|</function(?:$|[\s>])"
    r"|<parameter(?:$|[\s=>])"
    r"|</parameter(?:$|[\s>])"
    r")"
)
_PROVIDER_ERROR_DETAIL_HEAD_CHARS = 450
_PROVIDER_ERROR_DETAIL_TAIL_CHARS = 300
_PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER = "... [truncated provider error body] ..."
_NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
_NON_VISION_TOOL_IMAGE_PLACEHOLDER = "(tool image omitted: model does not support images)"
_STREAMING_TOOL_ARGUMENT_PREVIEW_MAX_CHARS = 8_192


def _format_provider_exception(error: Exception, model: Model, configured_model: str | None = None) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return _format_http_status_error(error, model, configured_model)
    return str(error)


def _format_http_status_error(error: httpx.HTTPStatusError, model: Model, configured_model: str | None = None) -> str:
    response = error.response
    status = response.status_code
    reason = response.reason_phrase
    provider = _provider_label(model.provider)
    requested_model = model.id or configured_model or "unknown"
    detail = _extract_response_error_message(response) or reason or str(error)
    lower_detail = detail.lower()
    display_detail = _truncate_provider_error_detail(detail)

    prefix = f"{provider} API error (HTTP {status}{f' {reason}' if reason else ''}) for model {requested_model}."
    if model.provider == "openrouter" and any(pattern in lower_detail for pattern in _OPENROUTER_POLICY_PATTERNS):
        return (
            f"{prefix} OpenRouter blocked the selected endpoint because of account privacy/data-policy settings. "
            "Open https://openrouter.ai/settings/privacy and allow a compatible endpoint, or choose another model. "
            f"Provider message: {display_detail}"
        )
    if status == 403 and model.provider == "openrouter" and _is_openrouter_prompt_guardrail_detail(lower_detail):
        return (
            f"OpenRouter prompt-injection guardrail blocked the request (HTTP 403) for model {requested_model}. "
            "The provider rejected prompt/tool-result content before generation; this is not an API-key failure. "
            "Compact or narrow raw tool output, then retry. "
            f"Provider message: {display_detail}"
        )
    if status == 403:
        if any(pattern in lower_detail for pattern in _BILLING_PATTERNS):
            return (
                f"{provider} billing or account limit failed (HTTP 403) for model {requested_model}. "
                "Check credits, spending limits, plan entitlement, and model access. "
                f"Provider message: {display_detail}"
            )
        return (
            f"{provider} authorization failed (HTTP 403) for model {requested_model}. "
            f"Check {_provider_api_key_label(model.provider)}, account credits, and model access. "
            f"Provider message: {display_detail}"
        )
    if status == 401:
        return (
            f"{provider} authentication failed (HTTP 401) for model {requested_model}. "
            f"Check {_provider_api_key_label(model.provider)} and re-authenticate if needed. "
            f"Provider message: {display_detail}"
        )
    if status == 402:
        return (
            f"{provider} billing or quota failed (HTTP 402) for model {requested_model}. "
            "Add credits or update billing with that provider, then retry. "
            f"Provider message: {display_detail}"
        )
    return f"{prefix} Provider message: {display_detail}"


def _truncate_provider_error_detail(detail: str) -> str:
    max_chars = (
        _PROVIDER_ERROR_DETAIL_HEAD_CHARS
        + len(_PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER)
        + _PROVIDER_ERROR_DETAIL_TAIL_CHARS
    )
    if len(detail) <= max_chars:
        return detail
    return (
        detail[:_PROVIDER_ERROR_DETAIL_HEAD_CHARS]
        + _PROVIDER_ERROR_DETAIL_TRUNCATION_MARKER
        + detail[-_PROVIDER_ERROR_DETAIL_TAIL_CHARS:]
    )


def _extract_response_error_message(response: httpx.Response) -> str:
    text = _read_response_text(response)
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        extracted = _extract_error_message_from_payload(payload)
        if extracted:
            return extracted
    return text


def _read_response_text(response: httpx.Response) -> str:
    try:
        return response.text.strip()
    except httpx.ResponseNotRead:
        try:
            response.read()
            return response.text.strip()
        except Exception:  # noqa: BLE001 - best-effort provider error extraction
            return ""
    except Exception:  # noqa: BLE001 - best-effort provider error extraction
        return ""


def _extract_error_message_from_payload(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("error") or error.get("detail")
        metadata = error.get("metadata")
        parts = [message.strip()] if isinstance(message, str) and message.strip() else []
        if isinstance(metadata, dict):
            patterns = metadata.get("patterns")
            if isinstance(patterns, list):
                normalized_patterns = [str(pattern).strip() for pattern in patterns if str(pattern).strip()]
                if normalized_patterns:
                    parts.append("Patterns: " + ", ".join(normalized_patterns))
            raw = metadata.get("raw")
            if isinstance(raw, str) and raw.strip():
                try:
                    nested = json.loads(raw)
                except json.JSONDecodeError:
                    if not parts:
                        return raw.strip()
                if isinstance(nested, dict):
                    nested_message = _extract_error_message_from_payload(nested)
                    if nested_message:
                        parts.append(nested_message)
        if parts:
            return ". ".join(parts)
    message = payload.get("message") or payload.get("detail")
    return message.strip() if isinstance(message, str) and message.strip() else ""


def _is_openrouter_prompt_guardrail_detail(lower_detail: str) -> bool:
    return any(pattern in lower_detail for pattern in _OPENROUTER_PROMPT_GUARDRAIL_PATTERNS)


def _provider_label(provider: str) -> str:
    if provider == "openrouter":
        return "OpenRouter"
    return provider or "Provider"


def _provider_api_key_label(provider: str) -> str:
    if provider == "openrouter":
        return "OPENROUTER_API_KEY"
    return "the configured API key"
