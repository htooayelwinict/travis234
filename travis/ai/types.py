"""Core data types for the travis ai layer."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Union

Api = str
Provider = str
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]
Transport = Literal["sse", "websocket", "websocket-cached", "auto"]


def now_ms() -> int:
    """Unix timestamp in milliseconds (travis messages use ms timestamps)."""
    return int(time.time() * 1000)


@dataclass
class TextContent:
    text: str
    text_signature: str | None = None
    type: Literal["text"] = "text"


@dataclass
class ThinkingContent:
    thinking: str
    thinking_signature: str | None = None
    redacted: bool = False
    type: Literal["thinking"] = "thinking"


@dataclass
class ImageContent:
    data: str  # base64
    mime_type: str
    type: Literal["image"] = "image"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    thought_signature: str | None = None
    type: Literal["toolCall"] = "toolCall"


ContentBlock = Union[TextContent, ThinkingContent, ImageContent, ToolCall]


@dataclass
class CostTier:
    input_tokens_above: int
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0
    tiers: list[CostTier] = field(default_factory=list)


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int = 0
    reasoning: int = 0
    total_tokens: int = 0
    cost: Cost = field(default_factory=Cost)


def empty_usage() -> Usage:
    return Usage(cost=Cost())


@dataclass
class UserMessage:
    content: "str | list[TextContent | ImageContent]"
    timestamp: int = field(default_factory=now_ms)
    role: Literal["user"] = "user"


@dataclass
class AssistantMessage:
    content: list[ContentBlock]
    api: Api
    provider: Provider
    model: str
    usage: Usage
    stop_reason: StopReason
    response_model: str | None = None
    response_id: str | None = None
    diagnostics: list[dict[str, Any]] | None = None
    error_message: str | None = None
    timestamp: int = field(default_factory=now_ms)
    role: Literal["assistant"] = "assistant"


@dataclass
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent]
    is_error: bool
    details: Any | None = None
    added_tool_names: list[str] | None = None
    timestamp: int = field(default_factory=now_ms)
    role: Literal["toolResult"] = "toolResult"


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema (travis uses TypeBox; we use plain JSON schema)


@dataclass
class Context:
    messages: list[Message]
    system_prompt: str | None = None
    tools: list[Tool] | None = None


@dataclass
class Model:
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool = False
    thinking_level_map: dict[str, str | None] | None = None
    input: list[Literal["text", "image"]] = field(default_factory=lambda: ["text"])
    cost: Cost = field(default_factory=Cost)
    context_window: int = 0
    max_tokens: int = 0
    headers: dict[str, str] | None = None
    compat: dict[str, Any] | None = None


@dataclass
class ProviderResponse:
    status: int
    headers: dict[str, str]


@dataclass
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    omit_max_tokens: bool = False
    signal: Any | None = None
    api_key: str | None = None
    transport: Transport | None = None
    cache_retention: str | None = None
    session_id: str | None = None
    on_payload: Any | None = None
    on_headers: Any | None = None
    on_response: Any | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    timeout_ms: int | None = None
    websocket_connect_timeout_ms: int | None = None
    max_retries: int | None = None
    max_retry_delay_ms: int | None = None
    metadata: dict[str, Any] | None = None
    tool_choice: Any | None = None
    reasoning_summary: str | None = None
    service_tier: str | None = None
    text_verbosity: str | None = None
    azure_api_version: str | None = None
    azure_resource_name: str | None = None
    azure_base_url: str | None = None
    azure_deployment_name: str | None = None
    generation_params: Any | None = None


@dataclass
class SimpleStreamOptions(StreamOptions):
    reasoning: ThinkingLevel | None = None
    thinking_budgets: dict[str, int] | None = None


# --- Streaming event protocol (travis AssistantMessageEvent union) ---


@dataclass
class StartEvent:
    partial: AssistantMessage
    type: Literal["start"] = "start"


@dataclass
class TextStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["text_start"] = "text_start"


@dataclass
class TextDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["text_delta"] = "text_delta"


@dataclass
class TextEndEvent:
    content_index: int
    content: str
    partial: AssistantMessage
    type: Literal["text_end"] = "text_end"


@dataclass
class ThinkingStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["thinking_start"] = "thinking_start"


@dataclass
class ThinkingDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["thinking_delta"] = "thinking_delta"


@dataclass
class ThinkingEndEvent:
    content_index: int
    content: str
    partial: AssistantMessage
    type: Literal["thinking_end"] = "thinking_end"


@dataclass
class ToolcallStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["toolcall_start"] = "toolcall_start"


@dataclass
class ToolcallDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["toolcall_delta"] = "toolcall_delta"


@dataclass
class ToolcallEndEvent:
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage
    type: Literal["toolcall_end"] = "toolcall_end"


@dataclass
class DoneEvent:
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage
    type: Literal["done"] = "done"


@dataclass
class ErrorEvent:
    reason: Literal["aborted", "error"]
    error: AssistantMessage
    type: Literal["error"] = "error"


AssistantMessageEvent = Union[
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolcallStartEvent,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    DoneEvent,
    ErrorEvent,
]
