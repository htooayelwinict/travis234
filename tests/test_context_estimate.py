from __future__ import annotations

from copy import deepcopy

import pytest

from travis.ai import calculate_prompt_tokens, calculate_total_tokens
from travis.ai.context_estimate import (
    ESTIMATED_IMAGE_CHARS,
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_text_tokens,
)
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
)


def test_prompt_tokens_exclude_generated_output() -> None:
    usage = Usage(
        input=2_000,
        output=7_000,
        cache_read=11_000,
        cache_write=3_000,
        total_tokens=23_000,
    )

    assert calculate_prompt_tokens(usage) == 16_000
    assert calculate_total_tokens(usage) == 23_000


def test_prompt_tokens_fall_back_to_input_cache_components() -> None:
    usage = Usage(input=400, output=900, cache_read=200, cache_write=100, total_tokens=0)

    assert calculate_prompt_tokens(usage) == 700
    assert calculate_total_tokens(usage) == 1_600


def test_full_request_estimate_reports_components() -> None:
    context = Context(
        system_prompt="s" * 400,
        messages=[
            AssistantMessage(
                content=[TextContent(text="visible")],
                api="faux",
                provider="faux",
                model="faux-model",
                usage=Usage(),
                stop_reason="stop",
            )
        ],
        tools=[Tool(name="read", description="d" * 400, parameters={"type": "object"})],
    )

    estimate = estimate_context_tokens(context)

    assert estimate.system_tokens == estimate_text_tokens("s" * 400)
    assert estimate.tool_tokens > 100
    assert estimate.message_tokens == estimate_text_tokens("visible")
    assert estimate.tokens == estimate.system_tokens + estimate.tool_tokens + estimate.message_tokens
    assert estimate.confidence == "estimated_full_request"


def test_provider_prompt_usage_excludes_output_and_reports_confidence() -> None:
    context = Context(
        messages=[
            AssistantMessage(
                content=[TextContent(text="answer")],
                api="faux",
                provider="faux",
                model="faux-model",
                usage=Usage(input=2_000, output=7_000, total_tokens=9_000),
                stop_reason="stop",
            )
        ]
    )

    estimate = estimate_context_tokens(context)

    assert estimate.tokens == 2_000
    assert estimate.usage_tokens == 2_000
    assert estimate.trailing_tokens == 0
    assert estimate.confidence == "provider_real"


@pytest.mark.parametrize(
    "field",
    [
        "reasoning_content",
        "reasoning_details",
        "codex_reasoning_items",
        "codex_message_items",
    ],
)
def test_assistant_replay_fields_increase_estimate(field: str) -> None:
    base = AssistantMessage(
        content=[TextContent(text="visible")],
        api="openai-responses",
        provider="openai",
        model="gpt-5.4",
        usage=Usage(),
        stop_reason="stop",
    )
    replay = deepcopy(base)
    setattr(replay, field, {"payload": "r" * 400})

    assert estimate_message_tokens(replay) > estimate_message_tokens(base)


def test_content_signatures_tool_ids_and_arguments_increase_estimate() -> None:
    base = AssistantMessage(
        content=[TextContent(text="visible")],
        api="openai-responses",
        provider="openai",
        model="gpt-5.4",
        usage=Usage(),
        stop_reason="toolUse",
    )
    replay = deepcopy(base)
    replay.content.extend(
        [
            TextContent(text="signed", text_signature="t" * 120),
            ThinkingContent(thinking="reasoning", thinking_signature="s" * 120),
            ToolCall(
                id="call-" + "i" * 80,
                name="read",
                arguments={"path": "p" * 160},
                thought_signature="d" * 120,
            ),
        ]
    )

    assert estimate_message_tokens(replay) > estimate_message_tokens(base) + 100


def test_tool_result_counts_identity_details_and_image_payload() -> None:
    result = ToolResultMessage(
        tool_call_id="call-" + "x" * 80,
        tool_name="read",
        content=[TextContent(text="result"), ImageContent(data="ignored", mime_type="image/png")],
        is_error=False,
        details={"replay": "d" * 160},
    )

    estimate = estimate_message_tokens(result)

    assert estimate >= ESTIMATED_IMAGE_CHARS // 4
    assert estimate > estimate_text_tokens("result") + ESTIMATED_IMAGE_CHARS // 4
    assert estimate_messages_tokens([result]) == estimate
