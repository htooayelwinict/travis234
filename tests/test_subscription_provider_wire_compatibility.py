from __future__ import annotations

import pytest

from travis.ai.builtin_models import load_builtin_models
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.catalog import get_provider_profile
from travis.ai.providers.transports import (
    AnthropicMessagesTransport,
    CodexResponsesTransport,
    OpenAIResponsesTransport,
)
from travis.ai.types import Context, UserMessage


def test_codex_request_uses_native_context_system_prompt_as_instructions() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "openai-codex" and model.id == "gpt-5.4"
    )
    context = Context(
        messages=[UserMessage(content="hello")],
        system_prompt="SYSTEM_SENTINEL",
    )

    body = CodexResponsesTransport().build_kwargs(
        model=model.id,
        messages=[
            {"role": "developer", "content": "SYSTEM_SENTINEL"},
            {"role": "user", "content": "hello"},
        ],
        tools=[],
        profile=get_provider_profile(model.provider),
        stream=True,
        temperature=None,
        max_tokens=None,
        context=context,
        target_model=model,
        model_compat=model.compat,
    )

    assert body["instructions"] == "SYSTEM_SENTINEL"
    assert "SYSTEM_SENTINEL" not in str(body["input"])


def test_codex_request_accepts_developer_instruction_without_native_context() -> None:
    body = CodexResponsesTransport().build_kwargs(
        model="gpt-test",
        messages=[
            {"role": "developer", "content": "DEVELOPER_SENTINEL"},
            {"role": "user", "content": "hello"},
        ],
        tools=[],
        profile=ProviderProfile(name="openai-codex"),
        stream=True,
        temperature=None,
        max_tokens=None,
    )

    assert body["instructions"] == "DEVELOPER_SENTINEL"


def test_codex_request_uses_default_only_without_any_instruction() -> None:
    body = CodexResponsesTransport().build_kwargs(
        model="gpt-test",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name="openai-codex"),
        stream=True,
        temperature=None,
        max_tokens=None,
    )

    assert body["instructions"] == "You are a helpful assistant."


def test_copilot_gpt_responses_sampling_behavior_is_unchanged() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "github-copilot" and model.api == "openai-responses"
    )

    body = OpenAIResponsesTransport().build_kwargs(
        model=model.id,
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=get_provider_profile(model.provider),
        stream=True,
        temperature=0.2,
        max_tokens=2048,
        request_overrides={"top_p": 0.8},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.8


def test_copilot_fable_completions_route_is_outside_anthropic_guard() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "github-copilot" and model.id == "claude-fable-5"
    )

    assert model.api == "openai-completions"
    assert model.compat.get("supportsTemperature") is None
    assert model.compat.get("supportsTopP") is None


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("anthropic", "claude-fable-5"),
        ("anthropic", "claude-opus-4-7"),
        ("anthropic", "claude-opus-4-8"),
        ("anthropic", "claude-sonnet-5"),
        ("github-copilot", "claude-opus-4.7"),
        ("github-copilot", "claude-opus-4.8"),
        ("github-copilot", "claude-sonnet-5"),
    ],
)
def test_subscription_claude_sampling_is_absent_from_final_wire_body(
    provider: str,
    model_id: str,
) -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == provider and model.id == model_id
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=get_provider_profile(provider),
        stream=True,
        temperature=0.2,
        max_tokens=4096,
        reasoning_config={"enabled": False, "effort": "off"},
        request_overrides={"temperature": 0.2, "top_p": 0.8},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert "temperature" not in body
    assert "top_p" not in body


def test_claude_fable_off_omits_unsupported_disabled_thinking() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "anthropic" and model.id == "claude-fable-5"
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=get_provider_profile(model.provider),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": False, "effort": "off"},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert "thinking" not in body


def test_manual_claude_thinking_normalizes_sampling_and_forced_tool_choice() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "anthropic" and model.id == "claude-haiku-4-5"
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=get_provider_profile(model.provider),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": True, "effort": "high"},
        request_overrides={"top_p": 0.8},
        tool_choice="any",
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert body["thinking"]["type"] == "enabled"
    assert "top_p" not in body
    assert body["tool_choice"] == {"type": "auto"}


def test_manual_claude_thinking_preserves_valid_sampling_and_none_tool_choice() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "anthropic" and model.id == "claude-haiku-4-5"
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=get_provider_profile(model.provider),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": True, "effort": "medium"},
        request_overrides={"top_p": 0.95},
        tool_choice="none",
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert body["top_p"] == 0.95
    assert body["tool_choice"] == {"type": "none"}


def test_manual_claude_thinking_rejects_too_small_output_budget() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "anthropic" and model.id == "claude-haiku-4-5"
    )

    with pytest.raises(ValueError, match="manual thinking requires max_tokens >= 2048"):
        AnthropicMessagesTransport().build_kwargs(
            model=model.id,
            messages=[],
            tools=[],
            profile=get_provider_profile(model.provider),
            stream=True,
            temperature=None,
            max_tokens=1500,
            reasoning_config={"enabled": True, "effort": "high"},
            context=Context(messages=[UserMessage(content="hello")]),
            target_model=model,
            model_compat=model.compat,
        )


def test_claude_code_wire_guard_preserves_identity_and_travis_system_prompt() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "anthropic" and model.id == "claude-sonnet-5"
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=get_provider_profile(model.provider),
        stream=True,
        temperature=0.2,
        max_tokens=4096,
        request_overrides={"top_p": 0.8},
        context=Context(
            messages=[UserMessage(content="hello")],
            system_prompt="SYSTEM_SENTINEL",
        ),
        target_model=model,
        model_compat=model.compat,
        api_key="sk-ant-oat-test-placeholder",
    )

    assert [block["text"] for block in body["system"]] == [
        "You are Claude Code, Anthropic's official CLI for Claude.",
        "SYSTEM_SENTINEL",
    ]
    assert "temperature" not in body
    assert "top_p" not in body
