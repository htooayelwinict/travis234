"""Tool-definition interfaces and the agent-tool bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from travis.agent.types import AgentTool, AgentToolResult
from travis.coding_agent.source_info import SourceInfo

if TYPE_CHECKING:
    from travis.tui.component import Component

ToolRenderContent = Union[str, "Component"]


@dataclass
class ToolContext:
    """Execution context injected into a ToolDefinition.execute (travis ExtensionContext subset)."""

    cwd: str
    model: Any | None = None


@dataclass
class ToolDefinition:
    """An AgentTool plus UI/prompt concerns (travis ToolDefinition)."""

    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., AgentToolResult]  # (tool_call_id, args, signal, on_update, ctx) -> AgentToolResult
    prompt_snippet: str | None = None
    prompt_guidelines: list[str] = field(default_factory=list)
    render_shell: str = "default"  # "default" | "self"
    render_call: Optional[Callable[..., ToolRenderContent]] = None
    render_result: Optional[Callable[..., ToolRenderContent]] = None
    execution_mode: str | None = None
    prepare_arguments: Optional[Callable[[Any], Any]] = None
    source_info: SourceInfo | None = None


def wrap_tool_definition(
    definition: ToolDefinition,
    ctx_factory: Optional[Callable[[], ToolContext]] = None,
) -> AgentTool:
    """Bridge a ToolDefinition to an AgentTool, injecting ctx at execute time."""

    def _execute(tool_call_id, args, signal=None, on_update=None):
        ctx = ctx_factory() if ctx_factory else None
        return definition.execute(tool_call_id, args, signal, on_update, ctx)

    return AgentTool(
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
        label=definition.label,
        execute=_execute,
        prepare_arguments=definition.prepare_arguments,
        execution_mode=definition.execution_mode,
    )


def create_tool_definition_from_agent_tool(tool: AgentTool) -> ToolDefinition:
    """Synthesize a minimal ToolDefinition from a bare AgentTool (travis parity)."""

    def _execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        return tool.execute(tool_call_id, args, signal, on_update)

    return ToolDefinition(
        name=tool.name,
        label=tool.label,
        description=tool.description,
        parameters=tool.parameters,
        execute=_execute,
        execution_mode=tool.execution_mode,
    )
