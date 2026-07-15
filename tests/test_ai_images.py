from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from travis.agent.types import AbortSignal
from travis.ai.auth import InMemoryCredentialStore, ProviderAuth, env_api_key_auth
from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.image_types import GeneratedImage, ImageGenerationOptions, ImageModel
from travis.ai.images import ImageGenerationError, generate_images, register_image_provider
from travis.ai.models import Models, Provider, ProviderStreams
from travis.ai.types import Model


def _streams() -> ProviderStreams:
    return ProviderStreams(
        stream=lambda model, context, options=None: create_assistant_message_event_stream(),
        stream_simple=lambda model, context, options=None: create_assistant_message_event_stream(),
    )


def test_image_registry_resolves_existing_auth_and_binary_results() -> None:
    captured: list[ImageGenerationOptions] = []

    async def provider(model, prompt, options):
        captured.append(options)
        return [GeneratedImage(mime_type="image/png", data=b"png-bytes")]

    register_image_provider("fixture-images", provider)
    runtime = Models(
        credentials=InMemoryCredentialStore(
            {"fixture": {"type": "api_key", "key": "stored-image-secret"}}
        )
    )
    runtime.set_provider(
        Provider(
            id="fixture",
            auth=ProviderAuth(api_key=env_api_key_auth("Fixture", ["FIXTURE_IMAGE_KEY"])),
            models=[Model(id="image", name="Image", api="fixture-images", provider="fixture", base_url="")],
            api=_streams(),
        )
    )
    model = ImageModel(id="image", provider="fixture", api="fixture-images")

    result = asyncio.run(
        generate_images(model, "draw a circuit", ImageGenerationOptions(models=runtime))
    )

    assert result == (GeneratedImage(mime_type="image/png", data=b"png-bytes"),)
    assert captured[0].api_key == "stored-image-secret"


def test_image_registry_rejects_unknown_provider_aborts_and_redacts_errors() -> None:
    model = ImageModel(id="image", provider="fixture", api="missing-images")
    with pytest.raises(ImageGenerationError, match="No image API provider"):
        asyncio.run(generate_images(model, "draw"))

    signal = AbortSignal()
    signal.abort()
    register_image_provider("aborted-images", lambda model, prompt, options: ())
    with pytest.raises(ImageGenerationError, match="aborted"):
        asyncio.run(
            generate_images(
                ImageModel(id="image", provider="fixture", api="aborted-images"),
                "draw",
                ImageGenerationOptions(signal=signal),
            )
        )

    async def fail(model, prompt, options):
        raise RuntimeError(f"provider rejected Bearer {options.api_key}")

    register_image_provider("failed-images", fail)
    with pytest.raises(ImageGenerationError) as raised:
        asyncio.run(
            generate_images(
                ImageModel(id="image", provider="fixture", api="failed-images"),
                "draw",
                ImageGenerationOptions(api_key="secret-image-key"),
            )
        )
    assert "secret-image-key" not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)


def test_openrouter_image_adapter_parses_data_and_url_outputs_without_live_network() -> None:
    png = b"\x89PNG\r\n\x1a\nfixture"
    encoded = base64.b64encode(png).decode("ascii")
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "revised prompt",
                            "images": [
                                {"image_url": {"url": f"data:image/png;base64,{encoded}"}},
                                {"image_url": {"url": "https://images.example/result.png"}},
                            ],
                        }
                    }
                ]
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await generate_images(
                ImageModel(
                    id="openai/gpt-image-1",
                    provider="openrouter",
                    api="openrouter-images",
                    base_url="https://openrouter.ai/api/v1",
                ),
                "draw a safe diagram",
                ImageGenerationOptions(api_key="test-key", client=client),
            )

    result = asyncio.run(scenario())

    assert result == (
        GeneratedImage(mime_type="image/png", data=png, revised_prompt="revised prompt"),
        GeneratedImage(
            mime_type="image/png",
            url="https://images.example/result.png",
            revised_prompt="revised prompt",
        ),
    )
    assert captured == [
        {
            "model": "openai/gpt-image-1",
            "messages": [{"role": "user", "content": "draw a safe diagram"}],
            "stream": False,
            "modalities": ["image"],
        }
    ]


def test_generated_image_requires_exactly_one_payload() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        GeneratedImage(mime_type="image/png")
    with pytest.raises(ValueError, match="exactly one"):
        GeneratedImage(mime_type="image/png", data=b"x", url="https://example.test/x")
