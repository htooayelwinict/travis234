from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from travis234_mcp_adapter import packaged_servers
from travis234_mcp_adapter.packaged_servers import (
    PackagedServer,
    get_packaged_servers,
    register_packaged_server,
)


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packaged_servers, "_REGISTRY", {})


@pytest.fixture
def packaged_descriptor(tmp_path: Path) -> PackagedServer:
    root = tmp_path / "payload"
    binary = root / "bin" / "fixture-server"
    binary.parent.mkdir(parents=True)
    binary.write_text("binary", encoding="utf-8")
    binary.chmod(0o755)
    return PackagedServer(
        name="package-fixture",
        package_root=root,
        command=binary,
        args=("mcp",),
        request_timeout_ms=1_800_000,
    )


def test_packaged_server_requires_absolute_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        PackagedServer(
            name="package-fixture",
            package_root=Path("payload"),
            command=Path("payload/bin/fixture-server"),
        )


def test_packaged_server_requires_executable_inside_package(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    outside = tmp_path / "fixture-server"
    outside.write_text("binary", encoding="utf-8")
    outside.chmod(0o755)

    with pytest.raises(ValueError, match="inside package root"):
        PackagedServer(name="package-fixture", package_root=root, command=outside)

    inside = root / "fixture-server"
    inside.write_text("binary", encoding="utf-8")
    with pytest.raises(ValueError, match="executable file"):
        PackagedServer(name="package-fixture", package_root=root, command=inside)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": " package-fixture"}, "non-empty and trimmed"),
        ({"args": ["mcp"]}, "tuple of strings"),
        ({"args": ("mcp", 1)}, "tuple of strings"),
        ({"request_timeout_ms": 0}, "positive integer"),
        ({"request_timeout_ms": True}, "positive integer"),
    ],
)
def test_packaged_server_rejects_malformed_fields(
    packaged_descriptor: PackagedServer,
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(packaged_descriptor, **changes)


def test_registration_is_idempotent_but_rejects_substitution(
    packaged_descriptor: PackagedServer,
) -> None:
    register_packaged_server(packaged_descriptor)
    register_packaged_server(packaged_descriptor)

    snapshot = get_packaged_servers()
    assert tuple(snapshot) == ("package-fixture",)
    with pytest.raises(TypeError):
        snapshot["other"] = packaged_descriptor  # type: ignore[index]

    replacement = packaged_descriptor.command.parent / "other"
    replacement.write_text("replacement", encoding="utf-8")
    replacement.chmod(0o755)
    with pytest.raises(ValueError, match="already registered"):
        register_packaged_server(replace(packaged_descriptor, command=replacement))


def test_registry_snapshot_is_sorted_and_detached(
    packaged_descriptor: PackagedServer,
) -> None:
    alpha_binary = packaged_descriptor.command.parent / "alpha"
    alpha_binary.write_text("alpha", encoding="utf-8")
    alpha_binary.chmod(0o755)
    alpha = replace(packaged_descriptor, name="alpha", command=alpha_binary)
    register_packaged_server(packaged_descriptor)
    first = get_packaged_servers()
    register_packaged_server(alpha)

    assert tuple(first) == ("package-fixture",)
    assert tuple(get_packaged_servers()) == ("alpha", "package-fixture")
