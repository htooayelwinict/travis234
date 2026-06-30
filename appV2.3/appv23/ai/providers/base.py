"""Hermes-style provider profiles and transports for appv23.

Profiles are declarative provider facts. Transports own provider wire payload
shape. Client lifecycle, streaming, retries, and auth stay outside this layer.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

OMIT_TEMPERATURE = object()


@dataclass
class NormalizedToolCall:
    """Hermes-style normalized tool call from any provider transport."""

    id: str | None
    name: str
    arguments: str
    provider_data: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def type(self) -> str:
        return "function"

    @property
    def function(self) -> "NormalizedToolCall":
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
    """Hermes-style response shape shared by provider transports."""

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
    def reasoning_details(self):
        return (self.provider_data or {}).get("reasoning_details")

    @property
    def codex_reasoning_items(self):
        return (self.provider_data or {}).get("codex_reasoning_items")

    @property
    def codex_message_items(self):
        return (self.provider_data or {}).get("codex_message_items")


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    api_mode: str = "chat_completions"
    aliases: tuple[str, ...] = ()
    display_name: str = ""
    description: str = ""
    signup_url: str = ""
    env_vars: tuple[str, ...] = ()
    base_url: str = ""
    models_url: str = ""
    auth_type: str = "api_key"
    supports_health_check: bool = True
    supports_vision: bool = False
    supports_vision_tool_messages: bool = True
    fallback_models: tuple[str, ...] = ()
    hostname: str = ""
    default_headers: dict[str, str] = field(default_factory=dict)
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    default_aux_model: str = ""

    def get_hostname(self) -> str:
        if self.hostname:
            return self.hostname
        if not self.base_url:
            return ""
        from urllib.parse import urlparse

        return urlparse(self.base_url).hostname or ""

    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return messages

    def build_extra_body(
        self,
        *,
        session_id: str | None = None,
        provider_preferences: dict[str, Any] | None = None,
        model: str | None = None,
        **_context: Any,
    ) -> dict[str, Any]:
        return {}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict[str, Any] | None = None,
        **_context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return {}, {}

    def get_max_tokens(self, model: str | None) -> int | None:
        return self.default_max_tokens

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        effective_base = base_url or self.base_url
        url = (self.models_url or "").strip()
        if not url:
            if not effective_base:
                return None
            url = effective_base.rstrip("/") + "/models"

        request = urllib.request.Request(url)
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "appv23")
        for key, value in self.default_headers.items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode())
            items = data if isinstance(data, list) else data.get("data", [])
            return [item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]
        except Exception as exc:
            logger.debug("fetch_models(%s): %s", self.name, exc)
            return None


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
    ) -> dict[str, Any]:
        ...

    def normalize_response(self, response: Any, **kwargs: Any) -> NormalizedResponse:
        ...
