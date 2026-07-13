from __future__ import annotations

from appv22.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    TextContent,
    TextDeltaEvent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    Usage,
    empty_usage,
    now_ms,
)


def test_content_blocks_carry_pi_type_literals() -> None:
    assert TextContent(text="hi").type == "text"
    assert ToolCall(id="t1", name="read", arguments={"path": "a"}).type == "toolCall"


def test_empty_usage_shape_matches_pi() -> None:
    usage = empty_usage()
    assert usage.input == 0 and usage.total_tokens == 0
    assert usage.cost.total == 0.0


def test_user_and_assistant_messages() -> None:
    user = UserMessage(content="hello", timestamp=now_ms())
    assert user.role == "user"
    assistant = AssistantMessage(
        content=[TextContent(text="ok")],
        api="openai-completions",
        provider="openrouter",
        model="m",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
    assert assistant.role == "assistant"
    assert assistant.content[0].text == "ok"


def test_tool_result_message_defaults() -> None:
    result = ToolResultMessage(
        tool_call_id="t1",
        tool_name="read",
        content=[TextContent(text="data")],
        is_error=False,
        timestamp=now_ms(),
    )
    assert result.role == "toolResult"
    assert result.details is None


def test_event_type_literals() -> None:
    msg = AssistantMessage(
        content=[],
        api="x",
        provider="p",
        model="m",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
    assert TextDeltaEvent(content_index=0, delta="a", partial=msg).type == "text_delta"
    assert DoneEvent(reason="stop", message=msg).type == "done"


def test_context_holds_messages_and_tools() -> None:
    ctx = Context(
        system_prompt="sys",
        messages=[UserMessage(content="q", timestamp=now_ms())],
        tools=[Tool(name="read", description="read a file", parameters={"type": "object"})],
    )
    assert ctx.tools[0].name == "read"
    assert ctx.messages[0].role == "user"
    _ = Usage
