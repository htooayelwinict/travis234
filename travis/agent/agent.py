"""Stateful Agent wrapper."""

from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from travis.ai.types import AssistantMessage, ImageContent, Message, Model, TextContent, UserMessage, empty_usage, now_ms
from travis.agent.agent_loop import (
    AgentEventSink,
    run_agent_loop_async,
    run_agent_loop_continue_async,
)
from travis.agent.async_utils import resolve, run_sync
from travis.agent.iteration_budget import IterationBudget
from travis.agent.run_lease import RunLease, RunLeaseToken
from travis.agent.types import (
    AbortSignal,
    AgentEndEvent,
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    MessageEndEvent,
    MessageStartEvent,
    QueueMode,
    ThinkingLevel,
    TurnEndEvent,
)

Listener = Callable[..., None]


class PendingMessageQueue:
    def __init__(self, mode: QueueMode = "one-at-a-time") -> None:
        self.messages: list[AgentMessage] = []
        self.mode = mode

    def enqueue(self, message: AgentMessage) -> None:
        self.messages.append(message)

    def has_items(self) -> bool:
        return bool(self.messages)

    def drain(self) -> list[AgentMessage]:
        if self.mode == "all":
            drained = list(self.messages)
            self.messages = []
            return drained
        if not self.messages:
            return []
        first = self.messages[0]
        self.messages = self.messages[1:]
        return [first]

    def clear(self) -> None:
        self.messages = []


@dataclass
class AgentState:
    system_prompt: str
    model: Model
    thinking_level: ThinkingLevel = "off"
    tools: list[AgentTool] = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    is_streaming: bool = False
    streaming_message: AgentMessage | None = None
    pending_tool_calls: set[str] = field(default_factory=set)
    error_message: str | None = None


class Agent:
    """Owns conversation state and drives the functional agent loop."""

    def __init__(
        self,
        *,
        system_prompt: str,
        model: Model,
        convert_to_llm: Callable[[list[AgentMessage]], list[Message]],
        tools: Optional[list[AgentTool]] = None,
        thinking_level: ThinkingLevel = "off",
        tool_execution: str = "parallel",
        max_parallel_tools: int = 8,
        before_tool_call=None,
        after_tool_call=None,
        should_stop_after_turn=None,
        prepare_next_turn=None,
        prepare_next_turn_with_context=None,
        transform_context=None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
        session_id: str | None = None,
        thinking_budgets: dict[str, int] | None = None,
        transport: str = "auto",
        max_retry_delay_ms: int | None = None,
        on_payload=None,
        on_response=None,
        max_iterations: int = 90,
        on_iteration_limit=None,
    ) -> None:
        self._state = AgentState(
            system_prompt=system_prompt,
            model=model,
            thinking_level=thinking_level,
            tools=list(tools or []),
        )
        self._convert_to_llm = convert_to_llm
        self._tool_execution = tool_execution
        self._max_parallel_tools = max(1, int(max_parallel_tools))
        self._before_tool_call = before_tool_call
        self._after_tool_call = after_tool_call
        self._should_stop_after_turn = should_stop_after_turn
        self._prepare_next_turn = prepare_next_turn
        self._prepare_next_turn_with_context = prepare_next_turn_with_context
        self._transform_context = transform_context
        self.session_id = session_id
        self.thinking_budgets = thinking_budgets
        self.transport = transport
        self.max_retry_delay_ms = max_retry_delay_ms
        self.on_payload = on_payload
        self.on_response = on_response
        self.max_iterations = max(1, int(max_iterations))
        self._on_iteration_limit = on_iteration_limit
        self._listeners: list[Listener] = []
        self._signal = AbortSignal()
        self._run_state_lock = threading.Lock()
        self._run_lease = RunLease()
        self._active_run_token: RunLeaseToken | None = None
        self._idle_event = threading.Event()
        self._idle_event.set()
        self._steering = PendingMessageQueue(steering_mode)
        self._follow_up = PendingMessageQueue(follow_up_mode)

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def signal(self) -> AbortSignal:
        return self._signal

    @property
    def run_lease(self) -> RunLease:
        return self._run_lease

    @property
    def steering_mode(self) -> QueueMode:
        return self._steering.mode

    @steering_mode.setter
    def steering_mode(self, mode: QueueMode) -> None:
        self._steering.mode = mode

    @property
    def follow_up_mode(self) -> QueueMode:
        return self._follow_up.mode

    @follow_up_mode.setter
    def follow_up_mode(self, mode: QueueMode) -> None:
        self._follow_up.mode = mode

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    def steer(self, message: AgentMessage) -> None:
        self._steering.enqueue(message)

    def follow_up(self, message: AgentMessage) -> None:
        self._follow_up.enqueue(message)

    def clear_steering_queue(self) -> None:
        self._steering.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up.clear()

    def clear_all_queues(self) -> None:
        self.clear_steering_queue()
        self.clear_follow_up_queue()

    def has_queued_messages(self) -> bool:
        return self._steering.has_items() or self._follow_up.has_items()

    def abort(self) -> None:
        self._signal.abort()

    def reset_abort_signal(self) -> AbortSignal:
        if self._state.is_streaming:
            return self._signal
        if self._signal.aborted:
            self._signal = AbortSignal()
        return self._signal


    def wait_for_idle(self, timeout: float | None = None) -> bool:
        return self._idle_event.wait(timeout)

    def reset(self) -> None:
        if self._run_lease.active:
            raise RuntimeError("Cannot reset Agent while an active run is in progress; abort and wait for idle first")
        self._state.messages = []
        self._state.is_streaming = False
        self._state.error_message = None
        self._state.streaming_message = None
        self._state.pending_tool_calls = set()
        self.clear_all_queues()

    def _build_config(self, *, skip_initial_steering_poll: bool = False) -> AgentLoopConfig:
        skip_steering_poll = {"value": skip_initial_steering_poll}

        def get_steering_messages() -> list[AgentMessage]:
            if skip_steering_poll["value"]:
                skip_steering_poll["value"] = False
                return []
            return self._drain_steering()

        def prepare_next_turn_adapter(context):
            if self._prepare_next_turn_with_context:
                return self._prepare_next_turn_with_context(context, self._signal)
            if self._prepare_next_turn:
                return self._prepare_next_turn(self._signal)
            return None

        return AgentLoopConfig(
            model=self._state.model,
            convert_to_llm=self._convert_to_llm,
            get_steering_messages=get_steering_messages,
            get_follow_up_messages=self._drain_follow_up,
            prepare_next_turn=prepare_next_turn_adapter
            if self._prepare_next_turn_with_context or self._prepare_next_turn
            else None,
            tool_execution=self._tool_execution,
            max_parallel_tools=self._max_parallel_tools,
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
            should_stop_after_turn=self._should_stop_after_turn,
            transform_context=self._transform_context,
            reasoning=None if self._state.thinking_level == "off" else self._state.thinking_level,
            session_id=self.session_id,
            transport=self.transport,
            thinking_budgets=self.thinking_budgets,
            max_retry_delay_ms=self.max_retry_delay_ms,
            on_payload=self.on_payload,
            on_response=self.on_response,
            max_tokens=self._state.model.max_tokens or None,
            max_iterations=self.max_iterations,
            iteration_budget=IterationBudget(self.max_iterations),
            on_iteration_limit=self._on_iteration_limit,
        )

    def _drain_steering(self) -> list[AgentMessage]:
        return self._steering.drain()

    def _drain_follow_up(self) -> list[AgentMessage]:
        return self._follow_up.drain()

    def _context(self) -> AgentContext:
        return AgentContext(
            system_prompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=list(self._state.tools),
        )

    def prompt(
        self,
        prompt: Union[str, AgentMessage, list[AgentMessage]],
        stream_fn=None,
        images: list[ImageContent] | None = None,
    ) -> list[AgentMessage]:
        return run_sync(self.async_prompt(prompt, stream_fn=stream_fn, images=images))

    async def async_prompt(
        self,
        prompt: Union[str, AgentMessage, list[AgentMessage]],
        stream_fn=None,
        images: list[ImageContent] | None = None,
    ) -> list[AgentMessage]:
        self._begin_run(
            "Agent is already processing a prompt. Use steer() or follow_up() to queue messages, or wait for completion."
        )
        if isinstance(prompt, str):
            content = [TextContent(text=prompt)]
            if images:
                content.extend(images)
            messages: list[AgentMessage] = [UserMessage(content=content, timestamp=now_ms())]
        elif isinstance(prompt, list):
            messages = list(prompt)
        else:
            messages = [prompt]
        try:
            new_messages = await run_agent_loop_async(
                messages, self._context(), self._build_config(), self._make_sink(), self._signal, stream_fn
            )
        except Exception as error:  # noqa: BLE001
            new_messages = await self._handle_run_failure(error, self._signal.aborted)
        finally:
            self._finish_run()
        return new_messages

    def continue_(self, stream_fn=None) -> list[AgentMessage]:
        return run_sync(self.async_continue(stream_fn=stream_fn))

    async def async_continue(self, stream_fn=None) -> list[AgentMessage]:
        self._begin_run("Agent is already processing. Wait for completion before continuing.")
        try:
            context = self._context()
            last_message = context.messages[-1] if context.messages else None
            if last_message is None:
                raise ValueError("No messages to continue from")
            if getattr(last_message, "role", None) == "assistant":
                queued_steering = self._drain_steering()
                if queued_steering:
                    return await run_agent_loop_async(
                        queued_steering,
                        context,
                        self._build_config(skip_initial_steering_poll=True),
                        self._make_sink(),
                        self._signal,
                        stream_fn,
                    )
                queued_follow_up = self._drain_follow_up()
                if queued_follow_up:
                    return await run_agent_loop_async(
                        queued_follow_up, context, self._build_config(), self._make_sink(), self._signal, stream_fn
                    )
                raise ValueError("Cannot continue from message role: assistant")
            new_messages = await run_agent_loop_continue_async(
                context, self._build_config(), self._make_sink(), self._signal, stream_fn
            )
        except Exception as error:  # noqa: BLE001
            new_messages = await self._handle_run_failure(error, self._signal.aborted)
        finally:
            self._finish_run()
        return new_messages

    async def _handle_run_failure(self, error: BaseException, aborted: bool) -> list[AgentMessage]:
        failure_message = AssistantMessage(
            content=[TextContent(text="")],
            api=self._state.model.api,
            provider=self._state.model.provider,
            model=self._state.model.id,
            usage=empty_usage(),
            stop_reason="aborted" if aborted else "error",
            error_message=str(error),
            timestamp=now_ms(),
        )
        sink = self._make_sink()
        await sink(MessageStartEvent(message=failure_message))
        await sink(MessageEndEvent(message=failure_message))
        await sink(TurnEndEvent(message=failure_message, tool_results=[]))
        await sink(AgentEndEvent(messages=[failure_message]))
        return [failure_message]

    def _begin_run(self, active_error: str) -> None:
        with self._run_state_lock:
            token = self._run_lease.acquire(active_error)
            self._active_run_token = token
            self._signal = AbortSignal()
            self._idle_event.clear()
            self._state.is_streaming = True
            self._state.streaming_message = None
            self._state.error_message = None

    def _finish_run(self) -> None:
        with self._run_state_lock:
            self._state.is_streaming = False
            self._state.streaming_message = None
            self._state.pending_tool_calls = set()
            self._idle_event.set()
            token = self._active_run_token
            self._active_run_token = None
            if token is not None:
                token.release()

    def _make_sink(self) -> AgentEventSink:
        async def _sink(event: AgentEvent) -> None:
            self._process_event(event)
            for listener in list(self._listeners):
                if _listener_accepts_signal(listener):
                    await resolve(listener(event, self._signal))
                else:
                    await resolve(listener(event))

        return _sink

    def _process_event(self, event: AgentEvent) -> None:
        etype = event.type
        if etype == "message_start":
            if getattr(event.message, "role", None) == "assistant":
                self._state.streaming_message = event.message
        elif etype == "message_update":
            self._state.streaming_message = event.message
        elif etype == "message_end":
            self._state.messages.append(event.message)
            if getattr(event.message, "role", None) == "assistant":
                self._state.streaming_message = None
                if getattr(event.message, "stop_reason", None) in ("error", "aborted"):
                    self._state.error_message = getattr(event.message, "error_message", None)
        elif etype == "tool_execution_start":
            self._state.pending_tool_calls.add(event.tool_call_id)
        elif etype == "tool_execution_end":
            self._state.pending_tool_calls.discard(event.tool_call_id)


def _listener_accepts_signal(listener: Listener) -> bool:
    try:
        signature = inspect.signature(listener)
    except (TypeError, ValueError):
        return False
    positional_count = 0
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional_count += 1
    return positional_count >= 2
