from __future__ import annotations

from types import SimpleNamespace

import pytest

from travis.ai.providers.base import OMIT_TEMPERATURE
from travis.ai.types import Context, Tool, UserMessage


def test_cache_control_fields_preserve_retention_rules() -> None:
    from travis.ai.providers.transport_families.anthropic import _anthropic_cache_control

    assert _anthropic_cache_control("none", supports_long=True) is None
    assert _anthropic_cache_control("short", supports_long=True) == {"type": "ephemeral"}
    assert _anthropic_cache_control("long", supports_long=True) == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert _anthropic_cache_control("long", supports_long=False) == {"type": "ephemeral"}


@pytest.mark.parametrize(
    ("temperature", "thinking_enabled", "supports_temperature", "fixed", "expected"),
    [
        (0.2, False, True, None, {"temperature": 0.2}),
        (0.2, False, True, 0.7, {"temperature": 0.7}),
        (0.2, True, True, None, {}),
        (0.2, False, False, None, {}),
        (0.2, False, True, OMIT_TEMPERATURE, {}),
        (None, False, True, None, {}),
    ],
)
def test_sampling_fields_preserve_omission_and_fixed_temperature(
    temperature: float | None,
    thinking_enabled: bool,
    supports_temperature: bool,
    fixed: object,
    expected: dict[str, float],
) -> None:
    from travis.ai.providers.transport_families.anthropic import _anthropic_sampling_fields

    assert _anthropic_sampling_fields(
        temperature=temperature,
        thinking_enabled=thinking_enabled,
        supports_temperature=supports_temperature,
        fixed_temperature=fixed,
    ) == expected


def test_system_fields_preserve_native_cache_and_oauth_identity_order() -> None:
    from travis.ai.providers.transport_families.anthropic import _anthropic_system_blocks

    blocks = _anthropic_system_blocks(
        messages=[{"role": "developer", "content": "ignored"}],
        context=Context(messages=[UserMessage(content="hello")], system_prompt="policy"),
        cache_control={"type": "ephemeral"},
        is_oauth=True,
    )

    assert [block["text"] for block in blocks] == [
        "You are Claude Code, Anthropic's official CLI for Claude.",
        "policy",
    ]
    assert all(block["cache_control"] == {"type": "ephemeral"} for block in blocks)


def test_tool_fields_preserve_native_cache_eager_and_deferred_flags() -> None:
    from travis.ai.providers.transport_families.anthropic import _anthropic_tool_fields

    immediate = Tool(name="read", description="Read", parameters={"type": "object"})
    deferred = Tool(name="write", description="Write", parameters={"type": "object"})

    fields = _anthropic_tool_fields(
        immediate_tools=[immediate],
        deferred_tools=[deferred],
        fallback_tools=None,
        cache_control={"type": "ephemeral"},
        supports_cache_control=True,
        supports_eager_input=True,
        normalize_tool_name=str.upper,
    )

    assert fields["tools"] == [
        {
            "name": "READ",
            "description": "Read",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "eager_input_streaming": True,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "name": "WRITE",
            "description": "Write",
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "eager_input_streaming": True,
            "defer_loading": True,
        },
    ]


def test_thinking_fields_preserve_adaptive_manual_and_disabled_contracts() -> None:
    from travis.ai.providers.transport_families.anthropic import _anthropic_thinking_fields

    adaptive_model = SimpleNamespace(reasoning=True, thinking_level_map={"off": "none"})
    assert _anthropic_thinking_fields(
        target_model=adaptive_model,
        reasoning_config={"enabled": True, "effort": "minimal"},
        max_tokens=4096,
        force_adaptive=True,
    ) == {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "low"},
    }
    assert _anthropic_thinking_fields(
        target_model=adaptive_model,
        reasoning_config={"enabled": False, "effort": "off"},
        max_tokens=4096,
        force_adaptive=False,
    ) == {"thinking": {"type": "disabled"}}

    manual_model = SimpleNamespace(reasoning=True, thinking_level_map={})
    assert _anthropic_thinking_fields(
        target_model=manual_model,
        reasoning_config={"enabled": True, "effort": "medium"},
        max_tokens=4096,
        force_adaptive=False,
    ) == {
        "thinking": {
            "type": "enabled",
            "budget_tokens": 3072,
            "display": "summarized",
        }
    }


def test_manual_thinking_fields_reject_too_small_output_budget() -> None:
    from travis.ai.providers.transport_families.anthropic import _anthropic_thinking_fields

    with pytest.raises(ValueError, match="manual thinking requires max_tokens >= 2048"):
        _anthropic_thinking_fields(
            target_model=SimpleNamespace(reasoning=True, thinking_level_map={}),
            reasoning_config={"enabled": True, "effort": "high"},
            max_tokens=1500,
            force_adaptive=False,
        )
