from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from travis234_ghost_mcp import host
from travis234_ghost_mcp.host import (
    PackageIntegrityError,
    UnsupportedHostError,
    ghost_binary,
    require_supported_host,
)


def _supported_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(host.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(host.platform, "mac_ver", lambda: ("14.0.0", ("", "", ""), ""))


@pytest.mark.parametrize(
    ("system", "machine", "version"),
    [
        ("linux", "arm64", "14.0.0"),
        ("darwin", "x86_64", "14.0.0"),
        ("darwin", "arm64", "13.6.9"),
    ],
)
def test_supported_host_requires_darwin_arm64_and_macos_14(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    version: str,
) -> None:
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(host.platform, "machine", lambda: machine)
    monkeypatch.setattr(
        host.platform,
        "mac_ver",
        lambda: (version, ("", "", ""), ""),
    )

    with pytest.raises(UnsupportedHostError, match="macOS 14.*Apple Silicon"):
        require_supported_host()


def test_supported_host_accepts_macos_14_arm64(monkeypatch: pytest.MonkeyPatch) -> None:
    _supported_host(monkeypatch)

    require_supported_host()


def test_ghost_binary_never_falls_back_to_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _supported_host(monkeypatch)
    monkeypatch.setattr(host, "package_root", lambda: tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake = fake_bin / "ghost"
    fake.write_text("not the package binary", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    with pytest.raises(PackageIntegrityError, match="embedded Ghost executable"):
        ghost_binary()


def test_ghost_binary_rejects_symlink_outside_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _supported_host(monkeypatch)
    root = tmp_path / "package"
    (root / "bin").mkdir(parents=True)
    outside = tmp_path / "ghost"
    outside.write_text("binary", encoding="utf-8")
    outside.chmod(0o755)
    os.symlink(outside, root / "bin" / "ghost")
    monkeypatch.setattr(host, "package_root", lambda: root)

    with pytest.raises(PackageIntegrityError, match="embedded Ghost executable"):
        ghost_binary()


def test_ghost_binary_returns_executable_inside_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _supported_host(monkeypatch)
    root = tmp_path / "package"
    binary = root / "bin" / "ghost"
    binary.parent.mkdir(parents=True)
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(host, "package_root", lambda: root)

    assert ghost_binary() == binary.resolve()
