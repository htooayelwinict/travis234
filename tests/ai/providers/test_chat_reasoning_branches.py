"""Direct branch characterizations for chat reasoning payloads."""

from __future__ import annotations

import pytest

from travis.ai.providers.openai_compat import OpenAICompat
from travis.ai.providers.transport_families.chat_completions import _apply_reasoning_payload


@pytest.mark.parametrize(
    ("compat", "reasoning_config", "level_map", "expected"),
    [
        (
            OpenAICompat(thinking_format="zai", supports_reasoning_effort=True),
            {"enabled": True, "effort": "high"},
            {"high": "xhigh"},
            {
                "thinking": {"type": "enabled", "clear_thinking": False},
                "reasoning_effort": "xhigh",
            },
        ),
        (
            OpenAICompat(thinking_format="zai"),
            {"enabled": False, "effort": "high"},
            None,
            {"thinking": {"type": "disabled"}},
        ),
        (
            OpenAICompat(thinking_format="qwen"),
            {"enabled": True, "effort": "medium"},
            None,
            {"enable_thinking": True},
        ),
        (
            OpenAICompat(thinking_format="qwen-chat-template"),
            {"enabled": False, "effort": "off"},
            None,
            {
                "chat_template_kwargs": {
                    "enable_thinking": False,
                    "preserve_thinking": True,
                }
            },
        ),
        (
            OpenAICompat(
                thinking_format="chat-template",
                chat_template_kwargs={
                    "enabled": {"$var": "thinking.enabled"},
                    "level": {"$var": "thinking.level"},
                    "only_when_on": {"omitWhenOff": True},
                },
            ),
            {"enabled": True, "effort": "high"},
            {"high": "HIGH", "off": "OFF"},
            {
                "chat_template_kwargs": {
                    "enabled": True,
                    "level": "HIGH",
                    "only_when_on": "HIGH",
                }
            },
        ),
        (
            OpenAICompat(
                thinking_format="chat-template",
                chat_template_kwargs={
                    "enabled": {"$var": "thinking.enabled"},
                    "level": {"$var": "thinking.level"},
                    "only_when_on": {"omitWhenOff": True},
                },
            ),
            {"enabled": False, "effort": "off"},
            {"off": "OFF"},
            {"chat_template_kwargs": {"enabled": False, "level": "OFF"}},
        ),
        (
            OpenAICompat(thinking_format="deepseek", supports_reasoning_effort=True),
            {"enabled": True, "effort": "medium"},
            {"medium": "high"},
            {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        ),
        (
            OpenAICompat(thinking_format="deepseek"),
            {"enabled": False, "effort": "off"},
            {"off": None},
            {},
        ),
        (
            OpenAICompat(thinking_format="openrouter"),
            {"enabled": True, "effort": "high"},
            {"high": "xhigh"},
            {"reasoning": {"effort": "xhigh"}},
        ),
        (
            OpenAICompat(thinking_format="openrouter"),
            {"enabled": False, "effort": "off"},
            {"off": "none"},
            {"reasoning": {"effort": "none"}},
        ),
        (
            OpenAICompat(thinking_format="ant-ling"),
            {"enabled": True, "effort": "low"},
            None,
            {"reasoning": {"effort": "low"}},
        ),
        (
            OpenAICompat(thinking_format="together", supports_reasoning_effort=True),
            {"enabled": True, "effort": "medium"},
            None,
            {"reasoning": {"enabled": True}, "reasoning_effort": "medium"},
        ),
        (
            OpenAICompat(thinking_format="string-thinking"),
            {"enabled": False, "effort": "off"},
            {"off": "disabled"},
            {"thinking": "disabled"},
        ),
        (
            OpenAICompat(thinking_format="openai", supports_reasoning_effort=True),
            {"enabled": True, "effort": "high"},
            {"high": "xhigh"},
            {"reasoning_effort": "xhigh"},
        ),
        (
            OpenAICompat(thinking_format="openai", supports_reasoning_effort=True),
            {"enabled": False, "effort": "off"},
            {"off": "none"},
            {"reasoning_effort": "none"},
        ),
    ],
)
def test_reasoning_format_payload_matrix(
    compat: OpenAICompat,
    reasoning_config: dict[str, object],
    level_map: dict[str, str | None] | None,
    expected: dict[str, object],
) -> None:
    body: dict[str, object] = {}

    _apply_reasoning_payload(body, compat, reasoning_config, level_map)

    assert body == expected
