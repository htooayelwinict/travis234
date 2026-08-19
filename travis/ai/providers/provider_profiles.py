"""Declarative provider profile facts."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from travis.ai.providers.provider_modes import transport_mode_is_supported

logger = logging.getLogger(__name__)


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
    supports_usage_in_streaming: bool = True
    fallback_models: tuple[str, ...] = ()
    hostname: str = ""
    default_headers: dict[str, str] = field(default_factory=dict)
    fixed_temperature: Any = None
    default_max_tokens: int | None = None
    default_aux_model: str = ""

    @property
    def transport_available(self) -> bool:
        return transport_mode_is_supported(self.api_mode)

    def auth_headers(self, credential: str, *, credential_kind: str = "api_key") -> dict[str, str]:
        from travis.ai.providers.request_auth import build_request_auth_headers

        return build_request_auth_headers(
            self.name,
            self.api_mode,
            credential,
            credential_kind=credential_kind,
        )

    def get_hostname(self) -> str:
        if self.hostname:
            return self.hostname
        if not self.base_url:
            return ""
        from urllib.parse import urlparse

        return urlparse(self.base_url).hostname or ""

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
        request.add_header("User-Agent", "travis")
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


__all__ = ["ProviderProfile"]
