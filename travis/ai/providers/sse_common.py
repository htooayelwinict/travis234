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



class _StartEventState:
    def __init__(self, message: AssistantMessage) -> None:
        self.message = message
        self.started = False

    def ensure(self) -> StartEvent | None:
        if self.started:
            return None
        self.started = True
        return StartEvent(partial=self.message)


def _map_stop_reason(reason: str | None) -> tuple[str, str | None]:
    if reason is None:
        return "stop", None
    if reason in ("stop", "end"):
        return "stop", None
    if reason == "length":
        return "length", None
    if reason in ("function_call", "tool_calls"):
        return "toolUse", None
    if reason in ("content_filter", "network_error"):
        return "error", f"Provider finish_reason: {reason}"
    return "error", f"Provider finish_reason: {reason}"

def _iter_sse_data(
    lines: Iterable[str],
    *,
    data_idle_timeout_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[str]:
    last_data_at = clock()
    for raw in lines:
        if (
            data_idle_timeout_seconds is not None
            and data_idle_timeout_seconds > 0
            and clock() - last_data_at > data_idle_timeout_seconds
        ):
            seconds = int(data_idle_timeout_seconds)
            raise TimeoutError(f"SSE stream received no data events for {seconds} seconds")
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        last_data_at = clock()
        yield payload
