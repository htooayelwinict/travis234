"""Agent loop over AgentMessage."""

from __future__ import annotations

import asyncio
import copy
import inspect
import threading
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import replace
from typing import Any, Callable, Optional, Union

from travis.ai.event_stream import EventStream
from travis.ai.stream import stream_simple as default_stream_simple
from travis.ai.types import (
    AssistantMessage,
    Context,
    SimpleStreamOptions,
    TextContent,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)
from travis.ai.validation import validate_tool_arguments
from travis.agent.async_utils import resolve, run_sync
from travis.agent.tool_coordinator import ToolCoordinator
from travis.agent.types import (
    AbortSignal,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    IterationLimitContext,
    ImmediateToolOutcome,
    AgentMessage,
    AgentStartEvent,
    AgentTool,
    AgentToolResult,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    PreparedToolCall,
    ShouldStopAfterTurnContext,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)

AgentEventSink = Callable[[AgentEvent], Any]
_ITERATION_DONE = object()


class AgentEventStream(EventStream):
    """EventStream that completes on `agent_end` with the returned messages."""

    def push(self, event: AgentEvent) -> None:
        super().push(event)
        if event.type == "agent_end":
            self.end(event.messages)


def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[Callable] = None,
) -> AgentEventStream:
    stream = AgentEventStream()

    def _run() -> None:
        try:
            run_agent_loop(prompts, context, config, stream.push, signal, stream_fn)
        except Exception as error:  # pragma: no cover - exercised via EventStream.result
            stream.fail(error)

    threading.Thread(target=_run, daemon=True).start()
    return stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[Callable] = None,
) -> AgentEventStream:
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if _role_of(context.messages[-1]) == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    stream = AgentEventStream()

    def _run() -> None:
        try:
            run_agent_loop_continue(context, config, stream.push, signal, stream_fn)
        except Exception as error:  # pragma: no cover - exercised via EventStream.result
            stream.fail(error)

    threading.Thread(target=_run, daemon=True).start()
    return stream


def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[Callable] = None,
) -> list[AgentMessage]:
    return run_sync(run_agent_loop_async(prompts, context, config, emit, signal, stream_fn))


async def run_agent_loop_async(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[Callable] = None,
) -> list[AgentMessage]:
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=context.tools,
    )

    await _emit_event(emit, AgentStartEvent())
    await _emit_event(emit, TurnStartEvent())
    for prompt in prompts:
        await _emit_event(emit, MessageStartEvent(message=prompt))
        await _emit_event(emit, MessageEndEvent(message=prompt))

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[Callable] = None,
) -> list[AgentMessage]:
    return run_sync(run_agent_loop_continue_async(context, config, emit, signal, stream_fn))


async def run_agent_loop_continue_async(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[Callable] = None,
) -> list[AgentMessage]:
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if _role_of(context.messages[-1]) == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    new_messages: list[AgentMessage] = []
    current_context = AgentContext(
        system_prompt=context.system_prompt, messages=context.messages, tools=context.tools
    )

    await _emit_event(emit, AgentStartEvent())
    await _emit_event(emit, TurnStartEvent())

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


async def _run_loop(
    initial_context: AgentContext,
    new_messages: list[AgentMessage],
    initial_config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[Callable],
) -> None:
    current_context = initial_context
    config = initial_config
    first_turn = True
    api_call_count = 0
    pending_messages = await _get_messages(config.get_steering_messages)

    while True:
        has_more_tool_calls = True

        while has_more_tool_calls or pending_messages:
            if not first_turn:
                await _emit_event(emit, TurnStartEvent())
            else:
                first_turn = False

            if pending_messages:
                for message in pending_messages:
                    await _emit_event(emit, MessageStartEvent(message=message))
                    await _emit_event(emit, MessageEndEvent(message=message))
                    current_context.messages.append(message)
                    new_messages.append(message)
                pending_messages = []

            if _iteration_budget_exhausted(api_call_count, config):
                await _handle_iteration_limit(
                    current_context, new_messages, config, signal, emit, stream_fn, api_call_count
                )
                return

            _consume_iteration_budget(config)
            api_call_count += 1
            message = await _stream_assistant_response(current_context, config, signal, emit, stream_fn)
            new_messages.append(message)

            if message.stop_reason in ("error", "aborted"):
                await _emit_event(emit, TurnEndEvent(message=message, tool_results=[]))
                await _emit_event(emit, AgentEndEvent(messages=new_messages))
                return

            tool_calls = [c for c in message.content if getattr(c, "type", None) == "toolCall"]
            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False
            if tool_calls:
                batch = (
                    await _fail_tool_calls_from_truncated_message(tool_calls, emit)
                    if message.stop_reason == "length"
                    else await _execute_tool_calls(current_context, message, config, signal, emit)
                )
                tool_results.extend(batch.messages)
                has_more_tool_calls = not batch.terminate
                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            await _emit_event(emit, TurnEndEvent(message=message, tool_results=tool_results))

            if signal and signal.aborted:
                await _emit_event(emit, AgentEndEvent(messages=new_messages))
                return

            next_turn_ctx = ShouldStopAfterTurnContext(
                message=message, tool_results=tool_results, context=current_context, new_messages=new_messages
            )
            if config.prepare_next_turn:
                snapshot = await resolve(config.prepare_next_turn(next_turn_ctx))
                if snapshot:
                    current_context = snapshot.context or current_context
                    reasoning = config.reasoning
                    if snapshot.thinking_level is not None:
                        reasoning = None if snapshot.thinking_level == "off" else snapshot.thinking_level
                    config = replace(
                        config,
                        model=snapshot.model or config.model,
                        reasoning=reasoning,
                    )

            stop_turn_ctx = ShouldStopAfterTurnContext(
                message=message, tool_results=tool_results, context=current_context, new_messages=new_messages
            )
            if config.should_stop_after_turn and await resolve(config.should_stop_after_turn(stop_turn_ctx)):
                await _emit_event(emit, AgentEndEvent(messages=new_messages))
                return

            pending_messages = await _get_messages(config.get_steering_messages)

        follow_up = await _get_messages(config.get_follow_up_messages)
        if follow_up:
            pending_messages = follow_up
            continue
        break

    await _emit_event(emit, AgentEndEvent(messages=new_messages))


def _iteration_budget_exhausted(api_call_count: int, config: AgentLoopConfig) -> bool:
    max_iterations = max(1, int(config.max_iterations or 90))
    if api_call_count >= max_iterations:
        return True
    budget = config.iteration_budget
    return bool(budget is not None and getattr(budget, "remaining", 1) <= 0)


def _consume_iteration_budget(config: AgentLoopConfig) -> None:
    budget = config.iteration_budget
    if budget is not None:
        budget.consume()


async def _get_messages(callback: Callable | None) -> list[AgentMessage]:
    if callback is None:
        return []
    return list(await resolve(callback()) or [])


async def _handle_iteration_limit(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[Callable],
    api_call_count: int,
) -> None:
    max_iterations = max(1, int(config.max_iterations or 90))
    if config.on_iteration_limit is None:
        await _emit_event(emit, AgentEndEvent(messages=new_messages))
        return
    summary_request = await resolve(config.on_iteration_limit(
        IterationLimitContext(
            context=current_context,
            api_call_count=api_call_count,
            max_iterations=max_iterations,
            signal=signal,
        )
    ))
    if summary_request is None:
        await _emit_event(emit, AgentEndEvent(messages=new_messages))
        return
    await _emit_event(emit, MessageStartEvent(message=summary_request))
    await _emit_event(emit, MessageEndEvent(message=summary_request))
    current_context.messages.append(summary_request)
    new_messages.append(summary_request)

    summary_context = AgentContext(
        system_prompt=current_context.system_prompt,
        messages=current_context.messages,
        tools=[],
    )
    summary_config = replace(
        config,
        max_iterations=max_iterations,
        iteration_budget=None,
        on_iteration_limit=None,
    )
    message = await _stream_assistant_response(summary_context, summary_config, signal, emit, stream_fn)
    new_messages.append(message)
    await _emit_event(emit, TurnEndEvent(message=message, tool_results=[]))
    await _emit_event(emit, AgentEndEvent(messages=new_messages))


async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[Callable],
) -> AssistantMessage:
    messages = context.messages
    if config.transform_context:
        messages = await resolve(config.transform_context(messages, signal))

    llm_messages = await resolve(config.convert_to_llm(messages))
    llm_context = Context(system_prompt=context.system_prompt, messages=llm_messages, tools=_llm_tools(context.tools))

    stream_function = stream_fn or default_stream_simple
    resolved_api_key = await resolve(config.get_api_key(config.model.provider)) if config.get_api_key else None
    options = SimpleStreamOptions(
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        signal=signal,
        api_key=resolved_api_key or config.api_key,
        transport=config.transport,
        session_id=config.session_id,
        max_retry_delay_ms=config.max_retry_delay_ms,
        on_payload=config.on_payload,
        on_response=config.on_response,
        reasoning=config.reasoning,
        thinking_budgets=config.thinking_budgets,
    )
    response = await resolve(stream_function(config.model, llm_context, options))

    partial_added = False
    last_partial_snapshot: AssistantMessage | None = None
    async for event in _iter_response_events(response, signal):
        if signal and signal.aborted:
            await _close_response(response)
            return await _finalize_aborted_stream_response(
                context,
                config,
                emit,
                partial_added=partial_added,
                partial_snapshot=last_partial_snapshot,
            )
        if event.type == "start":
            last_partial_snapshot = copy.deepcopy(event.partial)
            context.messages.append(last_partial_snapshot)
            partial_added = True
            await _emit_event(emit, MessageStartEvent(message=copy.copy(last_partial_snapshot)))
        elif event.type in (
            "text_start", "text_delta", "text_end",
            "thinking_start", "thinking_delta", "thinking_end",
            "toolcall_start", "toolcall_delta", "toolcall_end",
        ):
            if partial_added:
                last_partial_snapshot = copy.deepcopy(event.partial)
                context.messages[-1] = last_partial_snapshot
                await _emit_event(
                    emit,
                    MessageUpdateEvent(
                        message=copy.copy(last_partial_snapshot), assistant_message_event=event
                    ),
                )
        elif event.type in ("done", "error"):
            final_message = await _response_result(response)
            if partial_added:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
                await _emit_event(emit, MessageStartEvent(message=copy.copy(final_message)))
            await _emit_event(emit, MessageEndEvent(message=final_message))
            return final_message
        if signal and signal.aborted:
            await _close_response(response)
            return await _finalize_aborted_stream_response(
                context,
                config,
                emit,
                partial_added=partial_added,
                partial_snapshot=last_partial_snapshot,
            )

    if signal and signal.aborted:
        await _close_response(response)
        return await _finalize_aborted_stream_response(
            context,
            config,
            emit,
            partial_added=partial_added,
            partial_snapshot=last_partial_snapshot,
        )

    final_message = await _response_result(response)
    if partial_added:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await _emit_event(emit, MessageStartEvent(message=copy.copy(final_message)))
    await _emit_event(emit, MessageEndEvent(message=final_message))
    return final_message


async def _iter_response_events(response: object, signal: Optional[AbortSignal]):
    iter_until = getattr(response, "iter_until", None)
    if callable(iter_until):
        iterator = iter(iter_until(lambda: bool(signal and signal.aborted)))
        while True:
            item = await asyncio.to_thread(_next_or_done, iterator)
            if item is _ITERATION_DONE:
                return
            yield item
        return
    async_iterator = getattr(response, "__aiter__", None)
    if callable(async_iterator):
        async for item in response:
            yield item
        return
    iterator = iter(response)
    while True:
        item = await asyncio.to_thread(_next_or_done, iterator)
        if item is _ITERATION_DONE:
            return
        yield item


def _next_or_done(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return _ITERATION_DONE


async def _response_result(response: object) -> AssistantMessage:
    result = getattr(response, "result", None)
    if callable(result):
        return await resolve(result())
    result_sync = getattr(response, "result_sync")
    return await asyncio.to_thread(result_sync)


async def _finalize_aborted_stream_response(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    *,
    partial_added: bool,
    partial_snapshot: AssistantMessage | None,
) -> AssistantMessage:
    if partial_added and partial_snapshot is not None:
        final_message = replace(
            partial_snapshot,
            stop_reason="aborted",
            error_message="Operation aborted",
            timestamp=now_ms(),
        )
        context.messages[-1] = final_message
    else:
        final_message = AssistantMessage(
            content=[TextContent(text="")],
            api=config.model.api,
            provider=config.model.provider,
            model=config.model.id,
            usage=empty_usage(),
            stop_reason="aborted",
            error_message="Operation aborted",
            timestamp=now_ms(),
        )
        context.messages.append(final_message)
        await _emit_event(emit, MessageStartEvent(message=copy.copy(final_message)))
    await _emit_event(emit, MessageEndEvent(message=final_message))
    return final_message


async def _close_response(response: object) -> None:
    aclose = getattr(response, "aclose", None)
    if callable(aclose):
        try:
            await resolve(aclose())
        except Exception:
            pass
        return
    close = getattr(response, "close", None)
    if not callable(close):
        return
    try:
        await resolve(close())
    except Exception:
        return


class _ExecutedBatch:
    def __init__(self, messages: list[ToolResultMessage], terminate: bool) -> None:
        self.messages = messages
        self.terminate = terminate


async def _fail_tool_calls_from_truncated_message(
    tool_calls: list[Any], emit: AgentEventSink
) -> _ExecutedBatch:
    messages: list[ToolResultMessage] = []
    for tool_call in tool_calls:
        await _emit_event(
            emit,
            ToolExecutionStartEvent(tool_call_id=tool_call.id, tool_name=tool_call.name, args=tool_call.arguments),
        )
        finalized = {
            "tool_call": tool_call,
            "result": _error_result(
                f'Tool call "{tool_call.name}" was not executed: the response hit the output token limit, '
                "so its arguments may be truncated. Re-issue the tool call with complete arguments."
            ),
            "is_error": True,
        }
        await _emit_tool_end(finalized, emit)
        message = _tool_result_message(finalized)
        await _emit_tool_result_message(message, emit)
        messages.append(message)
    return _ExecutedBatch(messages, False)


async def _execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
) -> _ExecutedBatch:
    tool_calls = [c for c in assistant_message.content if getattr(c, "type", None) == "toolCall"]
    tools = current_context.tools or []
    max_parallel_tools = 1 if config.tool_execution == "sequential" else config.max_parallel_tools
    async with ToolCoordinator(max_parallel_tools) as coordinator:
        if config.tool_execution == "sequential":
            return await _execute_sequential(
                current_context, assistant_message, tool_calls, config, signal, emit, coordinator
            )
        tool_by_name = {tool.name: tool for tool in tools}
        has_sequential_tool_call = any(
            tool_by_name.get(tool_call.name) is not None
            and tool_by_name[tool_call.name].execution_mode == "sequential"
            for tool_call in tool_calls
        )
        if has_sequential_tool_call:
            return await _execute_sequential(
                current_context, assistant_message, tool_calls, config, signal, emit, coordinator
            )
        return await _execute_parallel(
            current_context, assistant_message, tool_calls, config, signal, emit, coordinator
        )


async def _execute_sequential(
    current_context, assistant_message, tool_calls, config, signal, emit, coordinator
) -> _ExecutedBatch:
    finalized_calls: list[dict] = []
    messages: list[ToolResultMessage] = []
    for tool_call in tool_calls:
        await _emit_event(
            emit,
            ToolExecutionStartEvent(tool_call_id=tool_call.id, tool_name=tool_call.name, args=tool_call.arguments),
        )
        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )
        if isinstance(preparation, ImmediateToolOutcome):
            finalized = _finalize_immediate(preparation)
        else:
            executed = await _execute_prepared(preparation, signal, emit, coordinator)
            finalized = await _finalize(
                current_context, assistant_message, preparation, executed, config, signal
            )
        await _emit_tool_end(finalized, emit)
        message = _tool_result_message(finalized)
        await _emit_tool_result_message(message, emit)
        finalized_calls.append(finalized)
        messages.append(message)
        if signal and signal.aborted:
            break
    return _ExecutedBatch(messages, _should_terminate(finalized_calls))


async def _execute_parallel(
    current_context, assistant_message, tool_calls, config, signal, emit, coordinator
) -> _ExecutedBatch:
    entries: list = []  # either a finalized dict or an awaitable task
    for tool_call in tool_calls:
        await _emit_event(
            emit,
            ToolExecutionStartEvent(tool_call_id=tool_call.id, tool_name=tool_call.name, args=tool_call.arguments),
        )
        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )
        if isinstance(preparation, ImmediateToolOutcome):
            finalized = _finalize_immediate(preparation)
            await _emit_tool_end(finalized, emit)
            entries.append(finalized)
            if signal and signal.aborted:
                break
            continue

        entries.append(
            asyncio.create_task(
                _execute_and_finalize(
                    current_context,
                    assistant_message,
                    preparation,
                    config,
                    signal,
                    emit,
                    coordinator,
                )
            )
        )
        if signal and signal.aborted:
            break

    ordered: list[dict] = []
    for entry in entries:
        finalized = await entry if isinstance(entry, asyncio.Task) else entry
        ordered.append(finalized)

    messages: list[ToolResultMessage] = []
    for finalized in ordered:
        message = _tool_result_message(finalized)
        await _emit_tool_result_message(message, emit)
        messages.append(message)
    return _ExecutedBatch(messages, _should_terminate(ordered))


def _should_terminate(finalized_calls: list[dict]) -> bool:
    return len(finalized_calls) > 0 and all(f["result"].terminate is True for f in finalized_calls)


async def _execute_and_finalize(
    current_context, assistant_message, preparation, config, signal, emit, coordinator
) -> dict:
    executed = await _execute_prepared(preparation, signal, emit, coordinator)
    finalized = await _finalize(
        current_context, assistant_message, preparation, executed, config, signal
    )
    await _emit_tool_end(finalized, emit)
    return finalized


async def _prepare_tool_call(
    current_context, assistant_message, tool_call, config, signal
) -> PreparedToolCall | ImmediateToolOutcome:
    tool = next((t for t in (current_context.tools or []) if t.name == tool_call.name), None)
    if tool is None:
        return ImmediateToolOutcome(
            tool_call=tool_call,
            result=_error_result(_unknown_tool_message(tool_call.name, current_context.tools or [])),
            is_error=True,
            reason_code="unknown_tool",
        )
    prepared_call = tool_call
    try:
        prepared_call = await _prepare_arguments(tool, tool_call)
        validated_args = validate_tool_arguments(tool, prepared_call)
        if config.before_tool_call:
            before = await resolve(
                config.before_tool_call(
                    _before_ctx(assistant_message, tool_call, validated_args, current_context), signal
                )
            )
            if signal and signal.aborted:
                return ImmediateToolOutcome(
                    tool_call=prepared_call,
                    result=_error_result("Operation aborted"),
                    is_error=True,
                    reason_code="aborted",
                )
            if before and before.block:
                return ImmediateToolOutcome(
                    tool_call=prepared_call,
                    result=_error_result(before.reason or "Tool execution was blocked"),
                    is_error=True,
                    reason_code="before_hook_block",
                )
        if signal and signal.aborted:
            return ImmediateToolOutcome(
                tool_call=prepared_call,
                result=_error_result("Operation aborted"),
                is_error=True,
                reason_code="aborted",
            )
        return PreparedToolCall(tool_call=prepared_call, tool=tool, args=validated_args)
    except Exception as error:  # noqa: BLE001
        return ImmediateToolOutcome(
            tool_call=prepared_call,
            result=_error_result(str(error)),
            is_error=True,
            reason_code="invalid_arguments",
        )


async def _prepare_arguments(tool: AgentTool, tool_call):
    if not tool.prepare_arguments:
        return tool_call
    prepared = await resolve(tool.prepare_arguments(tool_call.arguments))
    if prepared is tool_call.arguments:
        return tool_call
    clone = copy.copy(tool_call)
    clone.arguments = prepared
    return clone


async def _execute_prepared(
    prepared: PreparedToolCall,
    signal,
    emit: AgentEventSink,
    coordinator: ToolCoordinator,
) -> dict:
    tool_call = prepared.tool_call
    accepting = {"value": True}
    update_tasks: list[Any] = []
    owner_loop = asyncio.get_running_loop()

    def on_update(partial_result: AgentToolResult) -> None:
        if not accepting["value"]:
            return
        event = ToolExecutionUpdateEvent(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=tool_call.arguments,
            partial_result=partial_result,
        )
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is owner_loop:
            update_tasks.append(owner_loop.create_task(_emit_event(emit, event)))
        else:
            future = asyncio.run_coroutine_threadsafe(_emit_event(emit, event), owner_loop)
            future.result()

    try:
        execute = prepared.tool.execute
        result = await coordinator.execute(
            execute, tool_call.id, prepared.args, signal, on_update
        )
        accepting["value"] = False
        await _settle_update_tasks(update_tasks)
        return {"result": result, "is_error": False}
    except Exception as error:  # noqa: BLE001
        accepting["value"] = False
        await _settle_update_tasks(update_tasks)
        return {"result": _error_result(str(error)), "is_error": True}


async def _settle_update_tasks(tasks: list[Any]) -> None:
    for task in tasks:
        if isinstance(task, asyncio.Future):
            await task
        else:
            await asyncio.wrap_future(task)


async def _emit_event(emit: AgentEventSink, event: AgentEvent) -> None:
    result = emit(event)
    if isinstance(result, ConcurrentFuture):
        await asyncio.wrap_future(result)
        return
    if inspect.isawaitable(result):
        await result
        return
    wait = getattr(result, "wait", None)
    if callable(wait):
        await asyncio.to_thread(wait)


async def _finalize(current_context, assistant_message, prepared, executed, config, signal) -> dict:
    result = executed["result"]
    is_error = executed["is_error"]
    if config.after_tool_call:
        try:
            after = await resolve(
                config.after_tool_call(
                    _after_ctx(
                        assistant_message,
                        prepared.tool_call,
                        prepared.args,
                        result,
                        is_error,
                        current_context,
                    ),
                    signal,
                )
            )
            if after:
                result = AgentToolResult(
                    content=after.content if after.content is not None else result.content,
                    details=after.details if after.details is not None else result.details,
                    terminate=after.terminate if after.terminate is not None else result.terminate,
                )
                if after.is_error is not None:
                    is_error = after.is_error
        except Exception as error:  # noqa: BLE001
            result = _error_result(str(error))
            is_error = True
    return {
        "tool_call": prepared.tool_call,
        "args": prepared.args,
        "result": result,
        "is_error": is_error,
    }


def _finalize_immediate(outcome: ImmediateToolOutcome) -> dict:
    return {
        "tool_call": outcome.tool_call,
        "args": getattr(outcome.tool_call, "arguments", {}),
        "result": outcome.result,
        "is_error": outcome.is_error,
        "reason_code": outcome.reason_code,
    }


def _error_result(message: str) -> AgentToolResult:
    from travis.ai.types import TextContent

    return AgentToolResult(content=[TextContent(text=message)], details={})


def _unknown_tool_message(tool_name: str, tools: list[AgentTool]) -> str:
    message = f"Tool {tool_name} not found"
    available: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        available.append(tool.name)
    if available:
        message += f". Available tools: {', '.join(available)}"
    if tool_name == "glob":
        message += ". Use find or ls for file discovery; glob is not available in this tool catalog"
    return message


async def _emit_tool_end(finalized: dict, emit: AgentEventSink) -> None:
    await _emit_event(
        emit,
        ToolExecutionEndEvent(
            tool_call_id=finalized["tool_call"].id,
            tool_name=finalized["tool_call"].name,
            result=finalized["result"],
            is_error=finalized["is_error"],
            args=finalized.get("args", getattr(finalized["tool_call"], "arguments", {})),
            reason_code=finalized.get("reason_code"),
        ),
    )


def _tool_result_message(finalized: dict) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=finalized["tool_call"].id,
        tool_name=finalized["tool_call"].name,
        content=finalized["result"].content,
        details=finalized["result"].details,
        is_error=finalized["is_error"],
        timestamp=now_ms(),
    )


async def _emit_tool_result_message(message: ToolResultMessage, emit: AgentEventSink) -> None:
    await _emit_event(emit, MessageStartEvent(message=message))
    await _emit_event(emit, MessageEndEvent(message=message))


def _role_of(message) -> str:
    return getattr(message, "role", "")


def _llm_tools(tools: Optional[list[AgentTool]]):
    if not tools:
        return None
    from travis.ai.types import Tool

    return [Tool(name=t.name, description=t.description, parameters=t.parameters) for t in tools]


def _before_ctx(assistant_message, tool_call, args, context):
    from travis.agent.types import BeforeToolCallContext

    return BeforeToolCallContext(assistant_message=assistant_message, tool_call=tool_call, args=args, context=context)


def _after_ctx(assistant_message, tool_call, args, result, is_error, context):
    from travis.agent.types import AfterToolCallContext

    return AfterToolCallContext(
        assistant_message=assistant_message, tool_call=tool_call, args=args, result=result, is_error=is_error, context=context
    )
