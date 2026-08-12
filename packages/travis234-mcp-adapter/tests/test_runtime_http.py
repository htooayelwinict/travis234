from __future__ import annotations

from pathlib import Path

import pytest

from mcp.types import TextContent
from travis234_mcp_adapter.config import ServerConfig
from travis234_mcp_adapter.runtime import McpRuntime


def _runtime(tmp_path: Path, url: str, *, timeout_ms: int | None = None) -> McpRuntime:
    server = ServerConfig(
        name="remote",
        source_path=tmp_path / "mcp.json",
        url=url,
        headers={"Authorization": "Bearer ${REMOTE_TOKEN}"},
        request_timeout_ms=timeout_ms,
    )
    return McpRuntime({"remote": server}, {"REMOTE_TOKEN": "http-secret-value"})


def _text(result) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


@pytest.mark.anyio
async def test_http_is_lazy_sends_referenced_header_and_closes(
    tmp_path: Path,
    mcp_http_server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _runtime(tmp_path, mcp_http_server.url)
    assert mcp_http_server.probe == {"requests": 0, "authorized": False}

    connected = await runtime.connect("remote", None)
    assert [tool.name for tool in (await connected.list_tools(None)).tools] == [
        "echo",
        "configured_secret_name",
        "slow",
        "large_output",
        "controlled_error",
        "emit_tools_changed",
    ]
    assert connected.metadata.protocol_version
    assert connected.metadata.instructions == "Use fixture tools only for deterministic tests."
    assert _text(await connected.call_tool("echo", {"text": "http-sentinel"}, None)) == "http-sentinel"
    assert mcp_http_server.probe["requests"] > 0
    assert mcp_http_server.probe["authorized"] is True

    await runtime.close()
    await runtime.close()

    captured = capsys.readouterr()
    assert "http-secret-value" not in captured.out
    assert "http-secret-value" not in captured.err


@pytest.mark.anyio
async def test_http_timeout_keeps_connection_usable(
    tmp_path: Path,
    mcp_http_server,
) -> None:
    runtime = _runtime(tmp_path, mcp_http_server.url, timeout_ms=2_000)
    connected = await runtime.connect("remote", None)

    with pytest.raises(TimeoutError, match="remote.*call_tool.*2000"):
        await connected.call_tool("slow", {"delay_ms": 5_000}, None)

    reconnected = await runtime.connect("remote", None)
    assert reconnected is not connected
    assert _text(await reconnected.call_tool("echo", {"text": "after-http-timeout"}, None)) == "after-http-timeout"
    await runtime.close()
