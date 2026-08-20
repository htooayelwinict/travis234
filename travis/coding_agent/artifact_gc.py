"""Explicit fail-closed durable artifact maintenance.

Ownership and nonautomatic retention policy are documented in
``docs/architecture/artifact-retention.md``.
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path

from travis.coding_agent.artifact_manifest import read_artifact_manifest_strict
from travis.coding_agent.artifact_store import DurableArtifactStore, fsync_artifact_directory
from travis.coding_agent.session_catalog import SessionCatalog

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ArtifactGcReport:
    completed: bool
    scanned_manifests: int
    referenced_digests: int
    deleted: tuple[str, ...]
    retained: tuple[str, ...]
    errors: tuple[str, ...]


class ArtifactGarbageCollector:
    def __init__(self, store: DurableArtifactStore, session_catalog: SessionCatalog) -> None:
        self.store = store
        self.session_catalog = session_catalog

    def collect(self, *, dry_run: bool = False) -> ArtifactGcReport:
        with self.store.maintenance_lock():
            manifest_paths = (
                sorted(self.session_catalog.root.rglob("*.artifacts.jsonl"))
                if self.session_catalog.root.exists()
                else []
            )
            referenced: set[str] = set()
            errors: list[str] = []
            scanned = 0
            for path in manifest_paths:
                try:
                    entries = read_artifact_manifest_strict(path)
                except (OSError, ValueError) as error:
                    errors.append(f"{path.name}: {type(error).__name__}")
                    continue
                scanned += 1
                referenced.update(entry.digest for entry in entries)

            if errors:
                return ArtifactGcReport(
                    completed=False,
                    scanned_manifests=scanned,
                    referenced_digests=len(referenced),
                    deleted=(),
                    retained=(),
                    errors=tuple(errors),
                )

            objects, object_errors = self._objects()
            if object_errors:
                return ArtifactGcReport(
                    completed=False,
                    scanned_manifests=scanned,
                    referenced_digests=len(referenced),
                    deleted=(),
                    retained=tuple(sorted(set(objects) & referenced)),
                    errors=tuple(object_errors),
                )

            retained = tuple(sorted(set(objects) & referenced))
            candidates = tuple(sorted(set(objects) - referenced))
            if not dry_run:
                changed_directories: set[Path] = set()
                for digest in candidates:
                    path = objects[digest]
                    path.unlink()
                    changed_directories.add(path.parent)
                for directory in changed_directories:
                    fsync_artifact_directory(directory)
            return ArtifactGcReport(
                completed=True,
                scanned_manifests=scanned,
                referenced_digests=len(referenced),
                deleted=candidates,
                retained=retained,
                errors=(),
            )

    def _objects(self) -> tuple[dict[str, Path], list[str]]:
        if not self.store.objects_dir.exists():
            return {}, []
        objects: dict[str, Path] = {}
        errors: list[str] = []
        for path in self.store.objects_dir.rglob("*"):
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _DIGEST_PATTERN.fullmatch(path.name) is None
                or path.parent.name != path.name[:2]
            ):
                errors.append(f"invalid object entry: {path.name}")
                continue
            objects[path.name] = path
        return objects, errors

__all__ = [
    "ArtifactGarbageCollector",
    "ArtifactGcReport",
]
