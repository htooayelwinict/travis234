from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from travis.ai.providers.faux import faux_model
from travis.coding_agent import AgentSession
from travis.coding_agent.extensions import ExtensionRunner
from travis234_mcp_adapter.extension import extension


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
GHOST = PACKAGE_ROOT.parents[1] / ".disposable/ghost-os/.build/arm64-apple-macosx/debug/ghost"
pytestmark = pytest.mark.skipif(
    not GHOST.is_file(),
    reason="disposable Ghost OS binary is unavailable",
)


def test_ghost_catalog_registers_29_native_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = home / ".travis234" / "agent" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ghost-os": {
                        "command": str(GHOST),
                        "args": ["mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    runner = ExtensionRunner(cwd=str(workspace))
    state = extension(runner)
    session = AgentSession(
        cwd=str(workspace),
        model=faux_model(),
        extension_runner=runner,
        allowed_tool_names=["mcp"],
        active_tool_names=["mcp"],
    )

    asyncio.run(runner.async_emit({"type": "session_start"}))

    native_names = list(state.native_names)
    active_names = session.get_active_tool_names()
    assert len(native_names) == 29
    assert "mcp__ghost-os__ghost_context" in native_names
    assert "mcp__ghost-os__ghost_screenshot" in native_names
    assert active_names == ["mcp", *native_names]
    assert all(name == "mcp" or name.startswith("mcp__ghost-os__") for name in active_names)

    status = session.get_tool_definition("mcp").execute("status", {}, None, None, None)
    assert "Native MCP tools (29)" in status.content[0].text
    asyncio.run(runner.async_emit({"type": "session_shutdown"}))
