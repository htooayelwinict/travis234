"""Bounded resolution of opaque durable artifact references."""

from __future__ import annotations

import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from travis.coding_agent.artifact_manifest import ArtifactManifest
from travis.coding_agent.artifact_store import ArtifactPromotionError, DurableArtifactStore

_MAX_READ_BYTES = 50 * 1024


@dataclass(frozen=True)
class ResourceReadResolution:
    available: bool
    artifact_id: str
    content: bytes = b""
    next_offset: int | None = None
    total_bytes: int | None = None
    error_code: str | None = None


class ResourceRefResolver:
    def __init__(self, store: DurableArtifactStore, manifest: ArtifactManifest) -> None:
        self.store = store
        self.manifest = manifest
        self._verified: dict[str, tuple[int, int, int, int]] = {}
        self._lock = threading.RLock()

    def resolve_path(self, identifier: str) -> Path | None:
        entry = self.manifest.get(identifier)
        if entry is None:
            return None
        with self._lock:
            try:
                return self._verified_path(entry.digest)
            except ArtifactPromotionError:
                return None

    def resolve_read(
        self,
        identifier: str,
        *,
        byte_offset: int,
        byte_limit: int,
    ) -> ResourceReadResolution:
        entry = self.manifest.get(identifier)
        if entry is None:
            return _unavailable(identifier, "unauthorized")
        if (
            not isinstance(byte_offset, int)
            or isinstance(byte_offset, bool)
            or byte_offset < 0
            or not isinstance(byte_limit, int)
            or isinstance(byte_limit, bool)
            or byte_limit < 1
            or byte_limit > _MAX_READ_BYTES
        ):
            return _unavailable(identifier, "invalid_range")

        with self._lock:
            try:
                path = self._verified_path(entry.digest)
                before = path.lstat()
                if byte_offset > before.st_size:
                    return _unavailable(identifier, "invalid_range", total_bytes=before.st_size)
                descriptor = _open_object(path)
                try:
                    opened = os.fstat(descriptor)
                    if _metadata_signature(opened) != _metadata_signature(before):
                        self._verified.pop(entry.digest, None)
                        return _unavailable(identifier, "integrity_error")
                    content = os.pread(descriptor, byte_limit, byte_offset)
                    after = os.fstat(descriptor)
                    if _metadata_signature(after) != _metadata_signature(opened):
                        self._verified.pop(entry.digest, None)
                        return _unavailable(identifier, "integrity_error")
                finally:
                    os.close(descriptor)
            except ArtifactPromotionError as error:
                self._verified.pop(entry.digest, None)
                return _unavailable(identifier, error.code)
            except OSError:
                self._verified.pop(entry.digest, None)
                return _unavailable(identifier, "unavailable")

        end_offset = byte_offset + len(content)
        return ResourceReadResolution(
            available=True,
            artifact_id=identifier,
            content=content,
            next_offset=end_offset if end_offset < before.st_size else None,
            total_bytes=before.st_size,
        )

    def _verified_path(self, digest: str) -> Path:
        path = self.store.objects_dir / digest[:2] / digest
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ArtifactPromotionError("missing_object", "Artifact object is unavailable") from error
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ArtifactPromotionError("integrity_error", "Artifact object is invalid")
        signature = _metadata_signature(metadata)
        if self._verified.get(digest) != signature:
            path = self.store.verify(digest)
            metadata = path.lstat()
            signature = _metadata_signature(metadata)
            self._verified[digest] = signature
        return path


def _metadata_signature(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _open_object(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def _unavailable(
    artifact_id: str,
    error_code: str,
    *,
    total_bytes: int | None = None,
) -> ResourceReadResolution:
    return ResourceReadResolution(
        available=False,
        artifact_id=artifact_id,
        total_bytes=total_bytes,
        error_code=error_code,
    )


__all__ = [
    "ResourceReadResolution",
    "ResourceRefResolver",
]
