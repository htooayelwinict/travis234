"""Fallback transport for unknown provider API modes."""

from __future__ import annotations

from typing import Any

from travis.ai.providers.provider_contracts import NormalizedResponse


class UnsupportedTransport:
    endpoint_path = "/unsupported"

    def __init__(self, api_mode: str) -> None:
        self.api_mode = api_mode

    def convert_messages(self, messages: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
        return messages

    def convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return tools

    def build_kwargs(self, **_kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.api_mode} transport is not supported by the travis HTTP provider"
        )

    def normalize_response(self, response: Any, **_kwargs: Any) -> NormalizedResponse:
        return NormalizedResponse(content=str(response or ""), tool_calls=None, finish_reason="error")


__all__ = ["UnsupportedTransport"]
