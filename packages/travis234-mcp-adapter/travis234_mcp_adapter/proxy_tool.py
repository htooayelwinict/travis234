from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Protocol

from mcp.types import Tool
from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.tools.types import ToolDefinition
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.results import convert_call_result

if TYPE_CHECKING:
    from travis.agent.types import AbortSignal
    from travis234_mcp_adapter.config import LoadedConfig
    from travis234_mcp_adapter.runtime import ConnectedServer, McpRuntime


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
MAX_CATALOG_PAGES = 100
MAX_CATALOG_TOOLS = 10_000
MAX_SEARCH_RESULTS = 20


class ProxyState(Protocol):
    config: LoadedConfig
    config_error: str | None
    runtime: McpRuntime | None
    catalogs: dict[str, tuple[Tool, ...]]
    spills: SpillRegistry
    generation: int


class StaleMcpGenerationError(RuntimeError):
    pass


def create_proxy_definition(state: ProxyState) -> ToolDefinition:
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
    state: ProxyState,
    params: dict[str, object],
    signal: AbortSignal | None,
) -> AgentToolResult:
    if not params:
        return _status_result(state)

    validation = _validate_dispatch(params)
    if validation is not None:
        return _error_result(validation, operation="validation")
    server_name = str(params["server"])
    if server_name not in state.config.servers:
        return _error_result(
            f'Unknown configured MCP server "{server_name}". Example: {{"server":"server-name"}}',
            operation="validation",
        )
    if state.runtime is None:
        return _error_result("MCP runtime is not active. Example: {}", operation="validation")

    operation = next(
        (name for name in ("search", "describe", "tool") if params.get(name) is not None),
        "list",
    )
    generation = state.generation
    try:
        connected = await state.runtime.connect(server_name, signal)
        catalog = state.catalogs.get(server_name)
        if catalog is None:
            catalog = await load_tool_catalog(connected, signal)
            state.catalogs[server_name] = catalog
        if operation == "list":
            result = _list_result(server_name, catalog)
        elif operation == "search":
            result = _search_result(server_name, catalog, str(params["search"]))
        elif operation == "describe":
            result = _describe_result(server_name, catalog, str(params["describe"]))
        else:
            result = await _call_result(
                connected,
                server_name,
                catalog,
                str(params["tool"]),
                params.get("args"),
                signal,
                state.spills,
            )
        if state.generation != generation:
            raise StaleMcpGenerationError("MCP session generation changed during tool execution")
        return result
    except asyncio.CancelledError:
        raise
    except StaleMcpGenerationError:
        raise
    except TimeoutError as error:
        if state.generation != generation:
            raise asyncio.CancelledError
        return _error_result(str(error), operation=operation, server=server_name)
    except Exception as error:
        if state.generation != generation:
            raise asyncio.CancelledError
        return _error_result(
            f'MCP server "{server_name}" {operation} failed ({type(error).__name__}).',
            operation=operation,
            server=server_name,
        )


async def load_tool_catalog(
    connected: ConnectedServer,
    signal: AbortSignal | None,
) -> tuple[Tool, ...]:
    tools: list[Tool] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for page_number in range(1, MAX_CATALOG_PAGES + 1):
        page = await connected.list_tools(signal, cursor=cursor)
        tools.extend(page.tools)
        if len(tools) > MAX_CATALOG_TOOLS:
            raise RuntimeError("MCP catalog exceeded 10,000 tools")
        next_cursor = page.next_cursor
        if next_cursor is None:
            return tuple(tools)
        if next_cursor in seen_cursors:
            raise RuntimeError("MCP catalog returned a repeated pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if page_number == MAX_CATALOG_PAGES:
            raise RuntimeError("MCP catalog exceeded 100 pages")
    raise RuntimeError("MCP catalog pagination did not terminate")


def _validate_dispatch(params: dict[str, object]) -> str | None:
    server = params.get("server")
    if not isinstance(server, str) or not server.strip():
        return 'An explicit non-empty server is required. Example: {"server":"github"}'
    operations = [name for name in ("search", "describe", "tool") if params.get(name) is not None]
    if len(operations) > 1:
        return 'Choose exactly one operation. Example: {"server":"github","search":"issue"}'
    if "args" in params and operations != ["tool"]:
        return 'args is valid only with tool. Example: {"server":"github","tool":"echo","args":{}}'
    if operations:
        value = params[operations[0]]
        if not isinstance(value, str) or not value.strip():
            return f'{operations[0]} must be non-empty. Example: {{"server":"github","{operations[0]}":"name"}}'
    if "args" in params and not isinstance(params["args"], dict):
        return 'args must be an object. Example: {"server":"github","tool":"echo","args":{}}'
    return None


def _status_result(state: ProxyState) -> AgentToolResult:
    if state.config_error is not None:
        return _adapter_result(
            f"MCP adapter configuration error: {state.config_error}",
            operation="status",
            is_error=True,
            servers=[],
            ignored_project_sources=0,
        )
    server_details = []
    for name in sorted(state.config.servers):
        connected = state.runtime is not None and state.runtime.is_connected(name)
        server_details.append({"name": name, "status": "connected" if connected else "disconnected"})
    lines = ["MCP adapter status"]
    lines.extend(f"- {item['name']}: {item['status']}" for item in server_details)
    ignored_count = len(state.config.ignored_project_sources)
    if ignored_count:
        noun = "file" if ignored_count == 1 else "files"
        lines.append(f"- {ignored_count} project configuration {noun} ignored until trust and reload")
    elif not server_details:
        lines.append("- no configured servers")
    return _adapter_result(
        "\n".join(lines),
        operation="status",
        is_error=False,
        servers=server_details,
        ignored_project_sources=ignored_count,
    )


def _list_result(server: str, catalog: tuple[Tool, ...]) -> AgentToolResult:
    lines = [f'MCP tools on "{server}" ({len(catalog)})']
    lines.extend(f"- {tool.name}: {_description(tool)}" for tool in catalog)
    return _adapter_result("\n".join(lines), operation="list", is_error=False, server=server)


def _search_result(server: str, catalog: tuple[Tool, ...], query: str) -> AgentToolResult:
    needle = query.casefold()
    matches: list[tuple[int, str, Tool]] = []
    for tool in catalog:
        name = tool.name.casefold()
        title = str(getattr(tool, "title", "") or "")
        description = str(tool.description or "")
        haystack = " ".join((tool.name, title, description)).casefold()
        if name == needle:
            rank = 0
        elif name.startswith(needle):
            rank = 1
        elif needle in haystack:
            rank = 2
        else:
            continue
        matches.append((rank, name, tool))
    matches.sort(key=lambda item: (item[0], item[1]))
    visible = matches[:MAX_SEARCH_RESULTS]
    lines = [f'MCP search on "{server}" for {query!r} ({len(matches)} matches)']
    lines.extend(f"- {tool.name}: {_description(tool)}" for _rank, _name, tool in visible)
    if len(matches) > MAX_SEARCH_RESULTS:
        lines.append(f"- refine the search to see the remaining {len(matches) - MAX_SEARCH_RESULTS} matches")
    return _adapter_result("\n".join(lines), operation="search", is_error=False, server=server)


def _describe_result(server: str, catalog: tuple[Tool, ...], name: str) -> AgentToolResult:
    tool = next((item for item in catalog if item.name == name), None)
    if tool is None:
        return _error_result(
            f'Unknown MCP tool "{name}" on "{server}"; use list or search first.',
            operation="describe",
            server=server,
        )
    schema = json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True, indent=2)
    text = f'MCP tool "{tool.name}" on "{server}"\n{_description(tool)}\nInput schema:\n{schema}'
    return _adapter_result(text, operation="describe", is_error=False, server=server)


async def _call_result(
    connected: ConnectedServer,
    server: str,
    catalog: tuple[Tool, ...],
    name: str,
    raw_arguments: object,
    signal: AbortSignal | None,
    spills: SpillRegistry,
) -> AgentToolResult:
    if not any(tool.name == name for tool in catalog):
        return _error_result(
            f'Unknown MCP tool "{name}" on "{server}"; use list or search first.',
            operation="call",
            server=server,
        )
    arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
    result = await connected.call_tool(name, arguments, signal)
    converted = convert_call_result(result, spills)
    converted.details["travis234Mcp"]["server"] = server
    return converted


def _description(tool: Tool) -> str:
    normalized = " ".join(str(tool.description or "No description").split())
    return normalized[:240] + ("…" if len(normalized) > 240 else "")


def _error_result(text: str, *, operation: str, server: str | None = None) -> AgentToolResult:
    return _adapter_result(text, operation=operation, is_error=True, server=server)


def _adapter_result(
    text: str,
    *,
    operation: str,
    is_error: bool,
    server: str | None = None,
    servers: list[dict[str, str]] | None = None,
    ignored_project_sources: int | None = None,
) -> AgentToolResult:
    marker: dict[str, object] = {"operation": operation}
    if server is not None:
        marker["server"] = server
    if servers is not None:
        marker["servers"] = servers
    if ignored_project_sources is not None:
        marker["ignoredProjectSources"] = ignored_project_sources
    marker["isError"] = is_error
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"travis234Mcp": marker},
    )
