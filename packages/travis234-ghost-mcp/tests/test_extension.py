from __future__ import annotations

from pathlib import Path

import pytest
import travis234_ghost_mcp.extension as extension_module
from travis234_mcp_adapter import packaged_servers
from travis234_mcp_adapter.packaged_servers import get_packaged_servers

from travis.coding_agent.extensions import ExtensionRunner


def _executable(root: Path) -> Path:
    binary = root / "travis234_ghost_mcp" / "bin" / "ghost"
    binary.parent.mkdir(parents=True)
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    return binary.resolve()


def test_extension_registers_embedded_server_and_commands_without_config_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "package"
    binary = _executable(root)
    monkeypatch.setattr(packaged_servers, "_REGISTRY", {})
    monkeypatch.setattr(extension_module, "package_root", lambda: binary.parents[1])
    monkeypatch.setattr(extension_module, "ghost_binary", lambda: binary)
    runner = ExtensionRunner(cwd=str(tmp_path / "project"))

    extension_module.extension(runner)

    descriptor = get_packaged_servers()["ghost-os"]
    assert descriptor.package_root == binary.parents[1]
    assert descriptor.command == binary
    assert descriptor.args == ("mcp",)
    assert descriptor.request_timeout_ms == 1_800_000
    assert runner.get_registered_command("ghost-setup") is not None
    assert runner.get_registered_command("ghost-doctor") is not None
    assert not list(tmp_path.rglob("mcp.json"))


def test_extension_registration_is_idempotent_for_duplicate_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = _executable(tmp_path / "package")
    monkeypatch.setattr(packaged_servers, "_REGISTRY", {})
    monkeypatch.setattr(extension_module, "package_root", lambda: binary.parents[1])
    monkeypatch.setattr(extension_module, "ghost_binary", lambda: binary)
    runner = ExtensionRunner(cwd=str(tmp_path))

    extension_module.extension(runner.create_extension_api("/one/extensions/ghost_mcp.py"))
    extension_module.extension(runner.create_extension_api("/two/extensions/ghost_mcp.py"))

    assert [item.name for item in runner.get_all_registered_commands()] == [
        "ghost-setup",
        "ghost-doctor",
    ]
    assert list(get_packaged_servers()) == ["ghost-os"]
