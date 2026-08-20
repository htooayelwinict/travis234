"""Agent state, lifecycle, listener, and queue contracts outside the core loop owner."""


from __future__ import annotations


import asyncio


import threading


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


def _config(model):
    from travis.agent.types import AgentLoopConfig

    return AgentLoopConfig(model=model, convert_to_llm=_convert)


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


def test_public_listener_mutation_cannot_change_canonical_agent_message() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)

    def listener(event) -> None:
        if event.type == "message_end" and getattr(event.message, "role", None) == "assistant":
            event.message.content[0].text = "mutated-by-observer"

    agent.subscribe(listener)
    agent.prompt(
        "hello",
        stream_fn=lambda model, context, options: create_faux_provider(
            lambda m, c: text_response_events(m, "canonical")
        ).stream_simple(model, context, options),
    )

    assert agent.state.messages[-1].content == [TextContent(text="canonical")]


def test_public_listener_failure_does_not_stop_run_or_later_listener() -> None:
    model = faux_model()
    agent = Agent(system_prompt="sys", model=model, convert_to_llm=_convert)
    later_events: list[str] = []

    def failing_listener(event) -> None:
        if event.type == "message_end" and getattr(event.message, "role", None) == "assistant":
            raise RuntimeError("observer exploded")

    agent.subscribe(failing_listener)
    agent.subscribe(lambda event: later_events.append(event.type))

    messages = agent.prompt(
        "hello",
        stream_fn=lambda model, context, options: create_faux_provider(
            lambda m, c: text_response_events(m, "canonical")
        ).stream_simple(model, context, options),
    )

    assert messages[-1].content == [TextContent(text="canonical")]
    assert agent.state.messages[-1].content == [TextContent(text="canonical")]
    assert later_events[-2:] == ["turn_end", "agent_end"]


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
