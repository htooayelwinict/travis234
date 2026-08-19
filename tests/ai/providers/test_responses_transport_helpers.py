from __future__ import annotations

from travis.ai.types import Context, UserMessage


def test_codex_instruction_selection_preserves_native_developer_and_default_order() -> None:
    from travis.ai.providers.transport_families.responses import _codex_instructions

    context = Context(messages=[UserMessage(content="hello")], system_prompt="native")
    messages = [{"role": "developer", "content": "developer"}]

    assert _codex_instructions(context, messages) == "native"
    assert _codex_instructions(None, messages) == "developer"
    assert _codex_instructions(None, [{"role": "user", "content": "hello"}]) == (
        "You are a helpful assistant."
    )


def test_codex_reasoning_fields_preserve_disabled_and_enabled_wire_values() -> None:
    from travis.ai.providers.transport_families.responses import _codex_reasoning_fields

    assert _codex_reasoning_fields({"enabled": False, "effort": "high"}, "detailed") == {
        "reasoning": {"effort": "none", "summary": "detailed"}
    }
    assert _codex_reasoning_fields({"enabled": True, "effort": "high"}, None) == {
        "reasoning": {"effort": "high", "summary": "auto"}
    }
    assert _codex_reasoning_fields(None, None) == {}


def test_openai_cache_fields_preserve_retention_and_key_clamp() -> None:
    from travis.ai.providers.transport_families.responses import _openai_cache_fields

    assert _openai_cache_fields("x" * 80, "long", supports_long=True) == {
        "prompt_cache_key": "x" * 64,
        "prompt_cache_retention": "24h",
    }
    assert _openai_cache_fields("session", "none", supports_long=True) == {}
    assert _openai_cache_fields(None, "long", supports_long=False) == {}


def test_openai_sampling_fields_preserve_minimum_and_omissions() -> None:
    from travis.ai.providers.transport_families.responses import _openai_sampling_fields

    assert _openai_sampling_fields(max_tokens=1, temperature=0.2, service_tier="priority") == {
        "max_output_tokens": 16,
        "temperature": 0.2,
        "service_tier": "priority",
    }
    assert _openai_sampling_fields(max_tokens=None, temperature=None, service_tier=None) == {}


def test_openai_reasoning_fields_preserve_mapping_include_and_off_contract() -> None:
    from travis.ai.providers.transport_families.responses import _openai_reasoning_fields

    assert _openai_reasoning_fields(
        model_reasoning=True,
        reasoning_config={"enabled": True, "effort": "high"},
        reasoning_summary="detailed",
        thinking_level_map={"high": "xhigh"},
    ) == {
        "reasoning": {"effort": "xhigh", "summary": "detailed"},
        "include": ["reasoning.encrypted_content"],
    }
    assert _openai_reasoning_fields(
        model_reasoning=True,
        reasoning_config={"enabled": False, "effort": "off"},
        reasoning_summary=None,
        thinking_level_map={"off": None},
    ) == {}
    assert _openai_reasoning_fields(
        model_reasoning=True,
        reasoning_config=None,
        reasoning_summary=None,
        thinking_level_map={"off": "none"},
    ) == {"reasoning": {"effort": "none"}}


def test_azure_deployment_mapping_prefers_explicit_then_exact_mapping() -> None:
    from travis.ai.providers.transport_families.azure_responses import (
        _resolve_azure_deployment_name,
    )

    assert _resolve_azure_deployment_name("gpt-5.4", "explicit", "gpt-5.4=mapped") == "explicit"
    assert _resolve_azure_deployment_name(
        "gpt-5.4", None, "gpt-5.3=old, gpt-5.4=corp-gpt-54"
    ) == "corp-gpt-54"
    assert _resolve_azure_deployment_name("gpt-5.4", None, "gpt-5.3=old") == "gpt-5.4"
