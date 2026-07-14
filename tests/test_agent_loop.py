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


def test_agent_class_reduces_state() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    seen: list[str] = []
    agent.subscribe(lambda e: seen.append(e.type))
    agent.prompt("hello")
    assert "agent_end" in seen
    roles = [getattr(m, "role", None) for m in agent.state.messages]
    assert "user" in roles and "assistant" in roles
    assert agent.state.is_streaming is False


def test_agent_prompt_normalizes_string_input_to_travis234_content_blocks() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)

    agent.prompt("plain")

    user = next(message for message in agent.state.messages if getattr(message, "role", None) == "user")
    assert user.content == [TextContent(text="plain")]


def test_agent_prompt_normalizes_string_input_to_travis234_content_blocks_with_images() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    image = ImageContent(data="aW1n", mime_type="image/png")

    agent.prompt("hello", images=[image])

    user = next(message for message in agent.state.messages if getattr(message, "role", None) == "user")
    assert user.content == [TextContent(text="hello"), image]


def test_agent_reset_clears_streaming_state_like_travis234() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    agent.state.is_streaming = True

    agent.reset()

    assert agent.state.is_streaming is False


def test_agent_reset_does_not_release_an_active_run() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    first_stream_started = threading.Event()
    release_first_stream = threading.Event()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        stream = create_assistant_message_event_stream()
        events = text_response_events(model, "first done")
        stream.push(type(events[0])(partial=events[0].partial))
        first_stream_started.set()

        def finish() -> None:
            release_first_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    first_thread = threading.Thread(target=lambda: agent.prompt("first", stream_fn=stream_fn))
    first_thread.start()
    assert first_stream_started.wait(timeout=2)

    try:
        with pytest.raises(RuntimeError, match="active run"):
            agent.reset()
        assert agent.state.is_streaming is True
        with pytest.raises(RuntimeError, match="already processing"):
            agent.prompt("second", stream_fn=stream_fn)
    finally:
        release_first_stream.set()
        first_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert calls["n"] == 1
    assert agent.state.is_streaming is False


def test_run_lease_tracks_owner_waits_and_releases_idempotently() -> None:
    lease = RunLease()
    token = lease.acquire("busy")
    other_thread_owns: list[bool] = []

    thread = threading.Thread(target=lambda: other_thread_owns.append(lease.owned_by_current_thread))
    thread.start()
    thread.join(timeout=1)

    assert lease.active is True
    assert lease.owned_by_current_thread is True
    assert other_thread_owns == [False]
    assert lease.wait(timeout=0.01) is False

    token.release()
    token.release()

    assert lease.active is False
    assert lease.wait(timeout=0.01) is True


def test_agent_rejects_prompt_while_streaming() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    first_stream_started = threading.Event()
    release_first_stream = threading.Event()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        if calls["n"] == 1:
            stream = create_assistant_message_event_stream()
            start_message = text_response_events(model, "")[0].partial
            stream.push(type(text_response_events(model, "")[0])(partial=start_message))
            first_stream_started.set()

            def finish() -> None:
                release_first_stream.wait(timeout=2)
                for event in text_response_events(model, "first done")[1:]:
                    stream.push(event)

            threading.Thread(target=finish, daemon=True).start()
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "second")).stream_simple(
            model, context, options
        )

    first_error: list[BaseException] = []

    def run_first() -> None:
        try:
            agent.prompt("first", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            first_error.append(error)

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert first_stream_started.wait(timeout=2)
    assert agent.state.is_streaming is True

    try:
        try:
            agent.prompt("second", stream_fn=stream_fn)
            assert False, "expected concurrent prompt rejection"
        except RuntimeError as error:
            assert "already processing" in str(error)
    finally:
        release_first_stream.set()
        first_thread.join(timeout=2)

    assert first_error == []
    assert calls["n"] == 1
    assert agent.state.is_streaming is False


def test_agent_abort_signal_is_fresh_for_next_prompt() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    first_stream_started = threading.Event()
    release_first_stream = threading.Event()
    stream_calls = {"n": 0}
    tool_signals: list[bool] = []

    def stream_fn(model, context, options):
        stream_calls["n"] += 1
        stream = create_assistant_message_event_stream()
        if stream_calls["n"] == 1:
            events = text_response_events(model, "first done")
            stream.push(type(events[0])(partial=events[0].partial))
            first_stream_started.set()

            def finish() -> None:
                release_first_stream.wait(timeout=2)
                for event in events[1:]:
                    stream.push(event)

            threading.Thread(target=finish, daemon=True).start()
            return stream
        if stream_calls["n"] == 2:
            for event in tool_call_response_events(model, "probe", {}):
                stream.push(event)
            return stream
        for event in text_response_events(model, "second done"):
            stream.push(event)
        return stream

    def probe_execute(tool_call_id, args, signal=None, on_update=None):
        tool_signals.append(bool(signal and signal.aborted))
        return AgentToolResult(content=[TextContent(text="probe ok")], details={})

    probe = AgentTool(
        name="probe",
        description="probe",
        parameters={"type": "object", "properties": {}},
        label="Probe",
        execute=probe_execute,
    )
    agent.state.tools = [probe]
    first_error: list[BaseException] = []

    def run_first() -> None:
        try:
            agent.prompt("first", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            first_error.append(error)

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert first_stream_started.wait(timeout=2)
    agent.abort()
    release_first_stream.set()
    first_thread.join(timeout=2)

    assert first_error == []
    assert agent.state.is_streaming is False

    agent.prompt("second", stream_fn=stream_fn)

    assert tool_signals == [False]


def test_agent_stream_options_include_active_signal() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert, thinking_level="medium")
    stream_started = threading.Event()
    release_stream = threading.Event()
    seen_options: list[object] = []

    def stream_fn(model, context, options):
        seen_options.append(options)
        stream = create_assistant_message_event_stream()
        events = text_response_events(model, "done")
        stream.push(type(events[0])(partial=events[0].partial))
        stream_started.set()

        def finish() -> None:
            release_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    run_error: list[BaseException] = []

    def run_prompt() -> None:
        try:
            agent.prompt("hello", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_prompt)
    thread.start()
    assert stream_started.wait(timeout=2)
    assert len(seen_options) == 1
    options = seen_options[0]
    assert options is not None
    assert getattr(options, "signal") is agent.signal
    assert getattr(options, "reasoning") == "medium"
    assert agent.signal.aborted is False

    agent.abort()
    assert getattr(options, "signal").aborted is True
    release_stream.set()
    thread.join(timeout=2)

    assert run_error == []


def test_continue_processes_queued_follow_up_after_assistant_turn() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    response_count = {"n": 0}

    def stream_fn(model, context, options):
        response_count["n"] += 1
        return create_faux_provider(
            lambda m, c: text_response_events(m, f"processed {response_count['n']}")
        ).stream_simple(model, context, options)

    agent.prompt("initial", stream_fn=stream_fn)
    agent.follow_up(UserMessage(content="queued follow-up", timestamp=now_ms()))

    agent.continue_(stream_fn=stream_fn)

    user_messages = [message for message in agent.state.messages if getattr(message, "role", None) == "user"]
    assert any(getattr(message, "content", None) == "queued follow-up" for message in user_messages)
    assert getattr(agent.state.messages[-1], "role", None) == "assistant"
    assert response_count["n"] == 2


def test_continue_validation_does_not_create_a_failed_turn() -> None:
    agent = Agent(system_prompt="sys", model=faux_model(), convert_to_llm=_convert)
    events: list[str] = []
    agent.subscribe(lambda event: events.append(event.type))

    with pytest.raises(ValueError, match="No messages to continue from"):
        agent.continue_()

    assert agent.state.messages == []
    assert events == []
    assert agent.state.is_streaming is False


def test_continue_keeps_one_at_a_time_steering_from_assistant_tail() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    response_count = {"n": 0}

    def stream_fn(model, context, options):
        response_count["n"] += 1
        return create_faux_provider(
            lambda m, c: text_response_events(m, f"processed {response_count['n']}")
        ).stream_simple(model, context, options)

    agent.prompt("initial", stream_fn=stream_fn)
    agent.steer(UserMessage(content="steering 1", timestamp=now_ms()))
    agent.steer(UserMessage(content="steering 2", timestamp=now_ms()))

    agent.continue_(stream_fn=stream_fn)

    assert [getattr(message, "role", None) for message in agent.state.messages[-4:]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [getattr(message, "content", None) for message in agent.state.messages[-4::2]] == [
        "steering 1",
        "steering 2",
    ]
    assert response_count["n"] == 3


def test_wait_for_idle_waits_for_agent_end_listeners() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    listener_entered = threading.Event()
    release_listener = threading.Event()

    def listener(event):
        if event.type == "agent_end":
            listener_entered.set()
            release_listener.wait(timeout=2)

    agent.subscribe(listener)
    run_error: list[BaseException] = []

    def run_prompt() -> None:
        try:
            agent.prompt(
                "hello",
                stream_fn=lambda model, context, options: create_faux_provider(
                    lambda m, c: text_response_events(m, "done")
                ).stream_simple(model, context, options),
            )
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_prompt)
    thread.start()
    assert listener_entered.wait(timeout=2)
    assert agent.state.is_streaming is True
    assert agent.wait_for_idle(timeout=0.01) is False

    release_listener.set()
    thread.join(timeout=2)

    assert run_error == []
    assert agent.wait_for_idle(timeout=0.01) is True
    assert agent.state.is_streaming is False


def test_agent_async_prompt_awaits_async_listener() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    seen: list[str] = []

    async def listener(event, signal) -> None:
        await asyncio.sleep(0)
        seen.append(event.type)

    agent.subscribe(listener)
    asyncio.run(agent.async_prompt("hello"))

    assert seen[-1] == "agent_end"
    assert agent.wait_for_idle(timeout=0.1) is True


def test_agent_async_prompt_awaits_async_hook_and_tool() -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    observed: list[str] = []

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return tool_call_response_events(m, "probe", {}, call_id="call_async")
        return text_response_events(m, "done")

    register_api_provider(create_faux_provider(script))

    async def before(context, signal):
        await asyncio.sleep(0)
        observed.append("before")
        return None

    async def execute(tool_call_id, args, signal=None, on_update=None):
        await asyncio.sleep(0)
        observed.append("tool")
        return AgentToolResult(content=[TextContent(text="ok")])

    agent = Agent(
        system_prompt="sys",
        model=model,
        convert_to_llm=_convert,
        tools=[
            AgentTool(
                name="probe",
                label="Probe",
                description="probe",
                parameters={"type": "object"},
                execute=execute,
            )
        ],
        before_tool_call=before,
    )

    asyncio.run(agent.async_prompt("run"))

    assert observed == ["before", "tool"]
    assert agent.state.messages[-1].content[0].text == "done"


def test_agent_sync_prompt_rejects_running_event_loop() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="Use the async travis API"):
            agent.prompt("hello")

    asyncio.run(exercise())


def test_listener_receives_active_abort_signal() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    stream_started = threading.Event()
    assistant_started = threading.Event()
    release_stream = threading.Event()
    seen_signals: list[object] = []

    def listener(event, signal):
        if event.type == "message_start" and getattr(event.message, "role", None) == "assistant":
            seen_signals.append(signal)
            assistant_started.set()

    def stream_fn(model, context, options):
        stream = create_assistant_message_event_stream()
        events = text_response_events(model, "done")
        stream.push(type(events[0])(partial=events[0].partial))
        stream_started.set()

        def finish() -> None:
            release_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    agent.subscribe(listener)
    run_error: list[BaseException] = []

    def run_prompt() -> None:
        try:
            agent.prompt("hello", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_prompt)
    thread.start()
    assert stream_started.wait(timeout=2)
    assert assistant_started.wait(timeout=2)
    assert seen_signals == [agent.signal]
    assert getattr(seen_signals[0], "aborted") is False

    agent.abort()
    assert getattr(seen_signals[0], "aborted") is True
    release_stream.set()
    thread.join(timeout=2)

    assert run_error == []


def test_abort_signal_callbacks_fire_once_and_can_unsubscribe() -> None:
    signal = AbortSignal()
    calls: list[str] = []
    signal.add_callback(lambda: calls.append("kept"))
    unsubscribe = signal.add_callback(lambda: calls.append("removed"))
    unsubscribe()

    signal.abort()
    signal.abort()

    assert calls == ["kept"]


def test_agent_prepare_next_turn_receives_active_abort_signal() -> None:
    model = faux_model()
    seen_signals: list[object] = []

    def prepare_next_turn(signal):
        seen_signals.append(signal)
        return None

    agent = Agent(
        system_prompt="sys",
        model=model,
        convert_to_llm=_convert,
        prepare_next_turn=prepare_next_turn,
    )

    agent.prompt(
        "hello",
        stream_fn=lambda model, context, options: create_faux_provider(
            lambda m, c: text_response_events(m, "done")
        ).stream_simple(model, context, options),
    )

    assert seen_signals == [agent.signal]
    assert getattr(seen_signals[0], "aborted") is False


def test_agent_prepare_next_turn_with_context_receives_context_and_signal() -> None:
    model = faux_model()
    seen_contexts: list[ShouldStopAfterTurnContext] = []
    seen_signals: list[object] = []

    def prepare_next_turn_with_context(context, signal):
        seen_contexts.append(context)
        seen_signals.append(signal)
        return None

    agent = Agent(
        system_prompt="sys",
        model=model,
        convert_to_llm=_convert,
        prepare_next_turn_with_context=prepare_next_turn_with_context,
    )

    agent.prompt(
        "hello",
        stream_fn=lambda model, context, options: create_faux_provider(
            lambda m, c: text_response_events(m, "done")
        ).stream_simple(model, context, options),
    )

    assert len(seen_contexts) == 1
    assert seen_contexts[0].message.content[0].text == "done"
    assert seen_contexts[0].context.system_prompt == "sys"
    assert seen_signals == [agent.signal]


def test_agent_loop_runtime_exception_fails_stream() -> None:
    model = faux_model()

    def stream_fn(model, context, options):
        raise RuntimeError("provider exploded")

    stream = agent_loop(
        [UserMessage(content="go", timestamp=now_ms())],
        _ctx(),
        _config(model),
        stream_fn=stream_fn,
    )

    with pytest.raises(RuntimeError, match="provider exploded"):
        stream.result_sync()
    assert not any(event.type == "agent_end" for event in list(stream))


def test_agent_loop_continue_runtime_exception_fails_stream() -> None:
    model = faux_model()
    context = AgentContext(
        system_prompt="sys",
        messages=[UserMessage(content="continue", timestamp=now_ms())],
        tools=[],
    )

    def stream_fn(model, context, options):
        raise RuntimeError("provider exploded during continue")

    stream = agent_loop_continue(context, _config(model), stream_fn=stream_fn)
    events = list(stream)

    assert [event.type for event in events] == ["agent_start", "turn_start"]
    with pytest.raises(RuntimeError, match="provider exploded during continue"):
        stream.result_sync()


def test_prompt_failure_emits_assistant_error_lifecycle() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    events: list[str] = []
    agent.subscribe(lambda event: events.append(event.type))

    def stream_fn(model, context, options):
        raise RuntimeError("provider exploded")

    new_messages = agent.prompt("hello", stream_fn=stream_fn)

    assert "message_start" in events
    assert "message_end" in events
    assert "turn_end" in events
    assert events[-1] == "agent_end"
    assert len(new_messages) == 1
    failure = new_messages[0]
    assert getattr(failure, "role", None) == "assistant"
    assert getattr(failure, "stop_reason", None) == "error"
    assert getattr(failure, "error_message", None) == "provider exploded"
    assert getattr(agent.state.messages[-1], "error_message", None) == "provider exploded"
    assert agent.state.is_streaming is False


def test_agent_forwards_provider_runtime_stream_options() -> None:
    model = faux_model()
    on_payload = object()
    on_response = object()
    agent = Agent(
        system_prompt="sys",
        model=model,
        convert_to_llm=_convert,
        thinking_level="high",
        session_id="session-abc",
        transport="websocket",
        thinking_budgets={"high": 2048},
        max_retry_delay_ms=1234,
        on_payload=on_payload,
        on_response=on_response,
    )
    seen_options: list[object] = []

    def stream_fn(model, context, options):
        seen_options.append(options)
        return create_faux_provider(lambda m, c: text_response_events(m, "done")).stream_simple(
            model, context, options
        )

    agent.prompt("hello", stream_fn=stream_fn)

    assert len(seen_options) == 1
    options = seen_options[0]
    assert getattr(options, "session_id") == "session-abc"
    assert getattr(options, "transport") == "websocket"
    assert getattr(options, "thinking_budgets") == {"high": 2048}
    assert getattr(options, "max_retry_delay_ms") == 1234
    assert getattr(options, "on_payload") is on_payload
    assert getattr(options, "on_response") is on_response


def test_agent_queue_status_clear_and_modes() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)

    assert agent.steering_mode == "one-at-a-time"
    assert agent.follow_up_mode == "one-at-a-time"
    assert agent.has_queued_messages() is False

    agent.steering_mode = "all"
    agent.follow_up_mode = "all"
    assert agent.steering_mode == "all"
    assert agent.follow_up_mode == "all"

    agent.steer(UserMessage(content="steer", timestamp=now_ms()))
    assert agent.has_queued_messages() is True
    agent.clear_steering_queue()
    assert agent.has_queued_messages() is False

    agent.follow_up(UserMessage(content="follow", timestamp=now_ms()))
    assert agent.has_queued_messages() is True
    agent.clear_follow_up_queue()
    assert agent.has_queued_messages() is False

    agent.steer(UserMessage(content="steer", timestamp=now_ms()))
    agent.follow_up(UserMessage(content="follow", timestamp=now_ms()))
    assert agent.has_queued_messages() is True
    agent.clear_all_queues()
    assert agent.has_queued_messages() is False
