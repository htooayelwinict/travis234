"""Installed-wheel smoke for the bundled Ghost computer-use MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import psutil

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_ROOT = REPOSITORY_ROOT / "packages" / "travis234-mcp-adapter"
GHOST_ROOT = REPOSITORY_ROOT / "packages" / "travis234-ghost-mcp"
MAX_RESULT_BYTES = 16_384
MODULE_NAME = "evals.bundled_ghost_mcp_smoke"


def run_bundled_ghost_smoke(workspace: Path) -> dict[str, object]:
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    home = workspace / "home"
    home.mkdir(exist_ok=True)
    environment = {
        name: os.environ[name]
        for name in ("PATH", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
        if name in os.environ
    }
    environment.update(
        {
            "HOME": str(home),
            "UV_CACHE_DIR": str(workspace / "uv-cache"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", MODULE_NAME, "--worker", str(workspace)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"bundled Ghost smoke worker failed with exit code {completed.returncode}"
        )
    encoded = completed.stdout.encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise RuntimeError("bundled Ghost smoke worker exceeded its JSON output bound")
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise TypeError("bundled Ghost smoke worker returned invalid JSON")
    return result


def _build_wheel(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--clear",
            "-o",
            str(destination),
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"wheel build failed for {source.name}")
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"wheel build for {source.name} produced {len(wheels)} artifacts")
    return wheels[0]


def _worker(workspace: Path) -> dict[str, object]:
    import travis.coding_agent.package_manager as package_manager_module
    from travis.coding_agent.extensions import ExtensionRunner
    from travis.coding_agent.package_manager import DefaultPackageManager
    from travis.coding_agent.resource_loader import DefaultResourceLoader
    from travis.coding_agent.settings_manager import SettingsManager

    wheels = workspace / "wheels"
    adapter_wheel = _build_wheel(ADAPTER_ROOT, wheels / "adapter")
    ghost_wheel = _build_wheel(GHOST_ROOT, wheels / "ghost")
    os.environ["UV_FIND_LINKS"] = str(adapter_wheel.parent)
    original_find_spec = package_manager_module.importlib.util.find_spec
    package_manager_module.importlib.util.find_spec = (
        lambda name: None if name == "pip" else original_find_spec(name)
    )

    home = workspace / "home"
    project = workspace / "project"
    project.mkdir(exist_ok=True)
    settings = SettingsManager.in_memory()
    manager = DefaultPackageManager(
        cwd=str(project),
        agent_dir=str(workspace / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )
    manager.install(
        f"travis234-mcp-adapter @ {adapter_wheel.as_uri()}",
        scope="global",
    )
    manager.install(
        f"travis234-ghost-mcp @ {ghost_wheel.as_uri()}",
        scope="global",
    )
    resolved = manager.resolve()
    ghost_extension = next(
        item for item in resolved.extensions if Path(item.path).name == "ghost_mcp.py"
    )
    install_root = Path(ghost_extension.path).resolve().parents[1]
    binary = install_root / "travis234_ghost_mcp" / "bin" / "ghost"

    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(workspace / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )
    loader.reload()
    runner: ExtensionRunner = loader.get_extensions()["runtime"]
    runner.bind_core(context_actions={"is_project_trusted": lambda: True})
    tool = runner.get_all_registered_tools()[0].definition
    protocol = asyncio.run(_exercise_protocol(runner, tool, binary))
    return {
        "server": "ghost-os",
        "tool_count": protocol["tool_count"],
        "configured": bool(list(home.rglob("mcp.json"))),
        "child_reaped": protocol["child_reaped"],
        "legacy_state_created": (home / ".ghost-os").exists(),
    }


async def _exercise_protocol(
    runner: Any,
    tool: Any,
    binary: Path,
) -> dict[str, object]:
    await runner.async_emit({"type": "session_start"})
    status = await tool.execute("status", {}, None, None, None)
    if "ghost-os: disconnected" not in status.content[0].text:
        raise RuntimeError("bundled Ghost status was not connection-free")
    catalog = await tool.execute("list", {"server": "ghost-os"}, None, None, None)
    match = re.search(r'MCP tools on "ghost-os" \((\d+)\)', catalog.content[0].text)
    if match is None:
        raise RuntimeError("bundled Ghost catalog response was malformed")
    call = await tool.execute(
        "recipes",
        {"server": "ghost-os", "tool": "ghost_recipes", "args": {}},
        None,
        None,
        None,
    )
    if call.details["travis234Mcp"]["isError"]:
        raise RuntimeError("bundled Ghost representative tool call failed")
    child_pid = await _wait_for_child(binary)
    await runner.async_emit({"type": "session_shutdown"})
    await runner.async_emit({"type": "session_shutdown"})
    await _wait_for_exit(child_pid)
    return {
        "tool_count": int(match.group(1)),
        "child_reaped": not psutil.pid_exists(child_pid),
    }


async def _wait_for_child(binary: Path) -> int:
    expected = str(binary.resolve())
    for _attempt in range(100):
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                command = process.info["cmdline"] or []
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if command and str(Path(command[0]).resolve()) == expected:
                return int(process.info["pid"])
        await asyncio.sleep(0.05)
    raise RuntimeError("bundled Ghost child did not start")


async def _wait_for_exit(pid: int) -> None:
    for _attempt in range(100):
        if not psutil.pid_exists(pid):
            return
        await asyncio.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.worker is not None:
            result = _worker(args.worker.resolve())
        else:
            with tempfile.TemporaryDirectory(prefix="travis234-ghost-smoke-") as raw:
                result = run_bundled_ghost_smoke(Path(raw))
    except Exception as error:  # noqa: BLE001 - CLI must return bounded error JSON.
        print(json.dumps({"error": type(error).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
