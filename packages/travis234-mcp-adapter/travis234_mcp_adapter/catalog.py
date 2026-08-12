from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp.types import Tool
from travis.ai.validation import compile_tool_schema

from travis234_mcp_adapter.config import ServerConfig

if TYPE_CHECKING:
    from travis.agent.types import AbortSignal
    from travis234_mcp_adapter.runtime import ConnectedServer


SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
UNSAFE_RUN = re.compile(r"[^A-Za-z0-9_-]+")
MAX_VISIBLE_NAME = 64
MAX_CATALOG_PAGES = 100
MAX_CATALOG_TOOLS = 10_000
MAX_SERVER_TOOLS = 64
MAX_SESSION_TOOLS = 128
MAX_SCHEMA_BYTES = 64 * 1024
MAX_SERVER_SCHEMA_BYTES = 256 * 1024
MAX_SESSION_SCHEMA_BYTES = 512 * 1024
MAX_DESCRIPTION_BYTES = 4 * 1024
MAX_DIAGNOSTICS = 64
MAX_DIAGNOSTIC_BYTES = 512


@dataclass(frozen=True)
class NativeToolSpec:
    server_name: str
    remote_name: str
    visible_name: str
    label: str
    description: str
    parameters: dict[str, Any]
    execution_mode: str


@dataclass(frozen=True)
class ServerCatalog:
    server_name: str
    tools: tuple[NativeToolSpec, ...]
    schema_bytes: int
    diagnostics: tuple[str, ...]
    rejected: bool = False


@dataclass(frozen=True)
class SessionCatalogPlan:
    accepted: tuple[ServerCatalog, ...]
    rejected: tuple[tuple[str, str], ...]
    tool_count: int
    schema_bytes: int


def native_tool_name(server_name: str, remote_name: str) -> str:
    preferred = f"mcp__{server_name}__{remote_name}"
    if (
        len(preferred) <= MAX_VISIBLE_NAME
        and SAFE_NAME.fullmatch(server_name)
        and SAFE_NAME.fullmatch(remote_name)
    ):
        return preferred
    server = _normalized_segment(server_name, "server")
    tool = _normalized_segment(remote_name, "tool")
    digest = hashlib.sha256(f"{server_name}\0{remote_name}".encode("utf-8")).hexdigest()[:10]
    suffix = f"__{digest}"
    readable = f"mcp__{server}__{tool}"
    return readable[: MAX_VISIBLE_NAME - len(suffix)].rstrip("_-") + suffix


def _normalized_segment(value: str, fallback: str) -> str:
    normalized = UNSAFE_RUN.sub("_", value).strip("_-")
    return normalized or fallback


async def load_remote_tools(
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


def build_server_catalog(
    server: ServerConfig,
    remote_tools: Collection[Tool],
    *,
    reserved_names: Collection[str],
) -> ServerCatalog:
    diagnostics: list[str] = []
    raw_names = {tool.name for tool in remote_tools}
    if server.include_tools is not None:
        for name in server.include_tools:
            if name not in raw_names:
                _add_diagnostic(diagnostics, f'Configured included tool "{name}" was not advertised.')
    for name in server.exclude_tools:
        if name not in raw_names or (server.include_tools is not None and name not in server.include_tools):
            _add_diagnostic(diagnostics, f'Configured exclusion "{name}" is redundant.')

    candidates: list[NativeToolSpec] = []
    schema_bytes = 0
    seen_remote_names: set[str] = set()
    visible_names = set(reserved_names)
    for remote in remote_tools:
        if remote.name in seen_remote_names:
            _add_diagnostic(diagnostics, f'Duplicate remote tool "{remote.name}" was skipped.')
            continue
        seen_remote_names.add(remote.name)
        if not _selected(server, remote.name):
            continue

        visible_name = native_tool_name(server.name, remote.name)
        if visible_name in visible_names:
            _add_diagnostic(
                diagnostics,
                f'Native name for "{remote.name}" collides with a reserved or registered tool and was skipped.',
            )
            continue

        parameters = dict(remote.input_schema)
        try:
            compile_tool_schema(parameters)
            serialized = json.dumps(
                parameters,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except Exception as error:  # noqa: BLE001 - catalog isolates malformed third-party schemas.
            _add_diagnostic(
                diagnostics,
                f'Invalid input schema for "{remote.name}" was skipped ({type(error).__name__}).',
            )
            continue
        if len(serialized) > MAX_SCHEMA_BYTES:
            _add_diagnostic(diagnostics, f'Input schema for "{remote.name}" exceeds 64 KiB and was skipped.')
            continue

        visible_names.add(visible_name)
        schema_bytes += len(serialized)
        candidates.append(
            NativeToolSpec(
                server_name=server.name,
                remote_name=remote.name,
                visible_name=visible_name,
                label=f"{server.name} / {remote.name}",
                description=_native_description(server.name, remote),
                parameters=parameters,
                execution_mode=(
                    "parallel"
                    if remote.annotations is not None and remote.annotations.read_only_hint is True
                    else "sequential"
                ),
            )
        )

    if len(candidates) > MAX_SERVER_TOOLS:
        _add_diagnostic(diagnostics, "Server catalog exceeds 64 tools and was rejected.")
        return ServerCatalog(server.name, (), 0, tuple(diagnostics), rejected=True)
    if schema_bytes > MAX_SERVER_SCHEMA_BYTES:
        _add_diagnostic(diagnostics, "Server catalog schema budget exceeds 256 KiB and was rejected.")
        return ServerCatalog(server.name, (), 0, tuple(diagnostics), rejected=True)
    return ServerCatalog(server.name, tuple(candidates), schema_bytes, tuple(diagnostics))


def _selected(server: ServerConfig, remote_name: str) -> bool:
    included = server.include_tools is None or remote_name in server.include_tools
    return included and remote_name not in server.exclude_tools


def _native_description(server_name: str, tool: Tool) -> str:
    remote_description = str(tool.description or "No description provided.")
    return _bounded_utf8(f'MCP server "{server_name}": {remote_description}', MAX_DESCRIPTION_BYTES)


def _bounded_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _add_diagnostic(diagnostics: list[str], message: str) -> None:
    if len(diagnostics) >= MAX_DIAGNOSTICS:
        return
    diagnostics.append(_bounded_utf8(message, MAX_DIAGNOSTIC_BYTES))


def admit_session_catalogs(catalogs: Collection[ServerCatalog]) -> SessionCatalogPlan:
    accepted: list[ServerCatalog] = []
    rejected: list[tuple[str, str]] = []
    tool_count = 0
    schema_bytes = 0
    for catalog in sorted(catalogs, key=lambda item: item.server_name):
        if catalog.rejected:
            reason = catalog.diagnostics[-1] if catalog.diagnostics else "server catalog was rejected"
            rejected.append((catalog.server_name, _bounded_utf8(reason, MAX_DIAGNOSTIC_BYTES)))
            continue
        if tool_count + len(catalog.tools) > MAX_SESSION_TOOLS:
            rejected.append((catalog.server_name, "session tool budget exceeds 128 tools"))
            continue
        if schema_bytes + catalog.schema_bytes > MAX_SESSION_SCHEMA_BYTES:
            rejected.append((catalog.server_name, "session schema budget exceeds 512 KiB"))
            continue
        accepted.append(catalog)
        tool_count += len(catalog.tools)
        schema_bytes += catalog.schema_bytes
    return SessionCatalogPlan(tuple(accepted), tuple(rejected), tool_count, schema_bytes)
