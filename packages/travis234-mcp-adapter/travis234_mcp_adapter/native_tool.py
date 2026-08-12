from __future__ import annotations

import asyncio
from typing import Protocol

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.tools.types import ToolDefinition

from travis234_mcp_adapter.catalog import NativeToolSpec
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.results import convert_call_result
from travis234_mcp_adapter.runtime import McpRuntime


class NativeCallState(Protocol):
    generation: int
    runtime: McpRuntime | None
    spills: SpillRegistry


def create_native_definition(state: NativeCallState, spec: NativeToolSpec) -> ToolDefinition:
    async def execute(_call_id, args, signal=None, _on_update=None, _ctx=None):
        generation = state.generation
        runtime = state.runtime
        if runtime is None:
            return _native_error(spec, "MCP runtime is not active")
        try:
            connected = await runtime.connect(spec.server_name, signal)
            result = await connected.call_tool(spec.remote_name, dict(args), signal)
            if state.generation != generation:
                raise asyncio.CancelledError
            converted = convert_call_result(result, state.spills)
            _annotate_native_result(converted, spec)
            return converted
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            if state.generation != generation:
                raise asyncio.CancelledError
            return _native_error(spec, str(error))
        except Exception as error:  # noqa: BLE001 - remote failures are deliberately shaped.
            if state.generation != generation:
                raise asyncio.CancelledError
            return _native_error(
                spec,
                f'MCP server "{spec.server_name}" call failed ({type(error).__name__}).',
            )

    return ToolDefinition(
        name=spec.visible_name,
        label=spec.label,
        description=spec.description,
        parameters=spec.parameters,
        execute=execute,
        execution_mode=spec.execution_mode,
        activation_group="mcp",
    )


def _native_error(spec: NativeToolSpec, message: str) -> AgentToolResult:
    marker = {
        "operation": "call",
        "isError": True,
        "visibleName": spec.visible_name,
        "server": spec.server_name,
        "remoteName": spec.remote_name,
        "spilled": False,
    }
    return AgentToolResult(
        content=[TextContent(text=_bounded_utf8(message, 4 * 1024))],
        details={"travis234Mcp": marker},
    )


def _annotate_native_result(result: AgentToolResult, spec: NativeToolSpec) -> None:
    details = result.details if isinstance(result.details, dict) else {}
    marker = details.get("travis234Mcp")
    if not isinstance(marker, dict):
        marker = {}
        details["travis234Mcp"] = marker
    marker.update(
        {
            "visibleName": spec.visible_name,
            "server": spec.server_name,
            "remoteName": spec.remote_name,
        }
    )
    result.details = details


def _bounded_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")
