"""Contracts for canonical Python packaged resources and npm mirrors."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from scripts.sync_packaged_resources import (
    MANIFEST,
    canonical_resource_paths,
    sync_packaged_resources,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync_packaged_resources.py"
CANONICAL_ROOT = ROOT / "travis/resources"
MIRROR_ROOT = ROOT / "packages/travis234-cli"


def _seed_roots(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "canonical"
    destination_root = tmp_path / "mirror"
    for source_relative, destination_relative in MANIFEST:
        source = source_root / source_relative
        destination = destination_root / destination_relative
        source.parent.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = f"content:{source_relative}\n".encode()
        source.write_bytes(content)
        destination.write_bytes(content)
    return source_root, destination_root


def _run(
    mode: str,
    source_root: Path,
    destination_root: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            mode,
            "--source-root",
            str(source_root),
            "--destination-root",
            str(destination_root),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_manifest_exactly_describes_canonical_and_npm_resource_trees() -> None:
    source_paths = {source for source, _destination in MANIFEST}
    destination_paths = {destination for _source, destination in MANIFEST}

    assert source_paths == canonical_resource_paths(CANONICAL_ROOT)
    assert destination_paths == canonical_resource_paths(MIRROR_ROOT)
    assert len(source_paths) == len(MANIFEST) == len(destination_paths)
    for source_relative, destination_relative in MANIFEST:
        source = CANONICAL_ROOT / source_relative
        destination = MIRROR_ROOT / destination_relative
        assert not source.is_symlink()
        assert not destination.is_symlink()
        assert source.read_bytes() == destination.read_bytes()


def test_check_reports_only_drifted_manifest_paths(tmp_path: Path) -> None:
    source_root, destination_root = _seed_roots(tmp_path)
    drifted = MANIFEST[0][1]
    (destination_root / drifted).write_text("drift\n", encoding="utf-8")

    completed = _run("--check", source_root, destination_root)

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == f"{drifted}\n"


def test_write_uses_atomic_replacement_only_for_drifted_destinations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root, destination_root = _seed_roots(tmp_path)
    drifted = [MANIFEST[0][1], MANIFEST[-1][1]]
    for relative in drifted:
        (destination_root / relative).write_text("old\n", encoding="utf-8")
    replacements: list[Path] = []
    original_replace = os.replace

    def record_replace(source: str | bytes | os.PathLike, destination: str | bytes | os.PathLike) -> None:
        replacements.append(Path(destination))
        original_replace(source, destination)

    monkeypatch.setattr("scripts.sync_packaged_resources.os.replace", record_replace)

    assert sync_packaged_resources(source_root, destination_root, write=True) == ()
    assert sorted(path.relative_to(destination_root).as_posix() for path in replacements) == sorted(drifted)
    for source_relative, destination_relative in MANIFEST:
        assert (destination_root / destination_relative).read_bytes() == (source_root / source_relative).read_bytes()


def test_sync_treats_environment_syntax_as_literal_bytes(tmp_path: Path) -> None:
    source_root, destination_root = _seed_roots(tmp_path)
    source_relative, destination_relative = MANIFEST[0]
    literal = b"$API_KEY ${HOME} %SECRET%\n"
    (source_root / source_relative).write_bytes(literal)

    assert sync_packaged_resources(source_root, destination_root, write=True) == ()
    assert (destination_root / destination_relative).read_bytes() == literal


def test_unexpected_destination_fails_without_deletion_or_rewrite(tmp_path: Path) -> None:
    source_root, destination_root = _seed_roots(tmp_path)
    unexpected = destination_root / "skills/unreviewed/SKILL.md"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_text("keep me\n", encoding="utf-8")
    source_relative, destination_relative = MANIFEST[0]
    (source_root / source_relative).write_text("new\n", encoding="utf-8")
    before = (destination_root / destination_relative).read_bytes()

    completed = _run("--write", source_root, destination_root)

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "skills/unreviewed/SKILL.md\n"
    assert unexpected.read_text(encoding="utf-8") == "keep me\n"
    assert (destination_root / destination_relative).read_bytes() == before


def test_symlinked_manifest_member_fails_closed(tmp_path: Path) -> None:
    source_root, destination_root = _seed_roots(tmp_path)
    _source_relative, destination_relative = MANIFEST[0]
    destination = destination_root / destination_relative
    external = tmp_path / "external"
    external.write_text("external\n", encoding="utf-8")
    destination.unlink()
    destination.symlink_to(external)

    completed = _run("--write", source_root, destination_root)

    assert completed.returncode == 1
    assert completed.stderr == f"{destination_relative}\n"
    assert destination.is_symlink()
    assert external.read_text(encoding="utf-8") == "external\n"
