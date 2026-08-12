from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest
from conftest import PACKAGE_ROOT
from travis234_mcp_adapter import packaged_servers
from travis234_mcp_adapter.output_guard import MAX_INLINE_BYTES

from travis.agent.types import AbortSignal
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.package_manager import DefaultPackageManager
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.settings_manager import SettingsManager

REPOSITORY_ROOT = PACKAGE_ROOT.parent.parent
ADAPTER_ROOT = REPOSITORY_ROOT / "packages" / "travis234-mcp-adapter"


@pytest.fixture(scope="module")
def protocol_wheels(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    output = tmp_path_factory.mktemp("ghost-protocol-wheels")
    adapter_output = output / "adapter"
    ghost_output = output / "ghost"
    adapter_output.mkdir()
    ghost_output.mkdir()
    for source, destination in (
        (ADAPTER_ROOT, adapter_output),
        (PACKAGE_ROOT, ghost_output),
    ):
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--clear",
                "-o",
                str(destination),
                str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    return next(adapter_output.glob("*.whl")), next(ghost_output.glob("*.whl"))


@pytest.fixture
async def installed_ghost_runtime(
    protocol_wheels: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    adapter_wheel, ghost_wheel = protocol_wheels
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("UV_FIND_LINKS", str(adapter_wheel.parent))
    monkeypatch.setattr(packaged_servers, "_REGISTRY", {})
    monkeypatch.setattr(
        "travis.coding_agent.package_manager.importlib.util.find_spec",
        lambda name: None if name == "pip" else __import__(name).__spec__,
    )

    settings = SettingsManager.in_memory()
    manager = DefaultPackageManager(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
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

    for name in tuple(sys.modules):
        if name == "travis234_ghost_mcp" or name.startswith("travis234_ghost_mcp."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )
    loader.reload()
    runner: ExtensionRunner = loader.get_extensions()["runtime"]
    runner.bind_core(context_actions={"is_project_trusted": lambda: True})
    proxy = runner.get_all_registered_tools()[0].definition

    try:
        yield runner, proxy, binary, home
    finally:
        await runner.async_emit({"type": "session_shutdown"})


@pytest.mark.anyio
async def test_installed_embedded_server_protocol_and_lifecycle(
    installed_ghost_runtime,
) -> None:
    runner, proxy, binary, home = installed_ghost_runtime
    assert binary.is_file() and os.access(binary, os.X_OK)

    await runner.async_emit({"type": "session_start"})
    status = await proxy.execute("status", {}, None, None, None)
    assert "ghost-os: disconnected" in status.content[0].text

    catalog = await proxy.execute("list", {"server": "ghost-os"}, None, None, None)
    assert 'MCP tools on "ghost-os" (29)' in catalog.content[0].text
    assert "ghost_context" in catalog.content[0].text
    assert "ghost_recipes" in catalog.content[0].text

    child_pid = await _wait_for_child(binary)
    recipes = await proxy.execute(
        "recipes",
        {"server": "ghost-os", "tool": "ghost_recipes", "args": {}},
        None,
        None,
        None,
    )
    assert recipes.details["travis234Mcp"]["isError"] is False
    assert len(recipes.content[0].text.encode("utf-8")) <= MAX_INLINE_BYTES

    doctor = await asyncio.create_subprocess_exec(
        str(binary),
        "doctor",
        "--json",
        cwd=binary.parent,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    doctor_stdout, _doctor_stderr = await asyncio.wait_for(
        doctor.communicate(), timeout=30
    )
    assert doctor.returncode in {0, 2}
    permission_status = json.loads(doctor_stdout)
    if not permission_status["screenRecordingGranted"]:
        screenshot = await proxy.execute(
            "screenshot",
            {"server": "ghost-os", "tool": "ghost_screenshot", "args": {}},
            None,
            None,
            None,
        )
        assert "/ghost-setup" in screenshot.content[0].text
        assert len(screenshot.content[0].text.encode("utf-8")) <= MAX_INLINE_BYTES

    signal = AbortSignal()
    waiting = asyncio.create_task(
        proxy.execute(
            "wait",
            {
                "server": "ghost-os",
                "tool": "ghost_wait",
                "args": {
                    "condition": "titleContains",
                    "value": "travis234-never-present",
                    "timeout": 60,
                    "interval": 0.1,
                },
            },
            signal,
            None,
            None,
        )
    )
    await asyncio.sleep(0.2)
    signal.abort()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    await runner.async_emit({"type": "session_shutdown"})
    await runner.async_emit({"type": "session_shutdown"})
    await _wait_for_exit(child_pid)
    assert not psutil.pid_exists(child_pid)
    assert not list(home.rglob("mcp.json"))
    assert not (home / ".ghost-os").exists()
    assert (home / ".travis234" / "ghost-mcp" / "recipes").is_dir()


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
    raise AssertionError("embedded Ghost MCP child did not start")


async def _wait_for_exit(pid: int) -> None:
    for _attempt in range(100):
        if not psutil.pid_exists(pid):
            return
        await asyncio.sleep(0.05)
