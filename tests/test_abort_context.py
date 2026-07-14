from travis.ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)
from travis.coding_agent.agent_session import default_convert_to_llm


def _assistant(text: str, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="test",
        provider="test",
        model="test-model",
        usage=empty_usage(),
        stop_reason=stop_reason,
    )


def test_default_convert_to_llm_preserves_aborted_turn_for_provider_normalization():
    aborted_prompt = UserMessage(content=[TextContent(text="write 120 abort-live-check lines")])
    aborted_response = _assistant("1. abort-live-check", stop_reason="aborted")
    next_prompt = UserMessage(content=[TextContent(text="reply exactly: abort-recovery-final-ok")])

    assert default_convert_to_llm([aborted_prompt, aborted_response, next_prompt]) == [
        aborted_prompt,
        aborted_response,
        next_prompt,
    ]


def test_default_convert_to_llm_preserves_successful_tool_work_before_a_failed_retry():
    completed_prompt = UserMessage(content=[TextContent(text="completed task")])
    completed_response = _assistant("completed")
    failed_prompt = UserMessage(content=[TextContent(text="parser task")])
    poisoned_tool_call = AssistantMessage(
        content=[
            TextContent(text="the tests are contradictory"),
            ToolCall(id="call_1", name="edit", arguments={"path": "parser.py"}),
        ],
        api="test",
        provider="test",
        model="test-model",
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    tool_result = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="edit",
        content=[TextContent(text="edited parser.py")],
        is_error=False,
    )
    provider_error = _assistant("", stop_reason="error")
    aborted_retry = _assistant("the tests are contradictory", stop_reason="aborted")
    next_prompt = UserMessage(content=[TextContent(text="new active task")])

    assert default_convert_to_llm(
        [
            completed_prompt,
            completed_response,
            failed_prompt,
            poisoned_tool_call,
            tool_result,
            provider_error,
            aborted_retry,
            next_prompt,
        ]
    ) == [
        completed_prompt,
        completed_response,
        failed_prompt,
        poisoned_tool_call,
        tool_result,
        provider_error,
        aborted_retry,
        next_prompt,
    ]


def test_default_convert_to_llm_preserves_provider_error_turn_for_transport_normalization():
    failed_prompt = UserMessage(content=[TextContent(text="failed task")])
    provider_error = _assistant("partial poisoned output", stop_reason="error")
    next_prompt = UserMessage(content=[TextContent(text="new active task")])

    assert default_convert_to_llm([failed_prompt, provider_error, next_prompt]) == [
        failed_prompt,
        provider_error,
        next_prompt,
    ]


def test_default_convert_to_llm_preserves_completed_turns():
    previous_prompt = UserMessage(content=[TextContent(text="say hi")])
    previous_response = _assistant("hi")
    next_prompt = UserMessage(content=[TextContent(text="reply exactly: ok")])

    assert default_convert_to_llm([previous_prompt, previous_response, next_prompt]) == [
        previous_prompt,
        previous_response,
        next_prompt,
    ]
