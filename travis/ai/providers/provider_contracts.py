"""Provider-neutral normalized values and the transport protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from travis.ai.providers.provider_profiles import ProviderProfile

OMIT_TEMPERATURE = object()


@dataclass
class NormalizedToolCall:
    """Normalized tool call from any provider transport."""

    id: str | None
    name: str
    arguments: str
    provider_data: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def type(self) -> str:
        return "function"

    @property
    def function(self) -> NormalizedToolCall:
        return self

    @property
    def call_id(self) -> str | None:
        return (self.provider_data or {}).get("call_id")

    @property
    def response_item_id(self) -> str | None:
        return (self.provider_data or {}).get("response_item_id")

    @property
    def extra_content(self) -> dict[str, Any] | None:
        return (self.provider_data or {}).get("extra_content")


@dataclass
class NormalizedUsage:
    """Provider token usage normalized at the transport boundary."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class NormalizedResponse:
    """Response shape shared by provider transports."""

    content: str | None
    tool_calls: list[NormalizedToolCall] | None
    finish_reason: str
    reasoning: str | None = None
    usage: NormalizedUsage | None = None
    provider_data: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def reasoning_content(self) -> str | None:
        return (self.provider_data or {}).get("reasoning_content")

    @property
    def reasoning_details(self) -> object:
        return (self.provider_data or {}).get("reasoning_details")

    @property
    def codex_reasoning_items(self) -> object:
        return (self.provider_data or {}).get("codex_reasoning_items")

    @property
    def codex_message_items(self) -> object:
        return (self.provider_data or {}).get("codex_message_items")


class ProviderTransport(Protocol):
    api_mode: str
    endpoint_path: str

    def convert_messages(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        ...

    def convert_tools(self, tools: list[dict[str, Any]]) -> Any:
        ...

    def build_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        profile: ProviderProfile,
        stream: bool,
        temperature: float | None,
        max_tokens: int | None,
        provider_preferences: dict[str, Any] | None = None,
        session_id: str | None = None,
        reasoning_config: dict[str, Any] | None = None,
        request_overrides: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        ...

    def normalize_response(self, response: Any, **kwargs: Any) -> NormalizedResponse:
        ...


__all__ = [
    "OMIT_TEMPERATURE",
    "NormalizedResponse",
    "NormalizedToolCall",
    "NormalizedUsage",
    "ProviderTransport",
]
