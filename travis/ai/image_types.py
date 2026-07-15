"""Independent image-generation model and result value types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

import httpx

from travis.agent.types import AbortSignal

if TYPE_CHECKING:
    from travis.ai.models import Models


@dataclass(frozen=True)
class ImageModel:
    id: str
    provider: str
    api: str
    base_url: str = ""
    sizes: tuple[str, ...] = ()
    output: tuple[str, ...] = ("image",)

    def __post_init__(self) -> None:
        if not self.id or not self.provider or not self.api:
            raise ValueError("image model id, provider, and api are required")


@dataclass(frozen=True)
class ImageGenerationOptions:
    size: str | None = None
    n: int = 1
    quality: str | None = None
    style: str | None = None
    api_key: str | None = field(default=None, repr=False)
    headers: Mapping[str, str] | None = None
    timeout_seconds: float = 60.0
    max_response_bytes: int = 20 * 1024 * 1024
    signal: AbortSignal | None = field(default=None, repr=False, compare=False)
    models: Models | None = field(default=None, repr=False, compare=False)
    client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.n <= 0 or self.n > 10:
            raise ValueError("image count must be between 1 and 10")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")


@dataclass(frozen=True)
class GeneratedImage:
    mime_type: str
    data: bytes | None = None
    url: str | None = None
    revised_prompt: str | None = None

    def __post_init__(self) -> None:
        if bool(self.data is not None) == bool(self.url is not None):
            raise ValueError("generated image requires exactly one of data or url")
        if not self.mime_type.startswith("image/"):
            raise ValueError("generated image mime_type must be image/*")
        if self.url is not None and not self.url.startswith(("https://", "http://")):
            raise ValueError("generated image url must use HTTP or HTTPS")


__all__ = ["GeneratedImage", "ImageGenerationOptions", "ImageModel"]
