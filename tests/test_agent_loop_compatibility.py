from __future__ import annotations

import threading

from travis.agent import AgentContext, AgentTool, AgentToolResult
from travis.agent.types import AgentLoopConfig
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import (
    AssistantMessage,
    DoneEvent,
    Message,
    StartEvent,
    TextContent,
    ToolCall,
    ToolcallEndEvent,
    ToolcallStartEvent,
    UserMessage,
    empty_usage,
    now_ms,
)
from tests._provider_runtime import register_api_provider, reset_api_providers, run_agent_loop


def setup_function() -> None:
    reset_api_providers()


def _convert(messages) -> list[Message]:
    return [
        message
        for message in messages
        if getattr(message, "role", None) in {"user", "assistant", "toolResult"}
    ]


def _config(model) -> AgentLoopConfig:
    return AgentLoopConfig(model=model, convert_to_llm=_convert)


def _context(*, tools: list[AgentTool] | None = None) -> AgentContext:
    return AgentContext(system_prompt="sys", messages=[], tools=tools)


def _multi_tool_response(model, calls: list[tuple[str, str]]) -> list:
    partial = AssistantMessage(
        content=[ToolCall(id=call_id, name=name, arguments={}) for call_id, name in calls],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    events: list = [StartEvent(partial=partial)]
    for index, tool_call in enumerate(partial.content):
        events.append(ToolcallStartEvent(content_index=index, partial=partial))
        events.append(ToolcallEndEvent(content_index=index, tool_call=tool_call, partial=partial))
    events.append(DoneEvent(reason="toolUse", message=partial))
    return events


def test_successful_text_turn_matches_travis234_243_event_contract() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda _model, _context: text_response_events(model, "ok")))
    event_types: list[str] = []

    messages = run_agent_loop(
        [UserMessage(content="hello", timestamp=now_ms())],
        _context(),
        _config(model),
        lambda event: event_types.append(event.type),
    )

    assert event_types == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert [getattr(message, "role", None) for message in messages] == ["user", "assistant"]
    assert messages[-1].content == [TextContent(text="ok")]


def test_parallel_success_keeps_completion_order_events_and_source_order_results() -> None:
    model = faux_model()
    provider_calls = 0
    fast_finished = threading.Event()

    def provider(_model, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return _multi_tool_response(model, [("call_slow", "slow"), ("call_fast", "fast")])
        return text_response_events(model, "done")

    def slow_execute(_tool_call_id, _args, signal=None, on_update=None):
        assert fast_finished.wait(timeout=2)
        return AgentToolResult(content=[TextContent(text="slow")], details={})

    def fast_execute(_tool_call_id, _args, signal=None, on_update=None):
        fast_finished.set()
        return AgentToolResult(content=[TextContent(text="fast")], details={})

    tools = [
        AgentTool(
            name="slow",
            description="slow",
            parameters={"type": "object", "properties": {}},
            label="Slow",
            execute=slow_execute,
        ),
        AgentTool(
            name="fast",
            description="fast",
            parameters={"type": "object", "properties": {}},
            label="Fast",
            execute=fast_execute,
        ),
    ]
    register_api_provider(create_faux_provider(provider))
    config = _config(model)
    config.tool_execution = "parallel"
    config.max_parallel_tools = 2
    tool_end_ids: list[str] = []

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _context(tools=tools),
        config,
        lambda event: tool_end_ids.append(event.tool_call_id)
        if event.type == "tool_execution_end"
        else None,
    )

    tool_results = [message for message in messages if getattr(message, "role", None) == "toolResult"]
    assert tool_end_ids == ["call_fast", "call_slow"]
    assert [message.tool_call_id for message in tool_results] == ["call_slow", "call_fast"]
    assert provider_calls == 2
