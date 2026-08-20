#!/usr/bin/env python3
"""Check or atomically update reviewed npm mirrors of packaged resources."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = ROOT / "travis/resources"
DEFAULT_DESTINATION_ROOT = ROOT / "packages/travis234-cli"

MANIFEST: tuple[tuple[str, str], ...] = (
    ("roles/coordination-planner.json", "roles/coordination-planner.json"),
    ("skills/coordination/SKILL.md", "skills/coordination/SKILL.md"),
    (
        "skills/coordination/references/planning-contract.md",
        "skills/coordination/references/planning-contract.md",
    ),
    ("skills/orchestration/SKILL.md", "skills/orchestration/SKILL.md"),
    (
        "skills/orchestration/references/protocol.md",
        "skills/orchestration/references/protocol.md",
    ),
    (
        "skills/orchestration/scripts/orchestrate.py",
        "skills/orchestration/scripts/orchestrate.py",
    ),
    (
        "skills/subagent-delegation/SKILL.md",
        "skills/subagent-delegation/SKILL.md",
    ),
    ("skills/web-search/SKILL.md", "skills/web-search/SKILL.md"),
)


def canonical_resource_paths(root: Path) -> set[str]:
    """Return reviewed resource files below the skills and roles roots."""
    return {
        path.relative_to(root).as_posix()
        for category in ("skills", "roles")
        for path in (root / category).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def _has_symlink_component(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        current = current.parent
    return root.is_symlink()


def _structural_issues(source_root: Path, destination_root: Path) -> tuple[str, ...]:
    expected_sources = {source for source, _destination in MANIFEST}
    expected_destinations = {destination for _source, destination in MANIFEST}
    issues = canonical_resource_paths(source_root) ^ expected_sources
    issues.update(canonical_resource_paths(destination_root) - expected_destinations)
    for source_relative, destination_relative in MANIFEST:
        source = source_root / source_relative
        destination = destination_root / destination_relative
        if _has_symlink_component(source, source_root):
            issues.add(source_relative)
        if destination.exists() and _has_symlink_component(
            destination,
            destination_root,
        ):
            issues.add(destination_relative)
    return tuple(sorted(issues))


def _drifted_destinations(
    source_root: Path,
    destination_root: Path,
) -> tuple[str, ...]:
    drifted: list[str] = []
    for source_relative, destination_relative in MANIFEST:
        source = source_root / source_relative
        destination = destination_root / destination_relative
        if not source.is_file():
            continue
        if not destination.is_file() or destination.read_bytes() != source.read_bytes():
            drifted.append(destination_relative)
    return tuple(sorted(drifted))


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def sync_packaged_resources(
    source_root: Path = DEFAULT_SOURCE_ROOT,
    destination_root: Path = DEFAULT_DESTINATION_ROOT,
    *,
    write: bool,
) -> tuple[str, ...]:
    """Return path-only drift, or atomically update reviewed destinations."""
    structural_issues = _structural_issues(source_root, destination_root)
    if structural_issues:
        return structural_issues
    drifted = _drifted_destinations(source_root, destination_root)
    if not write:
        return drifted
    sources_by_destination = {destination: source for source, destination in MANIFEST}
    for destination_relative in drifted:
        _atomic_copy(
            source_root / sources_by_destination[destination_relative],
            destination_root / destination_relative,
        )
    return ()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument(
        "--destination-root",
        type=Path,
        default=DEFAULT_DESTINATION_ROOT,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    issues = sync_packaged_resources(
        args.source_root,
        args.destination_root,
        write=args.write,
    )
    if issues:
        for path in issues:
            print(path, file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
