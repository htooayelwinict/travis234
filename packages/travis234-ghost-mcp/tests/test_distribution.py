from __future__ import annotations

from email.parser import Parser
from pathlib import Path
import stat
import subprocess
import tarfile
import tomllib
import zipfile

import pytest

from conftest import PACKAGE_ROOT


def test_package_metadata_declares_markdown_readme() -> None:
    metadata = tomllib.loads(
        (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert metadata["readme"] == "README.md"
    assert (PACKAGE_ROOT / "README.md").is_file()


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    output = tmp_path_factory.mktemp("ghost-distributions")
    subprocess.run(
        ["uv", "build", "--clear", "-o", str(output), str(PACKAGE_ROOT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    wheels = list(output.glob("*.whl"))
    sdists = list(output.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1
    return wheels[0], sdists[0]


def test_wheel_has_strict_platform_metadata_and_payload(
    built_distributions: tuple[Path, Path],
) -> None:
    wheel, _sdist = built_distributions
    assert wheel.name.endswith("-py3-none-macosx_14_0_arm64.whl")

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
        binary = archive.getinfo("travis234_ghost_mcp/bin/ghost")

    assert metadata["Name"] == "travis234-ghost-mcp"
    assert metadata["Version"] == "0.1.0"
    assert "travis234-mcp-adapter<0.2,>=0.1.2" in metadata.get_all(
        "Requires-Dist", []
    )
    assert "travis234_ghost_mcp/assets/GHOST-MCP.md" in names
    assert "travis234_ghost_mcp/assets/vision-sidecar/server.py" in names
    assert any(name.endswith("/UPSTREAM.json") for name in names)
    assert any(name.endswith("/THIRD_PARTY_NOTICES.md") for name in names)
    assert not any("/.build/" in name or "/.git/" in name for name in names)
    assert stat.S_IMODE(binary.external_attr >> 16) & stat.S_IXUSR


def test_sdist_is_reproducible_source_not_generated_output(
    built_distributions: tuple[Path, Path],
) -> None:
    _wheel, sdist = built_distributions
    with tarfile.open(sdist) as archive:
        names = archive.getnames()

    assert any(name.endswith("/UPSTREAM.json") for name in names)
    assert any(name.endswith("/vendor/ghost-os/Package.swift") for name in names)
    assert any(name.endswith("/vendor/ghost-os/Package.resolved") for name in names)
    assert any(name.endswith("/Sources/GhostOS/Common/TravisPaths.swift") for name in names)
    assert not any("/.build/" in name or "/.git/" in name for name in names)
    assert not any(name.endswith("/travis234_ghost_mcp/bin/ghost") for name in names)


def test_embedded_binary_runs_is_signed_and_uses_expected_libraries(
    built_distributions: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    wheel, _sdist = built_distributions
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(tmp_path)
        mode = archive.getinfo("travis234_ghost_mcp/bin/ghost").external_attr >> 16

    binary = tmp_path / "travis234_ghost_mcp" / "bin" / "ghost"
    binary.chmod(stat.S_IMODE(mode))
    version = subprocess.run(
        [str(binary), "version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert version.stdout.strip() == "Travis234 Ghost MCP 2.2.1"
    assert version.stderr == ""
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(binary)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    file_result = subprocess.run(
        ["/usr/bin/file", str(binary)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "Mach-O 64-bit executable arm64" in file_result.stdout

    linked = subprocess.run(
        ["/usr/bin/otool", "-L", str(binary)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.splitlines()[1:]
    paths = [line.strip().split(" ", 1)[0] for line in linked]
    assert paths
    assert all(
        path.startswith(("/System/Library/", "/usr/lib/", "@rpath/libswift"))
        for path in paths
    )
