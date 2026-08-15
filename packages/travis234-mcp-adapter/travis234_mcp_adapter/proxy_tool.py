from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from mcp.types import Tool
from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.tools.types import ToolDefinition
from travis234_mcp_adapter.catalogs import (
    MAX_CATALOG_PAGES,
    MAX_SEARCH_RESULTS,
    McpProtocolError,
    PromptCatalog,
    ResourceCatalog,
    load_prompt_catalog,
    load_resource_catalog,
)
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.results import (
    convert_call_result,
    prompt_get_result,
    prompt_list_result,
    resource_list_result,
    resource_read_result,
)

if TYPE_CHECKING:
    from travis.agent.types import AbortSignal
    from travis234_mcp_adapter.config import LoadedConfig
    from travis234_mcp_adapter.runtime import ConnectedServer, McpRuntime


MCP_TOOL_NAME = "mcp"
MCP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "server": {"type": "string"},
        "operation": {
            "type": "string",
            "enum": [
                "tools.list",
                "tools.search",
                "tools.describe",
                "tools.call",
                "resources.list",
                "resources.read",
                "prompts.list",
                "prompts.get",
            ],
        },
        "query": {"type": "string"},
        "name": {"type": "string"},
        "resource": {
            "type": "string",
            "pattern": "^mcp-resource-[0-9a-f]{32}$",
        },
        "prompt": {"type": "string"},
        "arguments": {"type": "object", "additionalProperties": True},
        "search": {"type": "string"},
        "describe": {"type": "string"},
        "tool": {"type": "string"},
        "args": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}
MAX_CATALOG_TOOLS = 10_000
_RESOURCE_REFERENCE = re.compile(r"^mcp-resource-[0-9a-f]{32}$")


class ProxyOperation(StrEnum):
    TOOLS_LIST = "tools.list"
    TOOLS_SEARCH = "tools.search"
    TOOLS_DESCRIBE = "tools.describe"
    TOOLS_CALL = "tools.call"
    RESOURCES_LIST = "resources.list"
    RESOURCES_READ = "resources.read"
    PROMPTS_LIST = "prompts.list"
    PROMPTS_GET = "prompts.get"


@dataclass(frozen=True)
class NormalizedDispatch:
    server: str
    operation: ProxyOperation
    query: str | None = None
    name: str | None = None
    resource: str | None = None
    prompt: str | None = None
    arguments: dict[str, object] | None = None

    @property
    def result_operation(self) -> str:
        return {
            ProxyOperation.TOOLS_LIST: "list",
            ProxyOperation.TOOLS_SEARCH: "search",
            ProxyOperation.TOOLS_DESCRIBE: "describe",
            ProxyOperation.TOOLS_CALL: "call",
        }.get(self.operation, self.operation.value)


class ProxyState(Protocol):
    config: LoadedConfig
    config_error: str | None
    runtime: McpRuntime | None
    catalogs: dict[str, tuple[Tool, ...]]
    resource_catalogs: dict[str, ResourceCatalog]
    prompt_catalogs: dict[str, PromptCatalog]
    spills: SpillRegistry
    generation: int
    shadowed_configured_names: tuple[str, ...]


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
        effects=frozenset({"read", "write", "execute", "network"}),
        policy_context=_policy_context,
    )


def _policy_context(params: dict[str, object]) -> dict[str, str]:
    if not params:
        return {"operation": "status"}
    normalized = _normalize_dispatch(params)
    operation = (
        normalized.result_operation
        if isinstance(normalized, NormalizedDispatch)
        else "validation"
    )
    context = {"operation": operation}
    server = params.get("server")
    if isinstance(server, str) and server.strip():
        context["server"] = server
    return context


async def dispatch_proxy(
    state: ProxyState,
    params: dict[str, object],
    signal: AbortSignal | None,
) -> AgentToolResult:
    if not params:
        return _status_result(state)

    normalized = _normalize_dispatch(params)
    if isinstance(normalized, str):
        return _error_result(normalized, operation="validation")
    server_name = normalized.server
    if server_name not in state.config.servers:
        return _error_result(
            f'Unknown configured MCP server "{server_name}". Example: {{"server":"server-name"}}',
            operation="validation",
        )
    if state.runtime is None:
        return _error_result("MCP runtime is not active. Example: {}", operation="validation")

    operation = normalized.result_operation
    generation = state.generation
    try:
        if normalized.operation == ProxyOperation.RESOURCES_READ:
            resource_catalog = _resource_catalogs(state).get(server_name)
            if (
                resource_catalog is None
                or resource_catalog.generation != generation
                or normalized.resource not in resource_catalog.references
            ):
                return _error_result(
                    "Unknown or stale MCP resource reference; list resources again. Example: "
                    f'{{"server":"{server_name}","operation":"resources.list"}}',
                    operation=operation,
                    server=server_name,
                )
        connected = await state.runtime.connect(server_name, signal)
        if normalized.operation in {
            ProxyOperation.TOOLS_LIST,
            ProxyOperation.TOOLS_SEARCH,
            ProxyOperation.TOOLS_DESCRIBE,
            ProxyOperation.TOOLS_CALL,
        }:
            catalog = state.catalogs.get(server_name)
            if catalog is None:
                catalog = await load_tool_catalog(connected, signal)
                state.catalogs[server_name] = catalog
        else:
            catalog = ()
        if normalized.operation == ProxyOperation.TOOLS_LIST:
            result = _list_result(server_name, catalog)
        elif normalized.operation == ProxyOperation.TOOLS_SEARCH:
            result = _search_result(server_name, catalog, normalized.query or "")
        elif normalized.operation == ProxyOperation.TOOLS_DESCRIBE:
            result = _describe_result(server_name, catalog, normalized.name or "")
        elif normalized.operation == ProxyOperation.TOOLS_CALL:
            result = await _call_result(
                connected,
                server_name,
                catalog,
                normalized.name or "",
                normalized.arguments,
                signal,
                state.spills,
            )
        elif normalized.operation == ProxyOperation.RESOURCES_LIST:
            resource_catalog = await load_resource_catalog(
                connected,
                signal,
                generation=generation,
            )
            _resource_catalogs(state)[server_name] = resource_catalog
            result = resource_list_result(
                server_name,
                resource_catalog,
                normalized.query,
                state.spills,
            )
        elif normalized.operation == ProxyOperation.RESOURCES_READ:
            resource_catalog = _resource_catalogs(state)[server_name]
            result = await resource_read_result(
                connected,
                server_name,
                resource_catalog,
                normalized.resource or "",
                signal,
                state.spills,
            )
        elif normalized.operation in {
            ProxyOperation.PROMPTS_LIST,
            ProxyOperation.PROMPTS_GET,
        }:
            prompt_catalog = _prompt_catalogs(state).get(server_name)
            if prompt_catalog is None or prompt_catalog.generation != generation:
                prompt_catalog = await load_prompt_catalog(
                    connected,
                    signal,
                    generation=generation,
                )
                _prompt_catalogs(state)[server_name] = prompt_catalog
            if normalized.operation == ProxyOperation.PROMPTS_LIST:
                result = prompt_list_result(
                    server_name,
                    prompt_catalog,
                    normalized.query,
                    state.spills,
                )
            else:
                result = await prompt_get_result(
                    connected,
                    server_name,
                    prompt_catalog,
                    normalized.prompt or "",
                    normalized.arguments or {},
                    signal,
                    state.spills,
                )
        else:
            result = _error_result(
                "This MCP operation is not available in the installed adapter. Example: {}",
                operation=operation,
                server=server_name,
            )
        if state.generation != generation:
            raise StaleMcpGenerationError("MCP session generation changed during tool execution")
        return result
    except asyncio.CancelledError:
        raise
    except StaleMcpGenerationError:
        raise
    except McpProtocolError as error:
        if state.generation != generation:
            raise asyncio.CancelledError
        return _error_result(str(error), operation=operation, server=server_name)
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


def _resource_catalogs(state: ProxyState) -> dict[str, ResourceCatalog]:
    catalogs = getattr(state, "resource_catalogs", None)
    if not isinstance(catalogs, dict):
        catalogs = {}
        setattr(state, "resource_catalogs", catalogs)
    return catalogs


def _prompt_catalogs(state: ProxyState) -> dict[str, PromptCatalog]:
    catalogs = getattr(state, "prompt_catalogs", None)
    if not isinstance(catalogs, dict):
        catalogs = {}
        setattr(state, "prompt_catalogs", catalogs)
    return catalogs


def _normalize_dispatch(params: dict[str, object]) -> NormalizedDispatch | str:
    unknown = set(params).difference(MCP_TOOL_SCHEMA["properties"])
    if unknown:
        return 'Unknown MCP request fields. Example: {"server":"github"}'
    server = params.get("server")
    if not isinstance(server, str) or not server.strip():
        return 'An explicit non-empty server is required. Example: {"server":"github"}'
    server = server.strip()
    explicit = params.get("operation")
    if explicit is not None:
        if any(name in params for name in ("search", "describe", "tool", "args")):
            return 'Do not mix explicit operations with legacy aliases. Example: {"server":"github","operation":"tools.list"}'
        try:
            operation = ProxyOperation(explicit)
        except (TypeError, ValueError):
            return 'Unknown MCP operation. Example: {"server":"github","operation":"tools.list"}'
        allowed = {
            ProxyOperation.TOOLS_LIST: {"server", "operation"},
            ProxyOperation.TOOLS_SEARCH: {"server", "operation", "query"},
            ProxyOperation.TOOLS_DESCRIBE: {"server", "operation", "name"},
            ProxyOperation.TOOLS_CALL: {"server", "operation", "name", "arguments"},
            ProxyOperation.RESOURCES_LIST: {"server", "operation", "query"},
            ProxyOperation.RESOURCES_READ: {"server", "operation", "resource"},
            ProxyOperation.PROMPTS_LIST: {"server", "operation", "query"},
            ProxyOperation.PROMPTS_GET: {"server", "operation", "prompt", "arguments"},
        }[operation]
        if set(params).difference(allowed):
            return 'Request fields do not match the selected operation. Example: {"server":"github","operation":"tools.list"}'
        required_field = {
            ProxyOperation.TOOLS_SEARCH: "query",
            ProxyOperation.TOOLS_DESCRIBE: "name",
            ProxyOperation.TOOLS_CALL: "name",
            ProxyOperation.RESOURCES_READ: "resource",
            ProxyOperation.PROMPTS_GET: "prompt",
        }.get(operation)
        if required_field is not None:
            value = params.get(required_field)
            if not isinstance(value, str) or not value.strip():
                return f'{required_field} must be non-empty. Example: {{"server":"github","operation":"{operation.value}"}}'
        if "query" in params and (
            not isinstance(params["query"], str) or not params["query"].strip()
        ):
            return f'query must be non-empty. Example: {{"server":"github","operation":"{operation.value}"}}'
        if operation == ProxyOperation.RESOURCES_READ and _RESOURCE_REFERENCE.fullmatch(
            str(params["resource"])
        ) is None:
            return 'resource must be an opaque mcp-resource reference. Example: {"server":"github","operation":"resources.list"}'
        arguments = params.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            return 'arguments must be an object. Example: {"server":"github","operation":"tools.call","name":"echo","arguments":{}}'
        if operation == ProxyOperation.PROMPTS_GET and isinstance(arguments, dict) and any(
            not isinstance(value, str) for value in arguments.values()
        ):
            return 'prompt arguments must contain string values. Example: {"server":"github","operation":"prompts.get","prompt":"review","arguments":{}}'
        return NormalizedDispatch(
            server=server,
            operation=operation,
            query=str(params["query"]).strip() if "query" in params else None,
            name=str(params["name"]).strip() if "name" in params else None,
            resource=str(params["resource"]) if "resource" in params else None,
            prompt=str(params["prompt"]).strip() if "prompt" in params else None,
            arguments=dict(arguments) if isinstance(arguments, dict) else None,
        )

    if any(name in params for name in ("query", "name", "resource", "prompt", "arguments")):
        return 'Explicit request fields require operation. Example: {"server":"github","operation":"tools.list"}'
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
    operation = {
        "search": ProxyOperation.TOOLS_SEARCH,
        "describe": ProxyOperation.TOOLS_DESCRIBE,
        "tool": ProxyOperation.TOOLS_CALL,
    }.get(operations[0] if operations else "", ProxyOperation.TOOLS_LIST)
    value = params.get(operations[0]) if operations else None
    return NormalizedDispatch(
        server=server,
        operation=operation,
        query=str(value).strip() if operation == ProxyOperation.TOOLS_SEARCH else None,
        name=str(value).strip()
        if operation in {ProxyOperation.TOOLS_DESCRIBE, ProxyOperation.TOOLS_CALL}
        else None,
        arguments=dict(params["args"]) if isinstance(params.get("args"), dict) else None,
    )


def _validate_dispatch(params: dict[str, object]) -> str | None:
    normalized = _normalize_dispatch(params)
    return normalized if isinstance(normalized, str) else None


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
    shadowed_names = tuple(getattr(state, "shadowed_configured_names", ()))
    lines.extend(
        f"- ignored external configuration for packaged server: {name}"
        for name in shadowed_names
    )
    if not ignored_count and not server_details and not shadowed_names:
        lines.append("- no configured servers")
    return _adapter_result(
        "\n".join(lines),
        operation="status",
        is_error=False,
        servers=server_details,
        ignored_project_sources=ignored_count,
        shadowed_configured_servers=list(shadowed_names),
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
    shadowed_configured_servers: list[str] | None = None,
    marker_fields: dict[str, object] | None = None,
) -> AgentToolResult:
    marker: dict[str, object] = {"operation": operation}
    if server is not None:
        marker["server"] = server
    if servers is not None:
        marker["servers"] = servers
    if ignored_project_sources is not None:
        marker["ignoredProjectSources"] = ignored_project_sources
    if shadowed_configured_servers:
        marker["shadowedConfiguredServers"] = shadowed_configured_servers
    if marker_fields:
        marker.update(marker_fields)
    marker["isError"] = is_error
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"travis234Mcp": marker},
    )
