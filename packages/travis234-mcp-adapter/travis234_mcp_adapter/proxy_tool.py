from __future__ import annotations

from typing import TYPE_CHECKING

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.tools.types import ToolDefinition

if TYPE_CHECKING:
    from travis.agent.types import AbortSignal
    from travis234_mcp_adapter.extension import ExtensionState


MCP_TOOL_NAME = "mcp"
MCP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "server": {"type": "string"},
        "search": {"type": "string"},
        "describe": {"type": "string"},
        "tool": {"type": "string"},
        "args": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}


def create_proxy_definition(state: ExtensionState) -> ToolDefinition:
    async def execute(_tool_call_id, args, signal=None, _on_update=None, _ctx=None):
        return await dispatch_proxy(state, args, signal)

    return ToolDefinition(
        name=MCP_TOOL_NAME,
        label="MCP",
        description=(
            "Inspect and call tools from explicitly configured Model Context Protocol servers. "
            "Call with no arguments for connection-free status."
        ),
        parameters=MCP_TOOL_SCHEMA,
        execute=execute,
        prompt_guidelines=[
            "Call mcp with no arguments before using an unfamiliar configured server.",
            "Use one explicit server and one operation per call.",
        ],
    )


async def dispatch_proxy(
    state: ExtensionState,
    params: dict[str, object],
    signal: AbortSignal | None,
) -> AgentToolResult:
    del signal
    if not params:
        return _status_result(state)
    return _adapter_result(
        "MCP operation is not implemented yet.",
        operation="not_implemented",
        is_error=True,
    )


def _status_result(state: ExtensionState) -> AgentToolResult:
    if state.config_error is not None:
        return _adapter_result(
            f"MCP adapter configuration error: {state.config_error}",
            operation="status",
            is_error=True,
            servers=[],
            ignored_project_sources=0,
        )
    servers = sorted(state.config.servers)
    lines = ["MCP adapter status"]
    lines.extend(f"- {name}: disconnected" for name in servers)
    ignored_count = len(state.config.ignored_project_sources)
    if ignored_count:
        noun = "file" if ignored_count == 1 else "files"
        lines.append(
            f"- {ignored_count} project configuration {noun} ignored until trust and reload"
        )
    elif not servers:
        lines.append("- no configured servers")
    return _adapter_result(
        "\n".join(lines),
        operation="status",
        is_error=False,
        servers=[{"name": name, "status": "disconnected"} for name in servers],
        ignored_project_sources=ignored_count,
    )


def _adapter_result(
    text: str,
    *,
    operation: str,
    is_error: bool,
    servers: list[dict[str, str]] | None = None,
    ignored_project_sources: int | None = None,
) -> AgentToolResult:
    marker: dict[str, object] = {
        "operation": operation,
    }
    if servers is not None:
        marker["servers"] = servers
    if ignored_project_sources is not None:
        marker["ignoredProjectSources"] = ignored_project_sources
    marker["isError"] = is_error
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"travis234Mcp": marker},
    )
