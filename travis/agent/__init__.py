"""Core stateful-agent and bounded-loop interfaces."""

from travis.agent.agent import Agent, AgentState
from travis.agent.agent_loop import (
    AgentEventSink,
    AgentEventStream,
    agent_loop,
    agent_loop_continue,
    run_agent_loop,
    run_agent_loop_async,
    run_agent_loop_continue,
    run_agent_loop_continue_async,
)
from travis.agent.async_utils import MaybeAwaitable, resolve, run_sync
from travis.agent.iteration_budget import IterationBudget
from travis.agent.run_lease import RunLease, RunLeaseToken
from travis.agent.tool_coordinator import ToolCoordinator
from travis.agent.types import (
    AbortSignal,
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    IterationLimitContext,
    ImmediateToolOutcome,
    PreparedToolCall,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
)

__all__ = [
    "AbortSignal",
    "AfterToolCallContext",
    "AfterToolCallResult",
    "Agent",
    "AgentContext",
    "AgentEvent",
    "AgentEventSink",
    "AgentEventStream",
    "AgentLoopConfig",
    "AgentLoopTurnUpdate",
    "AgentMessage",
    "AgentState",
    "AgentTool",
    "AgentToolResult",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "IterationBudget",
    "IterationLimitContext",
    "ImmediateToolOutcome",
    "MaybeAwaitable",
    "PreparedToolCall",
    "RunLease",
    "RunLeaseToken",
    "ToolCoordinator",
    "PrepareNextTurnContext",
    "ShouldStopAfterTurnContext",
    "agent_loop",
    "agent_loop_continue",
    "run_agent_loop",
    "run_agent_loop_async",
    "run_agent_loop_continue",
    "run_agent_loop_continue_async",
    "resolve",
    "run_sync",
]
