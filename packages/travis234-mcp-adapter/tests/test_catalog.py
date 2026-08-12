from __future__ import annotations

import re
from pathlib import Path

import pytest
from mcp.types import ListToolsResult, Tool, ToolAnnotations

from travis234_mcp_adapter.catalog import (
    MAX_CATALOG_PAGES,
    MAX_CATALOG_TOOLS,
    MAX_SCHEMA_BYTES,
    MAX_SERVER_SCHEMA_BYTES,
    NativeToolSpec,
    ServerCatalog,
    admit_session_catalogs,
    build_server_catalog,
    load_remote_tools,
    native_tool_name,
)
from travis234_mcp_adapter.config import ServerConfig


def _tool(
    name: str,
    *,
    schema: dict | None = None,
    description: str | None = None,
    read_only: bool | None = None,
) -> Tool:
    annotations = None if read_only is None else ToolAnnotations(readOnlyHint=read_only)
    return Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object", "properties": {}},
        annotations=annotations,
    )


def _server(tmp_path: Path, **overrides: object) -> ServerConfig:
    values = {
        "name": "fixture",
        "source_path": tmp_path / "mcp.json",
        "command": "fixture",
    }
    values.update(overrides)
    return ServerConfig(**values)


def test_native_tool_name_preserves_safe_ghost_name() -> None:
    assert native_tool_name("ghost-os", "ghost_context") == "mcp__ghost-os__ghost_context"


def test_native_tool_name_normalizes_and_hashes_deterministically() -> None:
    first = native_tool_name("strange server", "read/item")
    second = native_tool_name("strange server", "read/item")
    neighbor = native_tool_name("strange-server", "read_item")

    assert first == second
    assert first != neighbor
    assert len(first) <= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)
    assert re.search(r"__[0-9a-f]{10}$", first)


@pytest.mark.parametrize(
    ("server_name", "remote_name"),
    [
        ("s" * 80, "read"),
        ("fixture", "t" * 80),
        ("服务器", "读取"),
    ],
)
def test_native_tool_name_bounds_overlong_and_unicode_segments(
    server_name: str,
    remote_name: str,
) -> None:
    result = native_tool_name(server_name, remote_name)

    assert len(result) <= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", result)
    assert re.search(r"__[0-9a-f]{10}$", result)


def test_build_server_catalog_filters_before_applying_budgets(tmp_path: Path) -> None:
    server = _server(
        tmp_path,
        include_tools=("read", "missing"),
        exclude_tools=("delete",),
    )
    result = build_server_catalog(
        server,
        (_tool("read"), _tool("write"), _tool("delete")),
        reserved_names={"mcp"},
    )

    assert [tool.remote_name for tool in result.tools] == ["read"]
    assert any("missing" in item for item in result.diagnostics)
    assert any("delete" in item for item in result.diagnostics)


def test_build_server_catalog_skips_invalid_oversize_duplicate_and_reserved_tools(
    tmp_path: Path,
) -> None:
    result = build_server_catalog(
        _server(tmp_path),
        (
            _tool("healthy"),
            _tool("invalid", schema={"type": "not-a-json-schema-type"}),
            _tool("huge", schema={"type": "object", "description": "x" * (MAX_SCHEMA_BYTES + 1)}),
            _tool("healthy"),
            _tool("read"),
        ),
        reserved_names={"mcp__fixture__read"},
    )

    assert [tool.remote_name for tool in result.tools] == ["healthy"]
    rendered = "\n".join(result.diagnostics)
    assert "invalid" in rendered
    assert "huge" in rendered
    assert "duplicate" in rendered.casefold()
    assert "reserved" in rendered
    assert result.rejected is False


def test_build_server_catalog_rejects_whole_server_over_tool_limit(tmp_path: Path) -> None:
    result = build_server_catalog(
        _server(tmp_path),
        tuple(_tool(f"tool_{index}") for index in range(65)),
        reserved_names=set(),
    )

    assert result.rejected is True
    assert result.tools == ()
    assert any("64 tools" in item for item in result.diagnostics)


def test_build_server_catalog_rejects_whole_server_over_schema_budget(tmp_path: Path) -> None:
    schema = {"type": "object", "description": "x" * 64_000}
    result = build_server_catalog(
        _server(tmp_path),
        tuple(_tool(f"tool_{index}", schema=schema) for index in range(5)),
        reserved_names=set(),
    )

    assert result.rejected is True
    assert result.tools == ()
    assert result.schema_bytes == 0
    assert any("256 KiB" in item for item in result.diagnostics)


def test_build_server_catalog_bounds_utf8_description_and_execution_mode(tmp_path: Path) -> None:
    result = build_server_catalog(
        _server(tmp_path),
        (
            _tool("parallel", description="界" * 2_000, read_only=True),
            _tool("sequential", read_only=False),
            _tool("default"),
        ),
        reserved_names=set(),
    )

    assert len(result.tools[0].description.encode("utf-8")) <= 4 * 1024
    assert [tool.execution_mode for tool in result.tools] == [
        "parallel",
        "sequential",
        "sequential",
    ]


class _PagedServer:
    def __init__(self, pages: dict[str | None, ListToolsResult]) -> None:
        self.pages = pages
        self.calls: list[str | None] = []

    async def list_tools(self, _signal, cursor: str | None = None) -> ListToolsResult:
        self.calls.append(cursor)
        return self.pages[cursor]


@pytest.mark.anyio
async def test_load_remote_tools_follows_bounded_pagination() -> None:
    connected = _PagedServer(
        {
            None: ListToolsResult(tools=[_tool("one")], nextCursor="next"),
            "next": ListToolsResult(tools=[_tool("two")]),
        }
    )

    tools = await load_remote_tools(connected, None)

    assert [tool.name for tool in tools] == ["one", "two"]
    assert connected.calls == [None, "next"]


@pytest.mark.anyio
async def test_load_remote_tools_rejects_cursor_cycles() -> None:
    connected = _PagedServer(
        {
            None: ListToolsResult(tools=[], nextCursor="cycle"),
            "cycle": ListToolsResult(tools=[], nextCursor="cycle"),
        }
    )

    with pytest.raises(RuntimeError, match="repeated pagination cursor"):
        await load_remote_tools(connected, None)


@pytest.mark.anyio
async def test_load_remote_tools_rejects_more_than_100_pages() -> None:
    pages: dict[str | None, ListToolsResult] = {}
    cursor: str | None = None
    for index in range(MAX_CATALOG_PAGES):
        next_cursor = str(index + 1)
        pages[cursor] = ListToolsResult(tools=[], nextCursor=next_cursor)
        cursor = next_cursor
    connected = _PagedServer(pages)

    with pytest.raises(RuntimeError, match="exceeded 100 pages"):
        await load_remote_tools(connected, None)


@pytest.mark.anyio
async def test_load_remote_tools_rejects_more_than_10000_raw_tools() -> None:
    connected = _PagedServer(
        {None: ListToolsResult(tools=[_tool(f"tool_{index}") for index in range(MAX_CATALOG_TOOLS + 1)])}
    )

    with pytest.raises(RuntimeError, match="exceeded 10,000 tools"):
        await load_remote_tools(connected, None)


def _catalog(name: str, tool_count: int, schema_bytes: int) -> ServerCatalog:
    tools = tuple(
        NativeToolSpec(
            server_name=name,
            remote_name=f"tool_{index}",
            visible_name=f"mcp__{name}__tool_{index}",
            label=f"{name} / tool_{index}",
            description="description",
            parameters={"type": "object"},
            execution_mode="sequential",
        )
        for index in range(tool_count)
    )
    return ServerCatalog(name, tools, schema_bytes, ())


def test_admit_session_catalogs_is_sorted_and_accepts_only_whole_servers() -> None:
    plan = admit_session_catalogs(
        (
            _catalog("charlie", 1, 1),
            _catalog("bravo", 64, 200_000),
            _catalog("alpha", 64, 200_000),
        )
    )

    assert [catalog.server_name for catalog in plan.accepted] == ["alpha", "bravo"]
    assert plan.tool_count == 128
    assert plan.schema_bytes == 400_000
    assert plan.rejected[0][0] == "charlie"
    assert "128 tools" in plan.rejected[0][1]


def test_admit_session_catalogs_applies_aggregate_schema_budget() -> None:
    plan = admit_session_catalogs(
        (
            _catalog("bravo", 1, MAX_SERVER_SCHEMA_BYTES),
            _catalog("alpha", 1, MAX_SERVER_SCHEMA_BYTES),
            _catalog("charlie", 1, 1),
        )
    )

    assert [catalog.server_name for catalog in plan.accepted] == ["alpha", "bravo"]
    assert plan.rejected == (("charlie", "session schema budget exceeds 512 KiB"),)
