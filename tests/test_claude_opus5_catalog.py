from __future__ import annotations

from travis.ai.env_config import ModelConfig
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.transports import BedrockConverseStreamTransport
from travis.ai.types import Context, Model, UserMessage
from travis.cli import _env_model_from_config


def test_openai_compatible_proxy_override_preserves_catalog_capabilities() -> None:
    selected = _env_model_from_config(
        ModelConfig(
            enabled=True,
            api_key="local-placeholder",
            model="anthropic/claude-opus-5",
            base_url="http://localhost:20128/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider="openrouter",
        )
    )

    assert selected.base_url == "http://localhost:20128/v1"
    assert selected.api == "openai-completions"
    assert selected.reasoning is True
    assert selected.context_window == 1_000_000
    assert selected.max_tokens == 128_000
    assert selected.compat["thinkingFormat"] == "openrouter"


def test_bedrock_opus_5_inference_profile_uses_adaptive_thinking() -> None:
    model = Model(
        id="global.anthropic.claude-opus-5",
        name="Claude Opus 5 (Global)",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 5, "output": 25, "cacheRead": 0.5, "cacheWrite": 6.25},
        context_window=1_000_000,
        max_tokens=128_000,
    )

    body = BedrockConverseStreamTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="bedrock"),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": True, "effort": "xhigh"},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
    )

    assert body["additionalModelRequestFields"] == {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "xhigh"},
    }
