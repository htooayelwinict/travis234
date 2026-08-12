"""Installed-distribution smoke for native MCP tool registration."""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
from pathlib import Path

from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from travis.app import CodingApp
from travis.coding_agent.automation import run_print_mode
from travis.coding_agent.config import get_agent_dir
from travis.coding_agent.model_registry import ModelRegistry


def run_native_mcp_smoke(workspace: Path, fixture_server: Path) -> dict[str, object]:
    root = Path(workspace).expanduser().resolve()
    fixture = Path(fixture_server).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not fixture.is_file():
        raise RuntimeError(f"MCP fixture server is unavailable: {fixture}")

    secret = secrets.token_hex(24)
    os.environ["FIXTURE_TOKEN"] = secret
    agent_dir = Path(get_agent_dir())
    _write_global_config(agent_dir, fixture)
    _write_installed_extension_entry(agent_dir)
    seen_tools: list[str] = []
    calls = 0

    def script(model, context):
        nonlocal calls
        calls += 1
        seen_tools[:] = [tool.name for tool in context.tools or []]
        if calls == 1:
            return tool_call_response_events(
                model,
                "mcp__fixture__echo",
                {"text": "installed-native-mcp"},
            )
        return text_response_events(model, "installed-native-mcp")

    output = io.StringIO()
    with _mcp_only_app(root, agent_dir, script) as app:
        if run_print_mode(app, "use the fixture echo tool", output) != 0:
            raise RuntimeError("native MCP print smoke failed")
        active_names = app.session.get_active_tool_names()
        serialized_session = json.dumps(app.session.agent.state.messages, default=str)

    evidence = {
        "activeNames": active_names,
        "providerTools": seen_tools,
        "text": output.getvalue().strip(),
    }
    if not active_names or active_names[0] != "mcp" or "mcp__fixture__echo" not in active_names:
        raise RuntimeError(f"native MCP tools were not active: {active_names}")
    if seen_tools != active_names:
        raise RuntimeError(f"provider tool list differed from active tools: {seen_tools}")
    if any(name in active_names for name in ("read", "bash", "edit", "write")):
        raise RuntimeError(f"MCP-only smoke exposed builtin tools: {active_names}")
    serialized_evidence = json.dumps(evidence, sort_keys=True)
    if evidence["text"] != "installed-native-mcp":
        raise RuntimeError(f"native MCP result differed: {evidence['text']!r}")
    if secret in serialized_evidence or secret in serialized_session:
        raise RuntimeError("native MCP credential boundary failed")
    return evidence


def _write_global_config(agent_dir: Path, fixture_server: Path) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fixture": {
                        "command": os.environ.get("PYTHON", os.sys.executable),
                        "args": [str(fixture_server)],
                        "env": {"FIXTURE_TOKEN": "${FIXTURE_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _write_installed_extension_entry(agent_dir: Path) -> None:
    extension_path = agent_dir / "extensions" / "mcp_adapter.py"
    extension_path.parent.mkdir(parents=True, exist_ok=True)
    extension_path.write_text(
        "from travis234_mcp_adapter.extension import extension\n\n__all__ = ['extension']\n",
        encoding="utf-8",
    )


class _McpOnlyApp:
    def __init__(self, workspace: Path, agent_dir: Path, script) -> None:
        registry = ModelRegistry.in_memory()
        registry.runtime.clear_providers()
        registry.runtime.set_provider(create_faux_provider(script))
        self.app = CodingApp(
            cwd=str(workspace),
            agent_dir=str(agent_dir),
            model=faux_model(),
            enable_tui=False,
            project_trust_override=False,
            model_registry=registry,
            allowed_tool_names=["mcp"],
            additional_active_tool_names=["mcp"],
            additional_extension_paths=[str(agent_dir / "extensions" / "mcp_adapter.py")],
        )

    def __enter__(self) -> CodingApp:
        return self.app

    def __exit__(self, *_args: object) -> None:
        self.app.close()


def _mcp_only_app(workspace: Path, agent_dir: Path, script) -> _McpOnlyApp:
    return _McpOnlyApp(workspace, agent_dir, script)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--fixture-server", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            run_native_mcp_smoke(args.workspace, args.fixture_server),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
