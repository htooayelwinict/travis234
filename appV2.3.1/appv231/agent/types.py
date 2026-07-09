"""appv231 port of pi's agent-core types. Port of pi/packages/agent/src/types.ts."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, Union

from appv231.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    ImageContent,
    Message,
    Model,
    TextContent,
    Tool,
    ToolResultMessage,
)

ToolExecutionMode = Literal["sequential", "parallel"]
QueueMode = Literal["all", "one-at-a-time"]
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# AgentMessage = ai Message | app-defined custom message (any object with a `role`).
AgentMessage = Union[Message, Any]


class AbortSignal:
    """Minimal port of the DOM AbortSignal used by the loop."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def abort(self) -> None:
        self._event.set()


@dataclass
class AgentToolResult:
    content: list[Union[TextContent, ImageContent]]
    details: Any = None
    terminate: bool | None = None


AgentToolUpdateCallback = Callable[[AgentToolResult], None]


@dataclass
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]
    label: str
    execute: Callable[..., AgentToolResult]
    prepare_arguments: Optional[Callable[[Any], dict[str, Any]]] = None
    execution_mode: ToolExecutionMode | None = None


@dataclass
class AgentContext:
    system_prompt: str
    messages: list[AgentMessage]
    tools: list[AgentTool] | None = None


@dataclass
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None


@dataclass
class AfterToolCallResult:
    content: list[Union[TextContent, ImageContent]] | None = None
    details: Any = None
    is_error: bool | None = None
    terminate: bool | None = None


@dataclass
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: Any
    args: Any
    context: AgentContext


@dataclass
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: Any
    args: Any
    result: AgentToolResult
    is_error: bool
    context: AgentContext


@dataclass
class ShouldStopAfterTurnContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    context: AgentContext
    new_messages: list[AgentMessage]


PrepareNextTurnContext = ShouldStopAfterTurnContext


@dataclass
class AgentLoopTurnUpdate:
    context: AgentContext | None = None
    model: Model | None = None
    thinking_level: ThinkingLevel | None = None


@dataclass
class AgentLoopConfig:
    model: Model
    convert_to_llm: Callable[[list[AgentMessage]], list[Message]]
    transform_context: Optional[Callable[[list[AgentMessage], Optional[AbortSignal]], list[AgentMessage]]] = None
    get_api_key: Optional[Callable[[str], Optional[str]]] = None
    should_stop_after_turn: Optional[Callable[[ShouldStopAfterTurnContext], bool]] = None
    prepare_next_turn: Optional[Callable[[ShouldStopAfterTurnContext], Optional[AgentLoopTurnUpdate]]] = None
    get_steering_messages: Optional[Callable[[], list[AgentMessage]]] = None
    get_follow_up_messages: Optional[Callable[[], list[AgentMessage]]] = None
    tool_execution: ToolExecutionMode = "parallel"
    before_tool_call: Optional[Callable[[BeforeToolCallContext, Optional[AbortSignal]], Optional[BeforeToolCallResult]]] = None
    after_tool_call: Optional[Callable[[AfterToolCallContext, Optional[AbortSignal]], Optional[AfterToolCallResult]]] = None
    reasoning: str | None = None
    api_key: str | None = None
    session_id: str | None = None
    transport: str | None = None
    thinking_budgets: dict[str, int] | None = None
    max_retry_delay_ms: int | None = None
    on_payload: Any | None = None
    on_response: Any | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_iterations: int = 90
    iteration_budget: Any | None = None


# --- AgentEvent union (pi AgentEvent) ---


@dataclass
class AgentStartEvent:
    type: Literal["agent_start"] = "agent_start"


@dataclass
class AgentEndEvent:
    messages: list[AgentMessage]
    type: Literal["agent_end"] = "agent_end"


@dataclass
class TurnStartEvent:
    type: Literal["turn_start"] = "turn_start"


@dataclass
class TurnEndEvent:
    message: AgentMessage
    tool_results: list[ToolResultMessage]
    type: Literal["turn_end"] = "turn_end"


@dataclass
class MessageStartEvent:
    message: AgentMessage
    type: Literal["message_start"] = "message_start"


@dataclass
class MessageUpdateEvent:
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent
    type: Literal["message_update"] = "message_update"


@dataclass
class MessageEndEvent:
    message: AgentMessage
    type: Literal["message_end"] = "message_end"


@dataclass
class ToolExecutionStartEvent:
    tool_call_id: str
    tool_name: str
    args: Any
    type: Literal["tool_execution_start"] = "tool_execution_start"


@dataclass
class ToolExecutionUpdateEvent:
    tool_call_id: str
    tool_name: str
    args: Any
    partial_result: Any
    type: Literal["tool_execution_update"] = "tool_execution_update"


@dataclass
class ToolExecutionEndEvent:
    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool
    type: Literal["tool_execution_end"] = "tool_execution_end"


AgentEvent = Union[
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
]
