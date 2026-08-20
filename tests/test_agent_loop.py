from __future__ import annotations

import asyncio
from concurrent.futures import Future
import threading
import time

import pytest

from travis.agent import (
    AbortSignal,
    Agent,
    AgentContext,
    AgentLoopTurnUpdate,
    AgentTool,
    AgentToolResult,
    AfterToolCallResult,
    BeforeToolCallResult,
    RunLease,
    ShouldStopAfterTurnContext,
)
from travis.agent.agent_loop import run_agent_loop_async
from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from tests._provider_runtime import (
    agent_loop,
    agent_loop_continue,
    register_api_provider,
    reset_api_providers,
    run_agent_loop,
    stream_simple,
)
from travis.ai.types import (
    AssistantMessage,
    DoneEvent,
    ImageContent,
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
def _convert(messages):
    out: list[Message] = []
    for m in messages:
        if getattr(m, "role", None) in ("user", "assistant", "toolResult"):
            out.append(m)
    return out


def _ctx(tools=None) -> AgentContext:
    return AgentContext(system_prompt="sys", messages=[], tools=tools)


def setup_function() -> None:
    reset_api_providers()


def test_single_text_turn_event_sequence() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "hello")))
    events: list[str] = []
    msgs = run_agent_loop(
        [UserMessage(content="hi", timestamp=now_ms())],
        _ctx(),
        _config(model),
        lambda e: events.append(e.type),
    )
    assert events[0] == "agent_start"
    assert events[1] == "turn_start"
    assert "message_update" in events
    assert events[-1] == "agent_end"
    assert any(getattr(m, "role", None) == "assistant" for m in msgs)


def _config(model):
    from travis.agent.types import AgentLoopConfig

    return AgentLoopConfig(model=model, convert_to_llm=_convert)


def _multi_tool_call_response_events(model, calls: list[tuple[str, str, dict]]) -> list:
    partial = AssistantMessage(
        content=[ToolCall(id=call_id, name=name, arguments=args) for call_id, name, args in calls],
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
    final = AssistantMessage(
        content=[ToolCall(id=call_id, name=name, arguments=args) for call_id, name, args in calls],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    events.append(DoneEvent(reason="toolUse", message=final))
    return events


def test_tool_call_turn_executes_and_continues() -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "echo", {"text": "hi"})
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def echo_execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"echo:{args['text']}")], details={})

    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        label="Echo",
        execute=echo_execute,
    )
    events: list[str] = []
    msgs = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[echo]),
        _config(model),
        lambda e: events.append(e.type),
    )
    assert "tool_execution_start" in events
    assert "tool_execution_end" in events
    assert any(getattr(m, "role", None) == "toolResult" for m in msgs)
    assert calls["n"] == 2


def test_tool_call_history_keeps_raw_arguments_after_execution_before_next_model_call() -> None:
    model = faux_model()
    large_content = "SMOKING-GUN-WRITE-CONTENT\n" + ("generated report body " * 500)
    calls = {"n": 0}
    second_context = {}
    tool_saw_raw_content = {"value": False}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "write", {"path": "docs/report.md", "content": large_content})
        second_context["messages"] = list(c.messages)
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def write_execute(tool_call_id, args, signal=None, on_update=None):
        tool_saw_raw_content["value"] = args["content"] == large_content
        return AgentToolResult(content=[TextContent(text="wrote")], details={})

    write = AgentTool(
        name="write",
        description="write",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        label="Write",
        execute=write_execute,
    )
    config = _config(model)

    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[write]),
        config,
        lambda e: None,
    )

    assert tool_saw_raw_content["value"] is True
    assistant = next(m for m in second_context["messages"] if getattr(m, "role", None) == "assistant")
    tool_call = next(block for block in assistant.content if isinstance(block, ToolCall))
    assert tool_call.arguments["path"] == "docs/report.md"
    assert tool_call.arguments["content"] == large_content
    assert "[travis redacted tool argument" not in tool_call.arguments["content"]


def test_agent_loop_stops_after_signal_aborted_during_tool_execution() -> None:
    model = faux_model()
    calls = {"n": 0}
    signal = AbortSignal()

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "aborter", {})
        return text_response_events(m, "should not run after abort")

    register_api_provider(create_faux_provider(script))

    def aborter_execute(tool_call_id, args, signal=None, on_update=None):
        assert signal is not None
        signal.abort()
        raise RuntimeError("Operation aborted")

    aborter = AgentTool(
        name="aborter",
        description="aborter",
        parameters={"type": "object", "properties": {}},
        label="Aborter",
        execute=aborter_execute,
    )
    events: list[str] = []

    msgs = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[aborter]),
        _config(model),
        lambda e: events.append(e.type),
        signal,
    )

    assert calls["n"] == 1
    assert events[-1] == "agent_end"
    assert any(
        getattr(message, "role", None) == "toolResult"
        and any(getattr(block, "text", "") == "Operation aborted" for block in message.content)
        for message in msgs
    )


def test_parallel_abort_skips_calls_waiting_for_coordinator_slot() -> None:
    model = faux_model()
    signal = AbortSignal()
    provider_calls = 0
    executed: list[str] = []
    tool_end_reasons: dict[str, str | None] = {}

    def provider(_model, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return _multi_tool_call_response_events(
                model,
                [("call_abort", "aborter", {}), ("call_late", "late", {})],
            )
        return text_response_events(model, "should not continue")

    def aborter_execute(tool_call_id, _args, signal=None, on_update=None):
        executed.append(tool_call_id)
        assert signal is not None
        signal.abort()
        return AgentToolResult(content=[TextContent(text="aborted")], details={})

    def late_execute(tool_call_id, _args, signal=None, on_update=None):
        executed.append(tool_call_id)
        return AgentToolResult(content=[TextContent(text="late")], details={})

    tools = [
        AgentTool(
            name="aborter",
            description="abort",
            parameters={"type": "object", "properties": {}},
            label="Aborter",
            execute=aborter_execute,
        ),
        AgentTool(
            name="late",
            description="late",
            parameters={"type": "object", "properties": {}},
            label="Late",
            execute=late_execute,
        ),
    ]
    register_api_provider(create_faux_provider(provider))
    config = _config(model)
    config.tool_execution = "parallel"
    config.max_parallel_tools = 1

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=tools),
        config,
        lambda event: tool_end_reasons.__setitem__(event.tool_call_id, event.reason_code)
        if event.type == "tool_execution_end"
        else None,
        signal,
    )

    results = [message for message in messages if getattr(message, "role", None) == "toolResult"]
    assert executed == ["call_abort"]
    assert [message.tool_call_id for message in results] == ["call_abort", "call_late"]
    assert results[1].is_error is True
    assert [block.text for block in results[1].content] == ["Operation aborted"]
    assert tool_end_reasons["call_late"] == "aborted"
    assert provider_calls == 1


def test_duplicate_tool_calls_in_same_assistant_turn_execute_like_travis234() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[tuple[str, dict]] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [
                    ("call_1", "echo", {"text": "same"}),
                    ("call_2", "echo", {"text": "same"}),
                    ("call_3", "echo", {"text": "different"}),
                ],
            )
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def echo_execute(tool_call_id, args, signal=None, on_update=None):
        executions.append((tool_call_id, dict(args)))
        return AgentToolResult(content=[TextContent(text=f"echo:{args['text']}")], details={})

    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        label="Echo",
        execute=echo_execute,
    )

    msgs = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[echo]),
        _config(model),
        lambda e: None,
    )

    tool_results = [msg for msg in msgs if getattr(msg, "role", None) == "toolResult"]
    assistant = next(msg for msg in msgs if getattr(msg, "role", None) == "assistant")
    assert len(executions) == 3
    assert {call_id for call_id, _args in executions} == {"call_1", "call_2", "call_3"}
    assert {call_id: args for call_id, args in executions} == {
        "call_1": {"text": "same"},
        "call_2": {"text": "same"},
        "call_3": {"text": "different"},
    }
    assert [result.tool_call_id for result in tool_results] == ["call_1", "call_2", "call_3"]
    assert [call.id for call in assistant.content if getattr(call, "type", None) == "toolCall"] == [
        "call_1",
        "call_2",
        "call_3",
    ]


def test_duplicate_provider_start_fails_without_context_corruption_and_closes_response() -> None:
    model = faux_model()
    stream_calls = 0
    executions: list[str] = []
    response_closed = threading.Event()
    assistant_lifecycle: list[str] = []

    class DuplicateStartResponse:
        def __init__(self) -> None:
            events = _multi_tool_call_response_events(model, [("call_probe", "probe", {})])
            self._result = events[-1].message
            self._events = [events[0], StartEvent(partial=events[0].partial), *events[1:]]

        def __iter__(self):
            return iter(self._events)

        def result_sync(self):
            return self._result

        def close(self) -> None:
            response_closed.set()

    def stream_fn(_model, context, options):
        nonlocal stream_calls
        stream_calls += 1
        if stream_calls == 1:
            return DuplicateStartResponse()
        return create_faux_provider(lambda m, c: text_response_events(m, "unexpected")).stream_simple(
            model, context, options
        )

    tool = AgentTool(
        name="probe",
        description="probe",
        parameters={"type": "object", "properties": {}},
        label="Probe",
        execute=lambda tool_call_id, *_args: executions.append(tool_call_id)
        or AgentToolResult(content=[TextContent(text="ran")]),
    )

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[tool]),
        _config(model),
        lambda event: assistant_lifecycle.append(event.type)
        if event.type in {"message_start", "message_end"}
        and getattr(event.message, "role", None) == "assistant"
        else None,
        stream_fn=stream_fn,
    )

    assistants = [message for message in messages if getattr(message, "role", None) == "assistant"]
    assert stream_calls == 1
    assert executions == []
    assert len(assistants) == 1
    assert assistants[0].stop_reason == "error"
    assert assistants[0].error_message == "Provider stream protocol error: duplicate start event"
    assert assistant_lifecycle == ["message_start", "message_end"]
    assert response_closed.is_set()


def test_provider_update_before_start_fails_with_balanced_message_lifecycle() -> None:
    model = faux_model()
    events = text_response_events(model, "invalid ordering")[1:]
    lifecycle: list[str] = []

    def stream_fn(_model, _context, _options):
        stream = create_assistant_message_event_stream()
        for event in events:
            stream.push(event)
        return stream

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(),
        _config(model),
        lambda event: lifecycle.append(event.type)
        if event.type in {"message_start", "message_end"}
        and getattr(event.message, "role", None) == "assistant"
        else None,
        stream_fn=stream_fn,
    )

    assistant = next(message for message in messages if getattr(message, "role", None) == "assistant")
    assert assistant.stop_reason == "error"
    assert assistant.error_message == (
        "Provider stream protocol error: text_start event emitted before start event"
    )
    assert lifecycle == ["message_start", "message_end"]


def test_invalid_provider_result_becomes_one_error_assistant() -> None:
    model = faux_model()
    response_closed = threading.Event()

    class InvalidResultResponse:
        def __iter__(self):
            final = AssistantMessage(
                content=[TextContent(text="ignored")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
            return iter([DoneEvent(reason="stop", message=final)])

        def result_sync(self):
            return "not-an-assistant"

        def close(self) -> None:
            response_closed.set()

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(),
        _config(model),
        lambda _event: None,
        stream_fn=lambda *_args: InvalidResultResponse(),
    )

    assistants = [message for message in messages if getattr(message, "role", None) == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].stop_reason == "error"
    assert assistants[0].error_message == (
        "Provider stream protocol error: invalid result type str; expected AssistantMessage"
    )
    assert response_closed.is_set()


def test_truncated_assistant_tool_calls_fail_without_execution_like_travis234() -> None:
    model = faux_model()
    executions: list[str] = []
    stream_calls = {"n": 0}

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        if stream_calls["n"] > 1:
            return create_faux_provider(lambda m, c: text_response_events(m, "done")).stream_simple(
                model, context, options
            )
        stream = create_assistant_message_event_stream()
        tool_call = ToolCall(id="call_truncated", name="echo", arguments={"text": "unfinished"})
        partial = AssistantMessage(
            content=[tool_call],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="length",
            timestamp=now_ms(),
        )
        stream.push(StartEvent(partial=partial))
        stream.push(ToolcallStartEvent(content_index=0, partial=partial))
        stream.push(ToolcallEndEvent(content_index=0, tool_call=tool_call, partial=partial))
        stream.push(DoneEvent(reason="length", message=partial))
        return stream

    def echo_execute(tool_call_id, args, signal=None, on_update=None):
        executions.append(tool_call_id)
        return AgentToolResult(content=[TextContent(text="should not run")], details={})

    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        label="Echo",
        execute=echo_execute,
    )

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[echo]),
        _config(model),
        lambda e: None,
        stream_fn=stream_fn,
    )

    tool_results = [message for message in messages if getattr(message, "role", None) == "toolResult"]
    assert executions == []
    assert [result.tool_call_id for result in tool_results] == ["call_truncated"]
    assert tool_results[0].is_error is True
    assert "truncated" in tool_results[0].content[0].text.lower()


def test_after_tool_call_terminate_uses_travis234_batch_semantics() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [
                    ("call_1", "write", {"path": "LOCAL_REVIEW.md", "content": "first"}),
                    ("call_2", "write", {"path": "LOCAL_REVIEW.md", "content": "second"}),
                    ("call_3", "write", {"path": "LOCAL_REVIEW.md", "content": "third"}),
                ],
            )
        return text_response_events(m, "recovered")

    register_api_provider(create_faux_provider(script))

    def write_execute(tool_call_id, args, signal=None, on_update=None):
        executions.append(args["content"])
        return AgentToolResult(content=[TextContent(text=f"wrote:{args['content']}")], details={})

    write = AgentTool(
        name="write",
        description="write",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        label="Write",
        execute=write_execute,
    )
    config = _config(model)
    config.tool_execution = "sequential"

    def after_tool_call(context, signal=None):
        if context.args["content"] == "second":
            return AfterToolCallResult(terminate=True)
        return None

    config.after_tool_call = after_tool_call

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[write]),
        config,
        lambda e: None,
    )

    assert executions == ["first", "second", "third"]
    assert provider_calls["n"] == 2
    assert len([message for message in messages if getattr(message, "role", None) == "toolResult"]) == 3
    assert any(
        getattr(message, "role", None) == "assistant"
        and message.content
        and getattr(message.content[0], "type", None) == "text"
        and message.content[0].text == "recovered"
        for message in messages
    )


def test_prepare_next_turn_snapshot_updates_loop_without_mutating_config() -> None:
    initial_model = faux_model()
    snapshot_model = faux_model()
    snapshot_model.id = "snapshot-model"
    seen_model_ids: list[str] = []
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        seen_model_ids.append(m.id)
        if calls["n"] == 1:
            return tool_call_response_events(m, "echo", {})
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def echo_execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="echo ok")], details={})

    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {}},
        label="Echo",
        execute=echo_execute,
    )
    cfg = _config(initial_model)
    cfg.reasoning = "medium"

    def prepare_next_turn(ctx):
        if ctx.tool_results:
            return AgentLoopTurnUpdate(model=snapshot_model, thinking_level="off")
        return None

    cfg.prepare_next_turn = prepare_next_turn

    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[echo]),
        cfg,
        lambda e: None,
    )

    assert seen_model_ids == ["faux-model", "snapshot-model"]
    assert cfg.model is initial_model
    assert cfg.reasoning == "medium"


def test_should_stop_after_turn_receives_prepare_next_turn_context_snapshot() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "done")))
    cfg = _config(model)
    seen_context_prompts: list[str] = []

    def prepare_next_turn(ctx):
        return AgentLoopTurnUpdate(
            context=AgentContext(system_prompt="snapshot-sys", messages=ctx.context.messages, tools=ctx.context.tools)
        )

    def should_stop_after_turn(ctx):
        seen_context_prompts.append(ctx.context.system_prompt)
        return True

    cfg.prepare_next_turn = prepare_next_turn
    cfg.should_stop_after_turn = should_stop_after_turn

    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(),
        cfg,
        lambda e: None,
    )

    assert seen_context_prompts == ["snapshot-sys"]


def test_tool_execution_update_emit_settles_before_tool_execution_end() -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "echo", {"text": "hi"})
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))
    update_settlement: Future[None] = Future()
    update_seen = threading.Event()
    end_seen = threading.Event()
    events: list[str] = []

    def echo_execute(tool_call_id, args, signal=None, on_update=None):
        on_update(AgentToolResult(content=[TextContent(text="partial")], details={}))
        return AgentToolResult(content=[TextContent(text="final")], details={})

    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        label="Echo",
        execute=echo_execute,
    )

    def emit(event):
        events.append(event.type)
        if event.type == "tool_execution_update":
            update_seen.set()
            return update_settlement
        if event.type == "tool_execution_end":
            end_seen.set()
        return None

    run_error: list[BaseException] = []

    def run_loop() -> None:
        try:
            run_agent_loop([UserMessage(content="go", timestamp=now_ms())], _ctx(tools=[echo]), _config(model), emit)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_loop)
    thread.start()
    assert update_seen.wait(timeout=2)
    assert end_seen.wait(timeout=0.05) is False

    update_settlement.set_result(None)
    thread.join(timeout=2)

    assert run_error == []
    assert thread.is_alive() is False
    assert events.index("tool_execution_update") < events.index("tool_execution_end")


def test_async_tool_update_fanout_is_bounded_and_latest_settles_before_end() -> None:
    model = faux_model()
    provider_calls = 0
    pending_task_counts: list[int] = []
    update_values: list[int] = []
    event_order: list[str] = []

    def provider(_model, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return tool_call_response_events(model, "progress", {})
        return text_response_events(model, "done")

    async def execute(_tool_call_id, _args, signal=None, on_update=None):
        assert on_update is not None
        for index in range(100):
            on_update(AgentToolResult(content=[TextContent(text=str(index))], details={}))
        current = asyncio.current_task()
        pending_task_counts.append(
            len(
                [
                    task
                    for task in asyncio.all_tasks()
                    if task is not current and not task.done()
                ]
            )
        )
        return AgentToolResult(content=[TextContent(text="final")], details={})

    tool = AgentTool(
        name="progress",
        description="progress",
        parameters={"type": "object", "properties": {}},
        label="Progress",
        execute=execute,
    )
    register_api_provider(create_faux_provider(provider))
    config = _config(model)
    config.max_parallel_tools = 1

    def emit(event) -> None:
        event_order.append(event.type)
        if event.type == "tool_execution_update":
            update_values.append(int(event.partial_result.content[0].text))

    async def scenario() -> None:
        await run_agent_loop_async(
            [UserMessage(content="go", timestamp=now_ms())],
            _ctx(tools=[tool]),
            config,
            emit,
            stream_fn=stream_simple,
        )

    asyncio.run(scenario())

    assert max(pending_task_counts) <= 4
    assert len(update_values) <= 65
    assert update_values[-1] == 99
    assert event_order.index("tool_execution_end") > max(
        index for index, value in enumerate(event_order) if value == "tool_execution_update"
    )


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
def test_tool_execution_start_emit_settles_before_tool_runs(mode: str) -> None:
    model = faux_model()
    provider_calls = {"n": 0}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [
                    ("call_first", "first", {}),
                    ("call_second", "second", {}),
                ],
            )
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))
    start_settlement: Future[None] = Future()
    first_start_seen = threading.Event()
    first_tool_ran = threading.Event()
    executions: list[str] = []

    def first_execute(tool_call_id, args, signal=None, on_update=None):
        executions.append("first")
        first_tool_ran.set()
        return AgentToolResult(content=[TextContent(text="first ok")], details={})

    def second_execute(tool_call_id, args, signal=None, on_update=None):
        executions.append("second")
        return AgentToolResult(content=[TextContent(text="second ok")], details={})

    tools = [
        AgentTool(
            name="first",
            description="first",
            parameters={"type": "object", "properties": {}},
            label="First",
            execute=first_execute,
        ),
        AgentTool(
            name="second",
            description="second",
            parameters={"type": "object", "properties": {}},
            label="Second",
            execute=second_execute,
        ),
    ]
    cfg = _config(model)
    cfg.tool_execution = mode

    def emit(event):
        if event.type == "tool_execution_start" and event.tool_name == "first":
            first_start_seen.set()
            return start_settlement
        return None

    run_error: list[BaseException] = []

    def run_loop() -> None:
        try:
            run_agent_loop([UserMessage(content=f"go {mode}", timestamp=now_ms())], _ctx(tools=tools), cfg, emit)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_loop)
    thread.start()
    assert first_start_seen.wait(timeout=2)
    assert first_tool_ran.wait(timeout=0.05) is False

    start_settlement.set_result(None)
    thread.join(timeout=2)

    assert run_error == []
    assert thread.is_alive() is False
    assert executions == ["first", "second"]


def test_all_terminating_parallel_tool_results_stop_without_next_assistant_turn() -> None:
    model = faux_model()
    provider_calls = {"n": 0}

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [
                    ("call_first", "first", {}),
                    ("call_second", "second", {}),
                ],
            )
        return text_response_events(m, "should not run")

    register_api_provider(create_faux_provider(script))

    def terminating_execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"{tool_call_id} done")], details={}, terminate=True)

    tools = [
        AgentTool(
            name="first",
            description="first",
            parameters={"type": "object", "properties": {}},
            label="First",
            execute=terminating_execute,
        ),
        AgentTool(
            name="second",
            description="second",
            parameters={"type": "object", "properties": {}},
            label="Second",
            execute=terminating_execute,
        ),
    ]
    cfg = _config(model)
    cfg.tool_execution = "parallel"

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=tools),
        cfg,
        lambda e: None,
    )

    assert provider_calls["n"] == 1
    assert [getattr(message, "role", None) for message in messages] == [
        "user",
        "assistant",
        "toolResult",
        "toolResult",
    ]


def test_core_parallel_dispatch_ignores_travis_batch_safety_for_bash_like_travis234() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    messages_holder: list[list[Message]] = []
    run_error: list[BaseException] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [
                    ("call_1", "bash", {"command": "sleep 1"}),
                    ("call_2", "bash", {"command": "pwd"}),
                ],
            )
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def bash_execute(tool_call_id, args, signal=None, on_update=None):
        if tool_call_id == "call_1":
            first_started.set()
            release_first.wait(timeout=2)
        if tool_call_id == "call_2":
            second_started.set()
        return AgentToolResult(content=[TextContent(text=f"ok:{tool_call_id}")], details={})

    bash = AgentTool(
        name="bash",
        description="bash",
        parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        label="Bash",
        execute=bash_execute,
    )
    cfg = _config(model)
    cfg.tool_execution = "parallel"

    def run_loop() -> None:
        try:
            messages_holder.append(
                run_agent_loop(
                    [UserMessage(content="go", timestamp=now_ms())],
                    _ctx(tools=[bash]),
                    cfg,
                    lambda e: None,
                )
            )
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_loop)
    thread.start()
    try:
        assert first_started.wait(timeout=2)
        assert second_started.wait(timeout=0.2)
    finally:
        release_first.set()
        thread.join(timeout=2)

    assert run_error == []
    assert thread.is_alive() is False
    tool_results = [message for message in messages_holder[0] if getattr(message, "role", None) == "toolResult"]
    assert [message.tool_call_id for message in tool_results] == ["call_1", "call_2"]


def test_parallel_tool_execution_end_events_emit_from_loop_thread() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    end_threads: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [
                    ("call_1", "grep", {"pattern": "a"}),
                    ("call_2", "grep", {"pattern": "b"}),
                ],
            )
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def grep_execute(tool_call_id, args, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"ok:{tool_call_id}")], details={})

    grep = AgentTool(
        name="grep",
        description="grep",
        parameters={"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
        label="Grep",
        execute=grep_execute,
    )
    cfg = _config(model)
    cfg.tool_execution = "parallel"

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[grep]),
        cfg,
        lambda event: end_threads.append(threading.current_thread().name)
        if event.type == "tool_execution_end"
        else None,
    )

    assert end_threads == ["MainThread", "MainThread"]
    tool_results = [message for message in messages if getattr(message, "role", None) == "toolResult"]
    assert [message.tool_call_id for message in tool_results] == ["call_1", "call_2"]


def test_parallel_tool_end_events_follow_completion_order_while_results_keep_source_order() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    fast_finished = threading.Event()
    tool_end_ids: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [
                    ("call_slow", "slow", {}),
                    ("call_fast", "fast", {}),
                ],
            )
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def slow_execute(tool_call_id, args, signal=None, on_update=None):
        assert fast_finished.wait(timeout=2)
        time.sleep(0.05)
        return AgentToolResult(content=[TextContent(text="slow done")], details={})

    def fast_execute(tool_call_id, args, signal=None, on_update=None):
        fast_finished.set()
        return AgentToolResult(content=[TextContent(text="fast done")], details={})

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
    cfg = _config(model)
    cfg.tool_execution = "parallel"

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=tools),
        cfg,
        lambda event: tool_end_ids.append(event.tool_call_id)
        if event.type == "tool_execution_end"
        else None,
    )

    assert tool_end_ids == ["call_fast", "call_slow"]
    tool_results = [message for message in messages if getattr(message, "role", None) == "toolResult"]
    assert [message.tool_call_id for message in tool_results] == ["call_slow", "call_fast"]


def test_parallel_tools_are_bounded_and_callbacks_stay_on_coordinator_thread() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    active = 0
    maximum = 0
    lock = threading.Lock()
    callback_threads: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return _multi_tool_call_response_events(
                m,
                [(f"call_{index}", "probe", {"index": index}) for index in range(12)],
            )
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def execute(tool_call_id, args, signal=None, on_update=None):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        on_update(AgentToolResult(content=[TextContent(text="partial")]))
        time.sleep(0.02)
        with lock:
            active -= 1
        return AgentToolResult(content=[TextContent(text=str(args["index"]))])

    def after(context, signal):
        callback_threads.append(threading.current_thread().name)
        return None

    tool = AgentTool(
        name="probe",
        description="probe",
        parameters={
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
        label="Probe",
        execute=execute,
    )
    cfg = _config(model)
    cfg.tool_execution = "parallel"
    cfg.max_parallel_tools = 3
    cfg.after_tool_call = after

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[tool]),
        cfg,
        lambda event: callback_threads.append(threading.current_thread().name)
        if event.type in ("tool_execution_update", "tool_execution_end")
        else None,
    )

    assert maximum == 3
    assert set(callback_threads) == {"MainThread"}
    tool_results = [message for message in messages if getattr(message, "role", None) == "toolResult"]
    assert [message.tool_call_id for message in tool_results] == [f"call_{index}" for index in range(12)]


def test_parallel_dispatch_creates_no_more_than_max_parallel_workers() -> None:
    model = faux_model()
    provider_calls = 0
    entered = 0
    release = asyncio.Event()
    pending_task_counts: list[int] = []

    def provider(_model, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return _multi_tool_call_response_events(
                model,
                [(f"call_{index}", "probe", {"index": index}) for index in range(12)],
            )
        return text_response_events(model, "done")

    async def execute(_tool_call_id, args, signal=None, on_update=None):
        nonlocal entered
        entered += 1
        current = asyncio.current_task()
        pending_task_counts.append(
            len(
                [
                    task
                    for task in asyncio.all_tasks()
                    if task is not current and not task.done()
                ]
            )
        )
        if entered == 3:
            release.set()
        await release.wait()
        return AgentToolResult(content=[TextContent(text=str(args["index"]))], details={})

    tool = AgentTool(
        name="probe",
        description="probe",
        parameters={
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
        label="Probe",
        execute=execute,
    )
    register_api_provider(create_faux_provider(provider))
    config = _config(model)
    config.tool_execution = "parallel"
    config.max_parallel_tools = 3

    async def scenario() -> None:
        await run_agent_loop_async(
            [UserMessage(content="go", timestamp=now_ms())],
            _ctx(tools=[tool]),
            config,
            lambda _event: None,
            stream_fn=stream_simple,
        )

    asyncio.run(scenario())

    # The owner remains pending alongside one worker and one update relay per active tool.
    assert max(pending_task_counts) <= (config.max_parallel_tools * 2) + 1
    assert entered == 12


def test_should_stop_after_turn_halts_loop() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "x")))
    cfg = _config(model)
    cfg.should_stop_after_turn = lambda ctx: True
    turn_starts = 0
    events: list[str] = []
    run_agent_loop([UserMessage(content="hi", timestamp=now_ms())], _ctx(), cfg, lambda e: events.append(e.type))
    assert events.count("turn_start") == 1


def test_before_tool_call_block_yields_error_result() -> None:
    model = faux_model()

    def script(m, c):
        return tool_call_response_events(m, "danger", {})

    register_api_provider(create_faux_provider(script))
    danger = AgentTool(
        name="danger", description="d", parameters={"type": "object"}, label="D",
        execute=lambda *a, **k: AgentToolResult(content=[TextContent(text="ran")], details={}),
    )
    cfg = _config(model)
    cfg.before_tool_call = lambda ctx, signal: BeforeToolCallResult(block=True, reason="nope")
    # avoid infinite loop: after the blocked tool, model would be called again; make 2nd call finalize
    calls = {"n": 0}

    def script2(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "danger", {})
        return text_response_events(m, "stopped")

    reset_api_providers()
    register_api_provider(create_faux_provider(script2))
    ends: list = []
    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())], _ctx(tools=[danger]), cfg,
        lambda e: ends.append(e) if e.type == "tool_execution_end" else None,
    )
    end = ends[0]
    assert end.is_error is True
    assert "nope" in end.result.content[0].text


def test_unknown_tool_returns_error_result() -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "missing", {})
        return text_response_events(m, "ok")

    register_api_provider(create_faux_provider(script))
    ends: list = []
    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())], _ctx(tools=[]), _config(model),
        lambda e: ends.append(e) if e.type == "tool_execution_end" else None,
    )
    assert ends[0].is_error is True
    assert "not found" in ends[0].result.content[0].text


def test_invalid_tool_result_becomes_error_tool_result() -> None:
    model = faux_model()
    provider_calls = 0
    end_reasons: dict[str, str | None] = {}

    def provider(_model, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return tool_call_response_events(model, "broken", {}, call_id="call_broken")
        return text_response_events(model, "recovered")

    broken = AgentTool(
        name="broken",
        description="broken",
        parameters={"type": "object", "properties": {}},
        label="Broken",
        execute=lambda *_args, **_kwargs: None,
    )
    register_api_provider(create_faux_provider(provider))

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[broken]),
        _config(model),
        lambda event: end_reasons.__setitem__(event.tool_call_id, event.reason_code)
        if event.type == "tool_execution_end"
        else None,
    )

    tool_result = next(message for message in messages if getattr(message, "role", None) == "toolResult")
    assert tool_result.is_error is True
    assert [block.text for block in tool_result.content] == [
        "Tool broken returned invalid result type NoneType; expected AgentToolResult"
    ]
    assert end_reasons == {"call_broken": "invalid_tool_result"}
    assert messages[-1].content == [TextContent(text="recovered")]
    assert provider_calls == 2


def test_invalid_tool_result_content_is_rejected_without_crashing_run() -> None:
    model = faux_model()
    provider_calls = 0

    def provider(_model, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return tool_call_response_events(model, "broken", {}, call_id="call_content")
        return text_response_events(model, "recovered")

    broken = AgentTool(
        name="broken",
        description="broken",
        parameters={"type": "object", "properties": {}},
        label="Broken",
        execute=lambda *_args, **_kwargs: AgentToolResult(content=["not-content"]),
    )
    register_api_provider(create_faux_provider(provider))

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[broken]),
        _config(model),
        lambda _event: None,
    )

    tool_result = next(message for message in messages if getattr(message, "role", None) == "toolResult")
    assert tool_result.is_error is True
    assert [block.text for block in tool_result.content] == [
        "Tool broken returned AgentToolResult with invalid content; expected TextContent or ImageContent items"
    ]
    assert messages[-1].content == [TextContent(text="recovered")]


@pytest.mark.parametrize(
    "result",
    [
        AgentToolResult(content=[TextContent(text="bad terminate")], terminate="yes"),
        AgentToolResult(content=[TextContent(text="bad tools")], added_tool_names=["valid", 7]),
    ],
)
def test_invalid_tool_result_metadata_becomes_error(result: AgentToolResult) -> None:
    model = faux_model()
    provider_calls = 0

    def provider(_model, _context):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return tool_call_response_events(model, "broken", {})
        return text_response_events(model, "recovered")

    register_api_provider(create_faux_provider(provider))
    tool = AgentTool(
        name="broken",
        description="broken",
        parameters={"type": "object", "properties": {}},
        label="Broken",
        execute=lambda *_args, **_kwargs: result,
    )

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[tool]),
        _config(model),
        lambda _event: None,
    )

    tool_result = next(message for message in messages if getattr(message, "role", None) == "toolResult")
    assert tool_result.is_error is True
    assert [block.text for block in tool_result.content] == [
        "Tool broken returned AgentToolResult with invalid metadata"
    ]


@pytest.mark.parametrize("case", ["before_block", "unknown_tool", "invalid_arguments"])
def test_immediate_tool_outcomes_bypass_after_hook(case: str) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    after_calls: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            name = "missing" if case == "unknown_tool" else "probe"
            arguments = {} if case == "invalid_arguments" else {"value": "ok"}
            return tool_call_response_events(m, name, arguments, call_id=f"call_{case}")
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))
    tool = AgentTool(
        name="probe",
        description="probe",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        label="Probe",
        execute=lambda *_args: AgentToolResult(content=[TextContent(text="ok")]),
    )
    cfg = _config(model)
    if case == "before_block":
        cfg.before_tool_call = lambda *_args: BeforeToolCallResult(block=True, reason="blocked")
    cfg.after_tool_call = lambda context, signal: after_calls.append(context.tool_call.id)

    messages = run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[tool]),
        cfg,
        lambda _event: None,
    )

    assert after_calls == []
    result = next(message for message in messages if getattr(message, "role", None) == "toolResult")
    assert result.is_error is True


def test_invoked_tool_failure_runs_after_hook_once() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    after_calls: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "probe", {}, call_id="call_failure")
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    def execute(*_args):
        raise RuntimeError("tool failed")

    tool = AgentTool(
        name="probe",
        description="probe",
        parameters={"type": "object"},
        label="Probe",
        execute=execute,
    )
    cfg = _config(model)
    cfg.after_tool_call = lambda context, signal: after_calls.append(context.tool_call.id)

    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[tool]),
        cfg,
        lambda _event: None,
    )

    assert after_calls == ["call_failure"]


def test_unknown_tool_error_reports_active_tool_catalog_for_recovery() -> None:
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "glob", {"pattern": "**/*.py"})
        return text_response_events(m, "ok")

    register_api_provider(create_faux_provider(script))
    read = AgentTool(
        name="read",
        description="read",
        parameters={"type": "object"},
        label="Read",
        execute=lambda *a, **k: AgentToolResult(content=[TextContent(text="read")], details={}),
    )
    grep = AgentTool(
        name="grep",
        description="grep",
        parameters={"type": "object"},
        label="Grep",
        execute=lambda *a, **k: AgentToolResult(content=[TextContent(text="grep")], details={}),
    )
    find = AgentTool(
        name="find",
        description="find",
        parameters={"type": "object"},
        label="Find",
        execute=lambda *a, **k: AgentToolResult(content=[TextContent(text="find")], details={}),
    )
    ls = AgentTool(
        name="ls",
        description="ls",
        parameters={"type": "object"},
        label="List",
        execute=lambda *a, **k: AgentToolResult(content=[TextContent(text="ls")], details={}),
    )
    ends: list = []

    run_agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(tools=[read, grep, find, ls]),
        _config(model),
        lambda e: ends.append(e) if e.type == "tool_execution_end" else None,
    )

    assert ends[0].is_error is True
    text = ends[0].result.content[0].text
    assert "Tool glob not found" in text
    assert "Available tools: read, grep, find, ls" in text
    assert "glob is not available in this tool catalog" not in text
