from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from travis234_mcp_adapter.catalog import (
    ServerCatalog,
    SessionCatalogPlan,
    admit_session_catalogs,
    build_server_catalog,
    load_remote_tools,
)
from travis234_mcp_adapter.config import ConfigError, LoadedConfig, ServerConfig, load_config
from travis234_mcp_adapter.native_tool import create_native_definition
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.runtime import McpRuntime
from travis234_mcp_adapter.status_tool import StatusSnapshot, create_status_definition


DISCOVERY_CONCURRENCY = 4
DISCOVERY_TIMEOUT_SECONDS = 30.0


def _empty_config() -> LoadedConfig:
    return LoadedConfig(servers={}, sources=(), ignored_project_sources=())


@dataclass
class ExtensionState:
    travis: Any
    config: LoadedConfig = field(default_factory=_empty_config)
    config_error: str | None = None
    runtime: McpRuntime | None = None
    generation: int = 0
    native_names: list[str] = field(default_factory=list)
    catalogs: dict[str, ServerCatalog] = field(default_factory=dict)
    diagnostics: dict[str, tuple[str, ...]] = field(default_factory=dict)
    instructions: dict[str, str] = field(default_factory=dict)
    spills: SpillRegistry = field(default_factory=SpillRegistry)

    async def on_session_start(self, _event, ctx) -> None:
        generation = await self._reset_generation()
        try:
            self.config = load_config(
                Path(ctx.cwd),
                Path.home(),
                ctx.is_project_trusted(),
            )
        except ConfigError as error:
            self.config_error = _bounded_error(str(error))
            self._publish_status(generation)
            return

        if "mcp" not in self.travis.get_active_tools():
            self._publish_status(generation)
            return

        self.runtime = McpRuntime(self.config.servers, lambda: os.environ)
        await self.discover_active_servers(ctx, generation=generation)

    async def discover_active_servers(self, _ctx: Any, *, generation: int | None = None) -> None:
        generation = self.generation if generation is None else generation
        if self.runtime is None or not self.config.servers:
            self._publish_status(generation)
            return

        semaphore = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
        reserved_names = self._reserved_names()

        async def discover(server: ServerConfig) -> ServerCatalog:
            async with semaphore:
                return await self._discover_one(server, generation, reserved_names)

        tasks = {
            asyncio.create_task(discover(server), name=f"travis234-mcp-discover-{name}"): name
            for name, server in sorted(self.config.servers.items())
        }
        done, pending = await asyncio.wait(tasks, timeout=DISCOVERY_TIMEOUT_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if generation != self.generation:
            return
        if pending:
            for task in pending:
                name = tasks[task]
                self.diagnostics[name] = ("Discovery timed out within the 30-second startup budget.",)

        catalogs: list[ServerCatalog] = []
        for task in sorted(done, key=lambda item: tasks[item]):
            name = tasks[task]
            try:
                catalog = task.result()
            except asyncio.CancelledError:
                self.diagnostics[name] = ("Discovery was cancelled.",)
            except Exception as error:  # noqa: BLE001 - isolate one configured server.
                self.diagnostics[name] = (
                    f'Discovery failed for MCP server "{name}" ({type(error).__name__}).',
                )
            else:
                catalogs.append(catalog)
                if catalog.diagnostics:
                    self.diagnostics[name] = catalog.diagnostics

        plan = admit_session_catalogs(catalogs)
        for name, reason in plan.rejected:
            self.diagnostics[name] = (*self.diagnostics.get(name, ()), reason)
        self._apply_catalog_plan(plan, generation)

    async def _discover_one(
        self,
        server: ServerConfig,
        generation: int,
        reserved_names: set[str],
    ) -> ServerCatalog:
        runtime = self.runtime
        if runtime is None:
            raise RuntimeError("MCP runtime is not active")
        connected = await runtime.connect(server.name, None)
        if generation != self.generation:
            raise asyncio.CancelledError
        tools = await load_remote_tools(connected, None)
        if generation != self.generation:
            raise asyncio.CancelledError
        catalog = build_server_catalog(server, tools, reserved_names=reserved_names)
        if not catalog.rejected and catalog.tools and connected.metadata.instructions:
            self.instructions[server.name] = connected.metadata.instructions
        return catalog

    def _apply_catalog_plan(self, plan: SessionCatalogPlan, generation: int) -> None:
        if generation != self.generation:
            return
        accepted = {catalog.server_name: catalog for catalog in plan.accepted}
        new_names = [tool.visible_name for catalog in plan.accepted for tool in catalog.tools]
        with self.travis.tool_registration_batch():
            for name in tuple(self.native_names):
                self.travis.unregister_tool(name)
            self.native_names.clear()
            for catalog in plan.accepted:
                for spec in catalog.tools:
                    self.travis.register_tool(create_native_definition(self, spec))
                    self.native_names.append(spec.visible_name)
            if generation != self.generation:
                for name in tuple(self.native_names):
                    self.travis.unregister_tool(name)
                self.native_names.clear()
                return
            self.catalogs = accepted
            self.instructions = {
                name: instruction
                for name, instruction in self.instructions.items()
                if name in accepted and accepted[name].tools
            }
            self._replace_status_definition()
        assert self.native_names == new_names

    async def _reset_generation(self) -> int:
        self.generation += 1
        generation = self.generation
        runtime = self.runtime
        self.runtime = None
        with self.travis.tool_registration_batch():
            self._unregister_native_tools()
        if runtime is not None:
            await runtime.close()
        self.config = _empty_config()
        self.config_error = None
        self.catalogs.clear()
        self.diagnostics.clear()
        self.instructions.clear()
        self.spills.cleanup()
        self.spills = SpillRegistry()
        return generation

    def _unregister_native_tools(self) -> None:
        for name in tuple(self.native_names):
            self.travis.unregister_tool(name)
        self.native_names.clear()

    def _reserved_names(self) -> set[str]:
        owned = set(self.native_names)
        reserved: set[str] = set()
        for registered in self.travis.get_all_registered_tools():
            name = registered.definition.name
            if name != "mcp" and name not in owned:
                reserved.add(name)
        return reserved

    def _status_snapshot(self) -> StatusSnapshot:
        connected = (
            tuple(name for name in sorted(self.config.servers) if self.runtime.is_connected(name))
            if self.runtime is not None
            else ()
        )
        diagnostics = tuple(
            item
            for name in sorted(self.diagnostics)
            for item in self.diagnostics[name]
        )
        return StatusSnapshot(
            configured_servers=tuple(sorted(self.config.servers)),
            connected_servers=connected,
            native_names=tuple(self.native_names),
            diagnostics=diagnostics,
            ignored_project_sources=len(self.config.ignored_project_sources),
            instructions=tuple(
                (name, self.instructions[name])
                for name in sorted(self.instructions)
                if any(catalog.server_name == name and catalog.tools for catalog in self.catalogs.values())
            ),
            config_error=self.config_error,
        )

    def _replace_status_definition(self) -> None:
        self.travis.unregister_tool("mcp")
        self.travis.register_tool(create_status_definition(self._status_snapshot()))

    def _publish_status(self, generation: int) -> None:
        if generation != self.generation:
            return
        with self.travis.tool_registration_batch():
            self._replace_status_definition()

    async def on_session_shutdown(self, _event, _ctx) -> None:
        generation = await self._reset_generation()
        self._publish_status(generation)

    async def on_before_agent_start(self, _event, _ctx) -> None:
        runtime = self.runtime
        if runtime is None:
            return None
        generation = self.generation
        dirty_servers = runtime.take_dirty_servers()
        notification_errors = runtime.take_notification_errors()
        for name, error_name in notification_errors:
            self.diagnostics[name] = (
                *self.diagnostics.get(name, ()),
                f'Tool-list listener for MCP server "{name}" stopped ({error_name}).',
            )
        if not dirty_servers:
            if notification_errors:
                self._publish_status(generation)
            return None

        catalogs = dict(self.catalogs)
        reserved_names = self._reserved_names()
        for name in dirty_servers:
            catalogs.pop(name, None)
            self.instructions.pop(name, None)
            server = self.config.servers.get(name)
            if server is None:
                self.diagnostics[name] = ("Tool-list change ignored for an unconfigured server.",)
                continue
            try:
                catalog = await self._discover_one(server, generation, reserved_names)
            except asyncio.CancelledError:
                if generation != self.generation:
                    return None
                raise
            except Exception as error:  # noqa: BLE001 - remove stale tools and shape server failure.
                self.diagnostics[name] = (
                    f'Reconciliation failed for MCP server "{name}" ({type(error).__name__}).',
                )
            else:
                catalogs[name] = catalog
                if catalog.diagnostics:
                    self.diagnostics[name] = catalog.diagnostics
                else:
                    self.diagnostics.pop(name, None)
            if generation != self.generation:
                return None

        plan = admit_session_catalogs(tuple(catalogs.values()))
        for name, reason in plan.rejected:
            self.diagnostics[name] = (*self.diagnostics.get(name, ()), reason)
        self._apply_catalog_plan(plan, generation)
        return None

    def on_tool_result(self, event, _ctx):
        details = event.get("details")
        if not isinstance(details, dict):
            return None
        marker = details.get("travis234Mcp")
        if not isinstance(marker, dict) or not isinstance(marker.get("isError"), bool):
            return None
        return {"isError": marker["isError"]}


def extension(travis) -> ExtensionState:
    state = ExtensionState(travis=travis)
    travis.register_tool(create_status_definition(state._status_snapshot()))
    travis.on("session_start", state.on_session_start)
    travis.on("session_shutdown", state.on_session_shutdown)
    travis.on("before_agent_start", state.on_before_agent_start)
    travis.on("tool_result", state.on_tool_result)
    return state


def _bounded_error(message: str) -> str:
    encoded = message.encode("utf-8")
    if len(encoded) <= 4_000:
        return message
    return encoded[:3_980].decode("utf-8", errors="ignore") + "…"
