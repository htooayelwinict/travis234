from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp.types import CallToolResult, ListToolsResult, TextContent as McpTextContent, Tool
from travis234_mcp_adapter.config import LoadedConfig, ServerConfig
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.proxy_tool import dispatch_proxy, load_tool_catalog


def _tool(name: str, description: str = "") -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )


class FakeConnected:
    def __init__(self, pages=None) -> None:
        self.pages = pages or {None: ListToolsResult(tools=[_tool("echo", "Echo text")])}
        self.list_cursors: list[str | None] = []
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_tools(self, _signal, cursor=None):
        self.list_cursors.append(cursor)
        page = self.pages[cursor] if isinstance(self.pages, dict) else self.pages(cursor)
        return page

    async def call_tool(self, name, arguments, _signal):
        self.calls.append((name, arguments))
        return CallToolResult(content=[McpTextContent(type="text", text=f"called:{name}")])


class FakeRuntime:
    def __init__(self, connected: FakeConnected) -> None:
        self.connected = connected
        self.connects: list[str] = []

    async def connect(self, name, _signal):
        self.connects.append(name)
        return self.connected

    def is_connected(self, _name: str) -> bool:
        return bool(self.connects)


def _state(connected: FakeConnected | None = None):
    configured = ServerConfig(name="github", source_path=Path("/fixture/mcp.json"), command="fixture")
    runtime = FakeRuntime(connected or FakeConnected())
    return SimpleNamespace(
        config=LoadedConfig(servers={"github": configured}, sources=(), ignored_project_sources=()),
        config_error=None,
        runtime=runtime,
        catalogs={},
        spills=SpillRegistry(),
        generation=1,
        shadowed_configured_names=(),
    )


@pytest.mark.anyio
async def test_status_reports_shadowed_external_packaged_server_without_connecting() -> None:
    state = _state()
    state.config = LoadedConfig(
        servers={
            "ghost-os": ServerConfig(
                name="ghost-os",
                source_path=Path("/payload/bin/ghost"),
                command="/payload/bin/ghost",
                args=("mcp",),
            )
        },
        sources=(Path("/home/test/.travis234/agent/mcp.json"),),
        ignored_project_sources=(),
    )
    state.shadowed_configured_names = ("ghost-os",)

    result = await dispatch_proxy(state, {}, None)

    assert state.runtime.connects == []
    assert result.content[0].text == (
        "MCP adapter status\n"
        "- ghost-os: disconnected\n"
        "- ignored external configuration for packaged server: ghost-os"
    )
    assert result.details["travis234Mcp"]["shadowedConfiguredServers"] == [
        "ghost-os"
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        {"search": "issue"},
        {"describe": "search_issues"},
        {"tool": "search_issues"},
        {"server": "github", "search": "issue", "tool": "search_issues"},
        {"server": "github", "args": {"query": "x"}},
    ],
)
async def test_invalid_dispatch_shape_does_not_connect(params) -> None:
    state = _state()

    result = await dispatch_proxy(state, params, None)

    assert state.runtime.connects == []
    assert result.details["travis234Mcp"]["isError"] is True
    assert "Example:" in result.content[0].text


@pytest.mark.anyio
async def test_list_drains_pages_and_omits_schemas() -> None:
    connected = FakeConnected(
        {
            None: ListToolsResult(tools=[_tool("alpha", "First")], next_cursor="next"),
            "next": ListToolsResult(tools=[_tool("beta", "Second")]),
        }
    )
    state = _state(connected)

    result = await dispatch_proxy(state, {"server": "github"}, None)

    assert connected.list_cursors == [None, "next"]
    assert result.content[0].text == 'MCP tools on "github" (2)\n- alpha: First\n- beta: Second'
    assert "properties" not in result.content[0].text


@pytest.mark.anyio
async def test_repeated_pagination_cursor_returns_no_partial_catalog() -> None:
    connected = FakeConnected(
        {
            None: ListToolsResult(tools=[_tool("alpha")], next_cursor="same"),
            "same": ListToolsResult(tools=[_tool("beta")], next_cursor="same"),
        }
    )

    with pytest.raises(RuntimeError, match="repeated pagination cursor"):
        await load_tool_catalog(connected, None)


@pytest.mark.anyio
async def test_pagination_page_and_entry_limits() -> None:
    def endless(cursor):
        page = 0 if cursor is None else int(cursor)
        return ListToolsResult(tools=[_tool(f"tool-{page}")], next_cursor=str(page + 1))

    with pytest.raises(RuntimeError, match="100 pages"):
        await load_tool_catalog(FakeConnected(endless), None)

    too_many = ListToolsResult(tools=[_tool(f"tool-{index}") for index in range(10_001)])
    with pytest.raises(RuntimeError, match="10,000 tools"):
        await load_tool_catalog(FakeConnected({None: too_many}), None)


@pytest.mark.anyio
async def test_search_ranks_and_describe_returns_one_schema() -> None:
    connected = FakeConnected(
        {
            None: ListToolsResult(
                tools=[
                    _tool("issue", "Exact"),
                    _tool("issue_create", "Prefix"),
                    _tool("search_records", "Find an issue by title"),
                    _tool("unrelated", "No match"),
                ]
            )
        }
    )
    state = _state(connected)

    searched = await dispatch_proxy(state, {"server": "github", "search": "issue"}, None)
    described = await dispatch_proxy(
        state,
        {"server": "github", "describe": "issue_create"},
        None,
    )

    search_text = searched.content[0].text
    assert search_text.index("- issue: Exact") < search_text.index("- issue_create: Prefix")
    assert search_text.index("- issue_create: Prefix") < search_text.index("- search_records: Find an issue")
    assert "unrelated" not in search_text
    assert '"query"' in described.content[0].text
    assert "issue_create" in described.content[0].text
    assert connected.list_cursors == [None]


@pytest.mark.anyio
async def test_call_defaults_args_and_invokes_original_name_once() -> None:
    connected = FakeConnected()
    state = _state(connected)

    result = await dispatch_proxy(
        state,
        {"server": "github", "tool": "echo"},
        None,
    )

    assert connected.calls == [("echo", {})]
    assert result.content[0].text == "called:echo"


@pytest.mark.anyio
async def test_unknown_call_recommends_discovery_without_invoking() -> None:
    connected = FakeConnected()
    state = _state(connected)

    result = await dispatch_proxy(
        state,
        {"server": "github", "tool": "missing", "args": {"x": 1}},
        None,
    )

    assert connected.calls == []
    assert "list or search" in result.content[0].text
    assert result.details["travis234Mcp"]["isError"] is True


@pytest.mark.anyio
async def test_result_from_replaced_generation_is_rejected() -> None:
    release = asyncio.Event()

    class DelayedConnected(FakeConnected):
        async def call_tool(self, name, arguments, _signal):
            await release.wait()
            return await super().call_tool(name, arguments, _signal)

    state = _state(DelayedConnected())
    call = asyncio.create_task(
        dispatch_proxy(state, {"server": "github", "tool": "echo"}, None)
    )
    await asyncio.sleep(0)
    state.generation += 1
    release.set()

    with pytest.raises(RuntimeError, match="generation"):
        await call
