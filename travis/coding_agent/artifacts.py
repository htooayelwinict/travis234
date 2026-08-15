"""Exact session-scoped references to tool-created artifacts."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from travis.coding_agent.artifact_manifest import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactProducer,
)
from travis.coding_agent.artifact_store import DurableArtifactStore
from travis.coding_agent.resource_refs import ResourceReadResolution, ResourceRefResolver

ARTIFACT_READ_BYTE_LIMIT = 50 * 1024


def artifact_read_instruction(artifact_id: str) -> str:
    return (
        f"Full output artifact: {artifact_id}. Use read with path={artifact_id}, "
        f"byte_offset=0, byte_limit={ARTIFACT_READ_BYTE_LIMIT}."
    )


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    path: Path
    kind: str
    access: Literal["read"] = "read"
    remove_on_close: bool = True


class ArtifactRegistry:
    def __init__(
        self,
        *,
        durable_store: DurableArtifactStore | None = None,
        manifest: ArtifactManifest | None = None,
    ) -> None:
        if (durable_store is None) != (manifest is None):
            raise ValueError("durable_store and manifest must be provided together")
        self._by_id: dict[str, ArtifactRef] = {}
        self._by_path: dict[Path, ArtifactRef] = {}
        self._durable_ids: set[str] = set()
        self._lock = threading.RLock()
        self._closed = False
        self._durable_store = durable_store
        self._manifest = manifest
        self._resolver = (
            ResourceRefResolver(durable_store, manifest)
            if durable_store is not None and manifest is not None
            else None
        )
        if durable_store is not None and manifest is not None:
            for entry in manifest.entries:
                path = durable_store.objects_dir / entry.digest[:2] / entry.digest
                ref = ArtifactRef(
                    id=entry.id,
                    path=path,
                    kind=entry.kind,
                    remove_on_close=False,
                )
                self._by_id[ref.id] = ref
                self._durable_ids.add(ref.id)

    def register(
        self,
        path: Path,
        kind: str,
        access: Literal["read"] = "read",
        remove_on_close: bool = True,
    ) -> ArtifactRef:
        resolved = path.expanduser().resolve(strict=False)
        with self._lock:
            if self._closed:
                raise RuntimeError("Artifact registry is closed")
            existing = self._by_path.get(resolved)
            if existing is not None:
                if existing.remove_on_close and not remove_on_close:
                    existing = ArtifactRef(
                        id=existing.id,
                        path=existing.path,
                        kind=existing.kind,
                        access=existing.access,
                        remove_on_close=False,
                    )
                    self._by_id[existing.id] = existing
                    self._by_path[resolved] = existing
                return existing
            ref = ArtifactRef(
                id=f"artifact-{uuid.uuid4().hex}",
                path=resolved,
                kind=kind,
                access=access,
                remove_on_close=remove_on_close,
            )
            self._by_id[ref.id] = ref
            self._by_path[resolved] = ref
            return ref

    def promote(
        self,
        path: Path,
        kind: str,
        *,
        session_entry_id: str | None = None,
        tool_call_id: str | None = None,
        retained: bool = False,
    ) -> ArtifactRef:
        if self._durable_store is None or self._manifest is None:
            return self.register(path, kind)
        resolved = path.expanduser().resolve(strict=False)
        with self._lock:
            if self._closed:
                raise RuntimeError("Artifact registry is closed")
            existing = self._by_path.get(resolved)
            artifact_id = existing.id if existing is not None else f"artifact-{uuid.uuid4().hex}"
            with self._durable_store.maintenance_lock():
                stored = self._durable_store.promote(resolved, self._manifest.limits)
                entry = ArtifactManifestEntry(
                    id=artifact_id,
                    digest=stored.digest,
                    kind=kind,
                    byte_size=stored.byte_size,
                    created_at_ms=time.time_ns() // 1_000_000,
                    producer=ArtifactProducer(
                        session_entry_id=session_entry_id,
                        tool_call_id=tool_call_id,
                    ),
                    retained=retained,
                )
                self._manifest.append(entry)
            ref = ArtifactRef(
                id=artifact_id,
                path=self._durable_store.objects_dir / stored.digest[:2] / stored.digest,
                kind=kind,
                remove_on_close=False,
            )
            self._by_id[ref.id] = ref
            self._durable_ids.add(ref.id)
            self._by_path.pop(resolved, None)
            return ref

    def resolve_read(self, path_or_id: str) -> Path | None:
        with self._lock:
            by_id = self._by_id.get(path_or_id)
            if by_id is not None and by_id.access == "read":
                if by_id.id in self._durable_ids:
                    return self._resolver.resolve_path(by_id.id) if self._resolver is not None else None
                return by_id.path
            try:
                resolved = Path(path_or_id).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                return None
            ref = self._by_path.get(resolved)
            return ref.path if ref is not None and ref.access == "read" else None

    def is_readable_reference(self, identifier: str) -> bool:
        with self._lock:
            ref = self._by_id.get(identifier)
            return ref is not None and ref.access == "read"

    def resolve_resource_read(
        self,
        identifier: str,
        *,
        byte_offset: int,
        byte_limit: int,
    ) -> ResourceReadResolution | None:
        if self._resolver is None or identifier not in self._durable_ids:
            return None
        return self._resolver.resolve_read(
            identifier,
            byte_offset=byte_offset,
            byte_limit=byte_limit,
        )

    def close(self, remove_files: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            refs = tuple(self._by_id.values())
            self._closed = True
            self._by_id.clear()
            self._by_path.clear()
            self._durable_ids.clear()
        if remove_files:
            for ref in refs:
                if not ref.remove_on_close:
                    continue
                try:
                    ref.path.unlink()
                except FileNotFoundError:
                    pass


__all__ = [
    "ARTIFACT_READ_BYTE_LIMIT",
    "ArtifactRef",
    "ArtifactRegistry",
    "artifact_read_instruction",
]
