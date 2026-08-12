from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent as McpTextContent
from travis.ai.types import TextContent

from travis234_mcp_adapter.catalog import NativeToolSpec
from travis234_mcp_adapter.native_tool import create_native_definition
from travis234_mcp_adapter.output_guard import SpillRegistry


def _spec(*, execution_mode: str = "sequential") -> NativeToolSpec:
    return NativeToolSpec(
        server_name="ghost-os",
        remote_name="ghost_context",
        visible_name="mcp__ghost-os__ghost_context",
        label="ghost-os / ghost_context",
        description="MCP server ghost-os: inspect context",
        parameters={"type": "object", "properties": {}},
        execution_mode=execution_mode,
    )


class FakeRuntime:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = list(outcomes or [CallToolResult(content=[])])
        self.connects: list[str] = []
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def connect(self, server_name: str, _signal):
        self.connects.append(server_name)
        return self

    async def call_tool(self, remote_name: str, arguments: dict[str, object], _signal):
        self.calls.append((self.connects[-1], remote_name, arguments))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.mark.anyio
async def test_native_definition_calls_exact_remote_identity() -> None:
    runtime = FakeRuntime(
        [CallToolResult(content=[McpTextContent(type="text", text="context")])]
    )
    state = SimpleNamespace(generation=4, runtime=runtime, spills=SpillRegistry())
    spec = _spec()

    definition = create_native_definition(state, spec)
    result = await definition.execute("call-1", {"app": "Finder"}, None, None, None)

    assert runtime.calls == [("ghost-os", "ghost_context", {"app": "Finder"})]
    assert definition.activation_group == "mcp"
    assert definition.parameters == spec.parameters
    assert definition.execution_mode == "sequential"
    assert result.details["travis234Mcp"]["visibleName"] == spec.visible_name
    assert result.details["travis234Mcp"]["remoteName"] == "ghost_context"


@pytest.mark.anyio
async def test_native_definition_preserves_parallel_mode_and_remote_is_error() -> None:
    runtime = FakeRuntime(
        [
            CallToolResult(
                content=[McpTextContent(type="text", text="remote failure")],
                isError=True,
            )
        ]
    )
    definition = create_native_definition(
        SimpleNamespace(generation=1, runtime=runtime, spills=SpillRegistry()),
        _spec(execution_mode="parallel"),
    )

    result = await definition.execute("call-1", {}, None, None, None)

    assert definition.execution_mode == "parallel"
    assert result.details["travis234Mcp"]["isError"] is True


@pytest.mark.anyio
async def test_native_definition_shapes_timeout_without_echoing_arguments() -> None:
    runtime = FakeRuntime([TimeoutError('MCP server "ghost-os" call timed out after 10 ms')])
    definition = create_native_definition(
        SimpleNamespace(generation=1, runtime=runtime, spills=SpillRegistry()),
        _spec(),
    )

    result = await definition.execute("call-1", {"token": "credential-value"}, None, None, None)
    text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))

    assert "timed out" in text
    assert "credential-value" not in text
    assert result.details["travis234Mcp"]["isError"] is True
    assert runtime.calls == [("ghost-os", "ghost_context", {"token": "credential-value"})]


@pytest.mark.anyio
async def test_native_definition_propagates_cancellation() -> None:
    runtime = FakeRuntime([asyncio.CancelledError()])
    definition = create_native_definition(
        SimpleNamespace(generation=1, runtime=runtime, spills=SpillRegistry()),
        _spec(),
    )

    with pytest.raises(asyncio.CancelledError):
        await definition.execute("call-1", {}, object(), None, None)


@pytest.mark.anyio
async def test_native_definition_cancels_result_from_changed_generation() -> None:
    state = SimpleNamespace(generation=1, runtime=None, spills=SpillRegistry())

    class GenerationChangingRuntime(FakeRuntime):
        async def call_tool(self, remote_name, arguments, signal):
            result = await super().call_tool(remote_name, arguments, signal)
            state.generation += 1
            return result

    state.runtime = GenerationChangingRuntime([CallToolResult(content=[])])
    definition = create_native_definition(state, _spec())

    with pytest.raises(asyncio.CancelledError):
        await definition.execute("call-1", {}, None, None, None)


@pytest.mark.anyio
async def test_native_definition_later_call_reconnects_without_replaying_timeout() -> None:
    runtime = FakeRuntime(
        [
            TimeoutError('MCP server "ghost-os" call timed out after 10 ms'),
            CallToolResult(content=[McpTextContent(type="text", text="recovered")]),
        ]
    )
    definition = create_native_definition(
        SimpleNamespace(generation=1, runtime=runtime, spills=SpillRegistry()),
        _spec(),
    )

    failed = await definition.execute("call-1", {"attempt": 1}, None, None, None)
    recovered = await definition.execute("call-2", {"attempt": 2}, None, None, None)

    assert failed.details["travis234Mcp"]["isError"] is True
    assert recovered.content[0].text == "recovered"
    assert runtime.connects == ["ghost-os", "ghost-os"]
    assert runtime.calls == [
        ("ghost-os", "ghost_context", {"attempt": 1}),
        ("ghost-os", "ghost_context", {"attempt": 2}),
    ]


@pytest.mark.anyio
async def test_native_definition_reports_inactive_runtime_without_connecting() -> None:
    definition = create_native_definition(
        SimpleNamespace(generation=1, runtime=None, spills=SpillRegistry()),
        _spec(),
    )

    result = await definition.execute("call-1", {}, None, None, None)

    assert result.details["travis234Mcp"]["isError"] is True
    assert "not active" in result.content[0].text
