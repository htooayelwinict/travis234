"""Agent loop over AgentMessage. Port of pi/packages/agent/src/agent-loop.ts.

Synchronous Python port: `emit` is a sync sink; the assistant event stream is
iterated synchronously; `agent_loop` runs the loop in a worker thread.
"""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, Callable, Optional, Union

from appv23.ai.event_stream import EventStream
from appv23.ai.stream import stream_simple as default_stream_simple
from appv23.ai.types import (
    AssistantMessage,
    Context,
    SimpleStreamOptions,
    TextContent,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)
from appv23.ai.validation import validate_tool_arguments
from appv23.agent.types import (
    AbortSignal,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentStartEvent,
    AgentTool,
    AgentToolResult,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ShouldStopAfterTurnContext,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from appv23.agent.tool_dispatch import should_parallelize_tool_batch

AgentEventSink = Callable[[AgentEvent], Any]


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
        except Exception:  # pragma: no cover - loop must not crash the thread silently
            stream.push(AgentEndEvent(messages=list(prompts)))

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
        run_agent_loop_continue(context, config, stream.push, signal, stream_fn)

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
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=context.tools,
    )

    _emit_event(emit, AgentStartEvent())
    _emit_event(emit, TurnStartEvent())
    for prompt in prompts:
        _emit_event(emit, MessageStartEvent(message=prompt))
        _emit_event(emit, MessageEndEvent(message=prompt))

    _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


def run_agent_loop_continue(
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

    _emit_event(emit, AgentStartEvent())
    _emit_event(emit, TurnStartEvent())

    _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


def _run_loop(
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
    pending_messages: list[AgentMessage] = list(config.get_steering_messages() or []) if config.get_steering_messages else []

    while True:
        has_more_tool_calls = True

        while has_more_tool_calls or pending_messages:
            if not first_turn:
                _emit_event(emit, TurnStartEvent())
            else:
                first_turn = False

            if pending_messages:
                for message in pending_messages:
                    _emit_event(emit, MessageStartEvent(message=message))
                    _emit_event(emit, MessageEndEvent(message=message))
                    current_context.messages.append(message)
                    new_messages.append(message)
                pending_messages = []

            if _iteration_budget_exhausted(api_call_count, config):
                _request_iteration_summary(current_context, new_messages, config, signal, emit, stream_fn, api_call_count)
                return

            _consume_iteration_budget(config)
            api_call_count += 1
            message = _stream_assistant_response(current_context, config, signal, emit, stream_fn)
            new_messages.append(message)

            if message.stop_reason in ("error", "aborted"):
                _emit_event(emit, TurnEndEvent(message=message, tool_results=[]))
                _emit_event(emit, AgentEndEvent(messages=new_messages))
                return

            tool_calls = [c for c in message.content if getattr(c, "type", None) == "toolCall"]
            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False
            if tool_calls:
                batch = _execute_tool_calls(current_context, message, config, signal, emit)
                tool_results.extend(batch.messages)
                has_more_tool_calls = not batch.terminate
                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            _emit_event(emit, TurnEndEvent(message=message, tool_results=tool_results))

            if signal and signal.aborted:
                _emit_event(emit, AgentEndEvent(messages=new_messages))
                return

            next_turn_ctx = ShouldStopAfterTurnContext(
                message=message, tool_results=tool_results, context=current_context, new_messages=new_messages
            )
            if config.prepare_next_turn:
                snapshot = config.prepare_next_turn(next_turn_ctx)
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
            if config.should_stop_after_turn and config.should_stop_after_turn(stop_turn_ctx):
                _emit_event(emit, AgentEndEvent(messages=new_messages))
                return

            pending_messages = list(config.get_steering_messages() or []) if config.get_steering_messages else []

        follow_up = list(config.get_follow_up_messages() or []) if config.get_follow_up_messages else []
        if follow_up:
            pending_messages = follow_up
            continue
        break

    _emit_event(emit, AgentEndEvent(messages=new_messages))


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


def _request_iteration_summary(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[Callable],
    api_call_count: int,
) -> None:
    max_iterations = max(1, int(config.max_iterations or 90))
    summary_request = UserMessage(
        content=(
            "You've reached the maximum number of tool-calling iterations allowed. "
            "Please provide a final response summarizing what you've found and accomplished so far, "
            "without calling any more tools."
        ),
        timestamp=now_ms(),
    )
    _emit_event(emit, MessageStartEvent(message=summary_request))
    _emit_event(emit, MessageEndEvent(message=summary_request))
    current_context.messages.append(summary_request)
    new_messages.append(summary_request)

    summary_context = AgentContext(
        system_prompt=current_context.system_prompt,
        messages=current_context.messages,
        tools=[],
    )
    summary_config = replace(config, max_iterations=max_iterations, iteration_budget=None)
    message = _stream_assistant_response(summary_context, summary_config, signal, emit, stream_fn)
    new_messages.append(message)
    _emit_event(emit, TurnEndEvent(message=message, tool_results=[]))
    _emit_event(emit, AgentEndEvent(messages=new_messages))


def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[Callable],
) -> AssistantMessage:
    messages = context.messages
    if config.transform_context:
        messages = config.transform_context(messages, signal)

    llm_messages = config.convert_to_llm(messages)
    llm_context = Context(system_prompt=context.system_prompt, messages=llm_messages, tools=_llm_tools(context.tools))

    stream_function = stream_fn or default_stream_simple
    resolved_api_key = config.get_api_key(config.model.provider) if config.get_api_key else None
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
    response = stream_function(config.model, llm_context, options)

    partial_added = False
    last_partial_snapshot: AssistantMessage | None = None
    for event in _iter_response_events(response, signal):
        if signal and signal.aborted:
            _close_response(response)
            return _finalize_aborted_stream_response(
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
            _emit_event(emit, MessageStartEvent(message=copy.copy(last_partial_snapshot)))
        elif event.type in (
            "text_start", "text_delta", "text_end",
            "thinking_start", "thinking_delta", "thinking_end",
            "toolcall_start", "toolcall_delta", "toolcall_end",
        ):
            if partial_added:
                last_partial_snapshot = copy.deepcopy(event.partial)
                context.messages[-1] = last_partial_snapshot
                _emit_event(emit, MessageUpdateEvent(message=copy.copy(last_partial_snapshot), assistant_message_event=event))
        elif event.type in ("done", "error"):
            final_message = response.result_sync()
            _deduplicate_tool_calls(final_message)
            if partial_added:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
                _emit_event(emit, MessageStartEvent(message=copy.copy(final_message)))
            _emit_event(emit, MessageEndEvent(message=final_message))
            return final_message
        if signal and signal.aborted:
            _close_response(response)
            return _finalize_aborted_stream_response(
                context,
                config,
                emit,
                partial_added=partial_added,
                partial_snapshot=last_partial_snapshot,
            )

    if signal and signal.aborted:
        _close_response(response)
        return _finalize_aborted_stream_response(
            context,
            config,
            emit,
            partial_added=partial_added,
            partial_snapshot=last_partial_snapshot,
        )

    final_message = response.result_sync()
    _deduplicate_tool_calls(final_message)
    if partial_added:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        _emit_event(emit, MessageStartEvent(message=copy.copy(final_message)))
    _emit_event(emit, MessageEndEvent(message=final_message))
    return final_message


def _iter_response_events(response: object, signal: Optional[AbortSignal]):
    iter_until = getattr(response, "iter_until", None)
    if callable(iter_until):
        return iter_until(lambda: bool(signal and signal.aborted))
    return iter(response)


def _finalize_aborted_stream_response(
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
        _emit_event(emit, MessageStartEvent(message=copy.copy(final_message)))
    _emit_event(emit, MessageEndEvent(message=final_message))
    return final_message


def _close_response(response: object) -> None:
    close = getattr(response, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return


def _deduplicate_tool_calls(message: AssistantMessage) -> None:
    content = getattr(message, "content", None)
    if not content:
        return
    seen: set[tuple[str, str]] = set()
    next_content = []
    changed = False
    for item in content:
        if getattr(item, "type", None) != "toolCall":
            next_content.append(item)
            continue
        key = (getattr(item, "name", ""), _canonical_tool_call_arguments(getattr(item, "arguments", None)))
        if key in seen:
            changed = True
            continue
        seen.add(key)
        next_content.append(item)
    if changed:
        message.content = next_content


def _canonical_tool_call_arguments(arguments: Any) -> str:
    try:
        return json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(arguments)


def _is_guardrail_block_result(reason: str | None) -> bool:
    if not reason:
        return False
    try:
        parsed = json.loads(reason)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and isinstance(parsed.get("guardrail"), dict)


class _ExecutedBatch:
    def __init__(self, messages: list[ToolResultMessage], terminate: bool) -> None:
        self.messages = messages
        self.terminate = terminate


def _execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
) -> _ExecutedBatch:
    tool_calls = [c for c in assistant_message.content if getattr(c, "type", None) == "toolCall"]
    tools = current_context.tools or []
    if config.tool_execution != "sequential" and should_parallelize_tool_batch(tool_calls, tools):
        return _execute_parallel(current_context, assistant_message, tool_calls, config, signal, emit)
    return _execute_sequential(current_context, assistant_message, tool_calls, config, signal, emit)


def _execute_sequential(current_context, assistant_message, tool_calls, config, signal, emit) -> _ExecutedBatch:
    finalized_calls: list[dict] = []
    messages: list[ToolResultMessage] = []
    for tool_call in tool_calls:
        _emit_event(
            emit,
            ToolExecutionStartEvent(tool_call_id=tool_call.id, tool_name=tool_call.name, args=tool_call.arguments),
        )
        preparation = _prepare_tool_call(current_context, assistant_message, tool_call, config, signal)
        if preparation["kind"] == "immediate":
            finalized = _finalize_immediate(current_context, assistant_message, tool_call, preparation, config, signal)
        else:
            executed = _execute_prepared(preparation, signal, emit)
            finalized = _finalize(current_context, assistant_message, preparation, executed, config, signal)
        _emit_tool_end(finalized, emit)
        message = _tool_result_message(finalized)
        _emit_tool_result_message(message, emit)
        finalized_calls.append(finalized)
        messages.append(message)
        if signal and signal.aborted:
            break
    return _ExecutedBatch(messages, _should_terminate(finalized_calls))


def _execute_parallel(current_context, assistant_message, tool_calls, config, signal, emit) -> _ExecutedBatch:
    entries: list = []  # either finalized dict or a callable returning finalized dict
    for tool_call in tool_calls:
        _emit_event(
            emit,
            ToolExecutionStartEvent(tool_call_id=tool_call.id, tool_name=tool_call.name, args=tool_call.arguments),
        )
        preparation = _prepare_tool_call(current_context, assistant_message, tool_call, config, signal)
        if preparation["kind"] == "immediate":
            finalized = _finalize_immediate(current_context, assistant_message, tool_call, preparation, config, signal)
            _emit_tool_end(finalized, emit)
            entries.append(finalized)
            if signal and signal.aborted:
                break
            continue

        def _make(prep):
            def _job():
                executed = _execute_prepared(prep, signal, emit)
                finalized = _finalize(current_context, assistant_message, prep, executed, config, signal)
                _emit_tool_end(finalized, emit)
                return finalized
            return _job

        entries.append(_make(preparation))
        if signal and signal.aborted:
            break

    ordered: list[dict] = []
    callables = [(i, e) for i, e in enumerate(entries) if callable(e)]
    results_by_index: dict[int, dict] = {}
    if callables:
        with ThreadPoolExecutor(max_workers=max(1, len(callables))) as pool:
            futures = {pool.submit(job): i for i, job in callables}
            for future in futures:
                idx = futures[future]
                results_by_index[idx] = future.result()
    for i, entry in enumerate(entries):
        ordered.append(results_by_index[i] if callable(entry) else entry)

    messages: list[ToolResultMessage] = []
    for finalized in ordered:
        message = _tool_result_message(finalized)
        _emit_tool_result_message(message, emit)
        messages.append(message)
    return _ExecutedBatch(messages, _should_terminate(ordered))


def _should_terminate(finalized_calls: list[dict]) -> bool:
    return len(finalized_calls) > 0 and all(f["result"].terminate is True for f in finalized_calls)


def _prepare_tool_call(current_context, assistant_message, tool_call, config, signal) -> dict:
    tool = next((t for t in (current_context.tools or []) if t.name == tool_call.name), None)
    if tool is None:
        return {
            "kind": "immediate",
            "tool_call": tool_call,
            "args": getattr(tool_call, "arguments", {}),
            "result": _error_result(f"Tool {tool_call.name} not found"),
            "is_error": True,
            "apply_after_tool_call": True,
        }
    prepared_call = tool_call
    try:
        prepared_call = _prepare_arguments(tool, tool_call)
        validated_args = validate_tool_arguments(tool, prepared_call)
        if config.before_tool_call:
            before = config.before_tool_call(
                _before_ctx(assistant_message, tool_call, validated_args, current_context), signal
            )
            if signal and signal.aborted:
                return {
                    "kind": "immediate",
                    "result": _error_result("Operation aborted"),
                    "is_error": True,
                    "apply_after_tool_call": False,
                }
            if before and before.block:
                return {
                    "kind": "immediate",
                    "tool_call": prepared_call,
                    "args": validated_args,
                    "result": _error_result(before.reason or "Tool execution was blocked"),
                    "is_error": True,
                    "apply_after_tool_call": not _is_guardrail_block_result(before.reason),
                }
        if signal and signal.aborted:
            return {
                "kind": "immediate",
                "result": _error_result("Operation aborted"),
                "is_error": True,
                "apply_after_tool_call": False,
            }
        return {"kind": "prepared", "tool_call": prepared_call, "tool": tool, "args": validated_args}
    except Exception as error:  # noqa: BLE001
        return {
            "kind": "immediate",
            "tool_call": prepared_call,
            "args": getattr(prepared_call, "arguments", getattr(tool_call, "arguments", {})),
            "result": _error_result(str(error)),
            "is_error": True,
            "apply_after_tool_call": True,
        }


def _prepare_arguments(tool: AgentTool, tool_call):
    if not tool.prepare_arguments:
        return tool_call
    prepared = tool.prepare_arguments(tool_call.arguments)
    if prepared is tool_call.arguments:
        return tool_call
    clone = copy.copy(tool_call)
    clone.arguments = prepared
    return clone


def _execute_prepared(prepared: dict, signal, emit: AgentEventSink) -> dict:
    tool_call = prepared["tool_call"]
    accepting = {"value": True}
    update_events: list[Any] = []

    def on_update(partial_result: AgentToolResult) -> None:
        if not accepting["value"]:
            return
        update_events.append(
            emit(ToolExecutionUpdateEvent(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                args=tool_call.arguments,
                partial_result=partial_result,
            ))
        )

    try:
        result = prepared["tool"].execute(tool_call.id, prepared["args"], signal, on_update)
        accepting["value"] = False
        _settle_emit_results(update_events)
        return {"result": result, "is_error": False}
    except Exception as error:  # noqa: BLE001
        accepting["value"] = False
        _settle_emit_results(update_events)
        return {"result": _error_result(str(error)), "is_error": True}


def _emit_event(emit: AgentEventSink, event: AgentEvent) -> None:
    _settle_emit_result(emit(event))


def _settle_emit_result(result: Any) -> None:
    if result is None:
        return
    if hasattr(result, "result") and callable(result.result):
        result.result()
    elif hasattr(result, "wait") and callable(result.wait):
        result.wait()


def _settle_emit_results(results: list[Any]) -> None:
    for result in results:
        _settle_emit_result(result)


def _finalize(current_context, assistant_message, prepared, executed, config, signal) -> dict:
    result = executed["result"]
    is_error = executed["is_error"]
    if config.after_tool_call:
        try:
            after = config.after_tool_call(
                _after_ctx(assistant_message, prepared["tool_call"], prepared["args"], result, is_error, current_context),
                signal,
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
    return {"tool_call": prepared["tool_call"], "result": result, "is_error": is_error}


def _finalize_immediate(current_context, assistant_message, tool_call, preparation, config, signal) -> dict:
    finalized = {
        "tool_call": preparation.get("tool_call", tool_call),
        "result": preparation["result"],
        "is_error": preparation["is_error"],
    }
    if not preparation.get("apply_after_tool_call"):
        return finalized
    prepared = {
        "tool_call": finalized["tool_call"],
        "args": preparation.get("args", getattr(finalized["tool_call"], "arguments", {})),
    }
    executed = {"result": finalized["result"], "is_error": finalized["is_error"]}
    return _finalize(current_context, assistant_message, prepared, executed, config, signal)


def _error_result(message: str) -> AgentToolResult:
    from appv23.ai.types import TextContent

    return AgentToolResult(content=[TextContent(text=message)], details={})


def _emit_tool_end(finalized: dict, emit: AgentEventSink) -> None:
    _emit_event(
        emit,
        ToolExecutionEndEvent(
            tool_call_id=finalized["tool_call"].id,
            tool_name=finalized["tool_call"].name,
            result=finalized["result"],
            is_error=finalized["is_error"],
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


def _emit_tool_result_message(message: ToolResultMessage, emit: AgentEventSink) -> None:
    _emit_event(emit, MessageStartEvent(message=message))
    _emit_event(emit, MessageEndEvent(message=message))


def _role_of(message) -> str:
    return getattr(message, "role", "")


def _llm_tools(tools: Optional[list[AgentTool]]):
    if not tools:
        return None
    from appv23.ai.types import Tool

    return [Tool(name=t.name, description=t.description, parameters=t.parameters) for t in tools]


def _before_ctx(assistant_message, tool_call, args, context):
    from appv23.agent.types import BeforeToolCallContext

    return BeforeToolCallContext(assistant_message=assistant_message, tool_call=tool_call, args=args, context=context)


def _after_ctx(assistant_message, tool_call, args, result, is_error, context):
    from appv23.agent.types import AfterToolCallContext

    return AfterToolCallContext(
        assistant_message=assistant_message, tool_call=tool_call, args=args, result=result, is_error=is_error, context=context
    )
