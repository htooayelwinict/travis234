"""Optional image-generation API registry and OpenRouter adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

import httpx

from travis.ai.image_types import GeneratedImage, ImageGenerationOptions, ImageModel
from travis.ai.types import Model

ImageProvider = Callable[
    [ImageModel, str, ImageGenerationOptions],
    Sequence[GeneratedImage] | Awaitable[Sequence[GeneratedImage]],
]


class ImageGenerationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_PROVIDERS: dict[str, ImageProvider] = {}
_PROVIDER_LOCK = threading.RLock()


def register_image_provider(name: str, generate: ImageProvider) -> None:
    if not name or not callable(generate):
        raise ValueError("image provider name and callable are required")
    with _PROVIDER_LOCK:
        _PROVIDERS[name] = generate


def unregister_image_provider(name: str) -> None:
    with _PROVIDER_LOCK:
        _PROVIDERS.pop(name, None)


async def generate_images(
    model: ImageModel,
    prompt: str,
    options: ImageGenerationOptions | None = None,
) -> tuple[GeneratedImage, ...]:
    if not prompt.strip():
        raise ImageGenerationError("invalid_prompt", "Image prompt must not be empty")
    resolved_options = options or ImageGenerationOptions()
    if resolved_options.signal is not None and resolved_options.signal.aborted:
        raise ImageGenerationError("aborted", "Image generation aborted")
    with _PROVIDER_LOCK:
        provider = _PROVIDERS.get(model.api)
    if provider is None:
        raise ImageGenerationError(
            "provider",
            f"No image API provider registered for api: {model.api}",
        )

    request_model, request_options = _resolve_auth(model, resolved_options)
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    unsubscribe = None
    if request_options.signal is not None and task is not None:
        unsubscribe = request_options.signal.add_callback(
            lambda: loop.call_soon_threadsafe(task.cancel)
        )
    try:
        result = provider(request_model, prompt, request_options)
        if isinstance(result, Awaitable):
            result = await result
        images = tuple(result)
        if not all(isinstance(item, GeneratedImage) for item in images):
            raise ImageGenerationError("provider", "Image provider returned an invalid result")
        return images
    except asyncio.CancelledError as error:
        if request_options.signal is not None and request_options.signal.aborted:
            raise ImageGenerationError("aborted", "Image generation aborted") from error
        raise
    except ImageGenerationError:
        raise
    except Exception as error:  # noqa: BLE001 - provider details are normalized and redacted.
        raise ImageGenerationError(
            "provider",
            _redact_error(str(error), request_options.api_key),
        ) from error
    finally:
        if unsubscribe is not None:
            unsubscribe()


def _resolve_auth(
    model: ImageModel,
    options: ImageGenerationOptions,
) -> tuple[ImageModel, ImageGenerationOptions]:
    if options.api_key is not None or options.models is None:
        return model, options
    runtime_model = Model(
        id=model.id,
        name=model.id,
        api=model.api,
        provider=model.provider,
        base_url=model.base_url,
    )
    resolution = options.models.get_auth(runtime_model)
    if resolution is None:
        return model, options
    auth = resolution.auth
    request_model = replace(model, base_url=auth.base_url or model.base_url)
    return request_model, replace(
        options,
        api_key=auth.api_key,
        headers={**dict(auth.headers or {}), **dict(options.headers or {})} or None,
    )


async def _generate_openrouter_images(
    model: ImageModel,
    prompt: str,
    options: ImageGenerationOptions,
) -> tuple[GeneratedImage, ...]:
    if not options.api_key:
        raise ImageGenerationError("auth", f"No API key for provider: {model.provider}")
    base_url = (model.base_url or "https://openrouter.ai/api/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        **dict(options.headers or {}),
        "Authorization": f"Bearer {options.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model.id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "modalities": ["image", "text"] if "text" in model.output else ["image"],
    }
    client = options.client or httpx.AsyncClient(timeout=options.timeout_seconds)
    owns_client = options.client is None
    try:
        content = await _bounded_post_json(
            client,
            url,
            headers=headers,
            payload=payload,
            max_bytes=options.max_response_bytes,
            timeout=options.timeout_seconds,
        )
    finally:
        if owns_client:
            await client.aclose()
    return _parse_openrouter_images(content, options.max_response_bytes)


async def _bounded_post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, object],
    max_bytes: int,
    timeout: float,
) -> dict[str, object]:
    collected = bytearray()
    async with client.stream(
        "POST",
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
    ) as response:
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            raise ImageGenerationError("response_too_large", "Image provider response is too large")
        async for chunk in response.aiter_bytes():
            collected.extend(chunk)
            if len(collected) > max_bytes:
                raise ImageGenerationError("response_too_large", "Image provider response is too large")
        if response.status_code >= 400:
            detail = collected.decode("utf-8", errors="replace")[:2_000]
            raise ImageGenerationError(
                "http",
                f"Image provider HTTP {response.status_code}: {detail}",
            )
    try:
        value = json.loads(collected)
    except json.JSONDecodeError as error:
        raise ImageGenerationError("response", "Image provider returned invalid JSON") from error
    if not isinstance(value, dict):
        raise ImageGenerationError("response", "Image provider returned an invalid response")
    return value


def _parse_openrouter_images(
    response: dict[str, object],
    max_bytes: int,
) -> tuple[GeneratedImage, ...]:
    choices = response.get("choices")
    message: dict[str, object] = {}
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        raw_message = choices[0].get("message")
        if isinstance(raw_message, dict):
            message = raw_message
    revised_prompt = message.get("content") if isinstance(message.get("content"), str) else None
    raw_images = message.get("images")
    if not isinstance(raw_images, list):
        raw_images = response.get("data") if isinstance(response.get("data"), list) else []
    images: list[GeneratedImage] = []
    for item in raw_images:
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url", item.get("url"))
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        b64_json = item.get("b64_json")
        if isinstance(b64_json, str):
            data = _decode_image_base64(b64_json, max_bytes)
            images.append(GeneratedImage("image/png", data=data, revised_prompt=revised_prompt))
        elif isinstance(image_url, str) and image_url.startswith("data:"):
            mime_type, encoded = _split_data_url(image_url)
            data = _decode_image_base64(encoded, max_bytes)
            images.append(GeneratedImage(mime_type, data=data, revised_prompt=revised_prompt))
        elif isinstance(image_url, str) and image_url.startswith(("https://", "http://")):
            mime_type = mimetypes.guess_type(image_url.partition("?")[0])[0] or "image/png"
            images.append(GeneratedImage(mime_type, url=image_url, revised_prompt=revised_prompt))
    return tuple(images)


def _split_data_url(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"data:(image/[A-Za-z0-9.+-]+);base64,(.+)", value, flags=re.DOTALL)
    if match is None:
        raise ImageGenerationError("response", "Image provider returned an invalid data URL")
    return match.group(1), match.group(2)


def _decode_image_base64(value: str, max_bytes: int) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ImageGenerationError("response", "Image provider returned invalid base64") from error
    if len(data) > max_bytes:
        raise ImageGenerationError("response_too_large", "Generated image is too large")
    return data


def _redact_error(message: str, api_key: str | None) -> str:
    redacted = message.replace(api_key, "[REDACTED]") if api_key else message
    redacted = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    return redacted


register_image_provider("openrouter-images", _generate_openrouter_images)


__all__ = [
    "ImageGenerationError",
    "generate_images",
    "register_image_provider",
    "unregister_image_provider",
]
