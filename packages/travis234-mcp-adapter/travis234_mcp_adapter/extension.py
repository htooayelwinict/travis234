from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

from travis234_mcp_adapter.config import ConfigError, LoadedConfig, load_config
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.proxy_tool import create_proxy_definition
from travis234_mcp_adapter.runtime import McpRuntime


def _empty_config() -> LoadedConfig:
    return LoadedConfig(servers={}, sources=(), ignored_project_sources=())


@dataclass
class ExtensionState:
    config: LoadedConfig = field(default_factory=_empty_config)
    config_error: str | None = None
    generation: int = 0
    session_started: bool = False
    runtime: McpRuntime | None = None
    catalogs: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    spills: SpillRegistry = field(default_factory=SpillRegistry)

    async def on_session_start(self, _event, ctx) -> None:
        self.generation += 1
        if self.runtime is not None:
            await self.runtime.close()
        self.session_started = True
        self.config = _empty_config()
        self.config_error = None
        self.runtime = None
        self.catalogs.clear()
        self.spills.cleanup()
        self.spills = SpillRegistry()
        try:
            self.config = load_config(
                Path(ctx.cwd),
                Path.home(),
                ctx.is_project_trusted(),
            )
        except ConfigError as error:
            self.config_error = _bounded_error(str(error))
        else:
            self.runtime = McpRuntime(self.config.servers, lambda: os.environ)

    async def on_session_shutdown(self, _event, _ctx) -> None:
        self.generation += 1
        if self.runtime is not None:
            await self.runtime.close()
        self.session_started = False
        self.config = _empty_config()
        self.config_error = None
        self.runtime = None
        self.catalogs.clear()
        self.spills.cleanup()

    def on_tool_result(self, event, _ctx):
        details = event.get("details")
        if not isinstance(details, dict):
            return None
        marker = details.get("travis234Mcp")
        if not isinstance(marker, dict) or not isinstance(marker.get("isError"), bool):
            return None
        return {"isError": marker["isError"]}


def extension(travis) -> None:
    state = ExtensionState()
    travis.register_tool(create_proxy_definition(state))
    travis.on("session_start", state.on_session_start)
    travis.on("session_shutdown", state.on_session_shutdown)
    travis.on("tool_result", state.on_tool_result)


def _bounded_error(message: str) -> str:
    encoded = message.encode("utf-8")
    if len(encoded) <= 4_000:
        return message
    return encoded[:3_980].decode("utf-8", errors="ignore") + "…"
