from appv23.ai.types import AssistantMessage, TextContent, UserMessage, empty_usage
from appv23.coding_agent.agent_session import default_convert_to_llm


def _assistant(text: str, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="test",
        provider="test",
        model="test-model",
        usage=empty_usage(),
        stop_reason=stop_reason,
    )


def test_default_convert_to_llm_excludes_aborted_turn_from_future_context():
    aborted_prompt = UserMessage(content=[TextContent(text="write 120 abort-live-check lines")])
    aborted_response = _assistant("1. abort-live-check", stop_reason="aborted")
    next_prompt = UserMessage(content=[TextContent(text="reply exactly: abort-recovery-final-ok")])

    assert default_convert_to_llm([aborted_prompt, aborted_response, next_prompt]) == [next_prompt]


def test_default_convert_to_llm_preserves_completed_turns():
    previous_prompt = UserMessage(content=[TextContent(text="say hi")])
    previous_response = _assistant("hi")
    next_prompt = UserMessage(content=[TextContent(text="reply exactly: ok")])

    assert default_convert_to_llm([previous_prompt, previous_response, next_prompt]) == [
        previous_prompt,
        previous_response,
        next_prompt,
    ]
