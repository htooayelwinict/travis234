"""Immutable content-addressed storage for durable session artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_COPY_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ArtifactLimits:
    max_object_bytes: int = 64 * 1024 * 1024
    max_session_logical_bytes: int = 512 * 1024 * 1024
    max_session_objects: int = 10_000
    max_physical_bytes: int = 2 * 1024 * 1024 * 1024
    max_physical_objects: int = 100_000
    min_free_bytes: int = 128 * 1024 * 1024


@dataclass(frozen=True)
class StoredArtifactObject:
    digest: str
    byte_size: int


class ArtifactPromotionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class _LockState:
    mutex: threading.RLock
    depth: int = 0
    descriptor: int | None = None


class ArtifactMaintenanceLock:
    """A path-scoped lock that is reentrant within a process."""

    _states_guard = threading.Lock()
    _states: dict[Path, _LockState] = {}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().absolute()
        with self._states_guard:
            self._state = self._states.setdefault(self.path, _LockState(threading.RLock()))
        self._entry_depth = 0

    def __enter__(self) -> "ArtifactMaintenanceLock":
        self._state.mutex.acquire()
        try:
            if self._state.depth == 0:
                _ensure_directory(self.path.parent)
                flags = os.O_CREAT | os.O_RDWR
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(self.path, flags, 0o600)
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    os.close(descriptor)
                    raise ArtifactPromotionError("invalid_lock", "Artifact lock is not a regular file")
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._state.descriptor = descriptor
            self._state.depth += 1
            self._entry_depth += 1
            return self
        except BaseException:
            self._state.mutex.release()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._entry_depth == 0:
            return
        try:
            self._entry_depth -= 1
            self._state.depth -= 1
            if self._state.depth == 0:
                descriptor = self._state.descriptor
                self._state.descriptor = None
                if descriptor is not None:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
        finally:
            self._state.mutex.release()


class DurableArtifactStore:
    def __init__(self, agent_dir: str | Path) -> None:
        self.agent_dir = Path(agent_dir).expanduser().absolute()
        self.artifacts_dir = self.agent_dir / "artifacts"
        self.objects_dir = self.artifacts_dir / "objects"
        self._maintenance_lock = ArtifactMaintenanceLock(self.artifacts_dir / ".lock")

    def maintenance_lock(self) -> ArtifactMaintenanceLock:
        return self._maintenance_lock

    def promote(
        self,
        source: Path,
        limits: ArtifactLimits | None = None,
    ) -> StoredArtifactObject:
        effective_limits = limits or ArtifactLimits()
        source_path = Path(source).expanduser().absolute()
        digest, source_metadata = _hash_source(source_path, effective_limits.max_object_bytes)
        byte_size = source_metadata.st_size

        with self._maintenance_lock:
            _ensure_directory(self.objects_dir)
            destination_dir = self.objects_dir / digest[:2]
            _ensure_directory(destination_dir)
            destination = destination_dir / digest

            if destination.exists() or destination.is_symlink():
                verified = self.verify(digest)
                if verified.stat().st_size != byte_size:
                    raise ArtifactPromotionError("integrity_error", "Stored artifact size does not match")
                return StoredArtifactObject(digest=digest, byte_size=byte_size)

            physical_bytes, physical_objects = self._physical_usage()
            if physical_objects + 1 > effective_limits.max_physical_objects:
                raise ArtifactPromotionError("physical_limit", "Artifact object-count limit exceeded")
            if physical_bytes + byte_size > effective_limits.max_physical_bytes:
                raise ArtifactPromotionError("physical_limit", "Artifact physical-byte limit exceeded")
            free_bytes = shutil.disk_usage(self.artifacts_dir).free
            if free_bytes - byte_size < effective_limits.min_free_bytes:
                raise ArtifactPromotionError("insufficient_space", "Artifact free-space reserve would be exceeded")

            temporary = destination_dir / f".{digest}.{uuid.uuid4().hex}.tmp"
            try:
                copied_digest, copied_size = _copy_source_to_temp(
                    source_path,
                    source_metadata,
                    temporary,
                )
                if copied_digest != digest or copied_size != byte_size:
                    raise ArtifactPromotionError("source_changed", "Artifact source changed during promotion")
                try:
                    os.link(temporary, destination, follow_symlinks=False)
                except FileExistsError:
                    self.verify(digest)
                else:
                    os.chmod(destination, 0o600, follow_symlinks=False)
                    fsync_artifact_directory(destination_dir)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

            self.verify(digest)
            return StoredArtifactObject(digest=digest, byte_size=byte_size)

    def verify(self, digest: str) -> Path:
        if _DIGEST_PATTERN.fullmatch(digest) is None:
            raise ArtifactPromotionError("invalid_digest", "Artifact digest is invalid")
        path = self.objects_dir / digest[:2] / digest
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            raise ArtifactPromotionError("missing_object", "Artifact object is unavailable") from error
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ArtifactPromotionError("integrity_error", "Artifact object is not a regular file")
        calculated, verified_metadata = _hash_source(path, None)
        if calculated != digest or verified_metadata.st_size != metadata.st_size:
            raise ArtifactPromotionError("integrity_error", "Artifact object failed verification")
        return path

    def physical_bytes(self) -> int:
        with self._maintenance_lock:
            return self._physical_usage()[0]

    def _physical_usage(self) -> tuple[int, int]:
        if not self.objects_dir.exists():
            return 0, 0
        total_bytes = 0
        total_objects = 0
        for prefix in self.objects_dir.iterdir():
            prefix_metadata = prefix.lstat()
            if not stat.S_ISDIR(prefix_metadata.st_mode) or stat.S_ISLNK(prefix_metadata.st_mode):
                raise ArtifactPromotionError("integrity_error", "Artifact object directory is invalid")
            for object_path in prefix.iterdir():
                if _DIGEST_PATTERN.fullmatch(object_path.name) is None:
                    continue
                metadata = object_path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    raise ArtifactPromotionError("integrity_error", "Artifact object is invalid")
                total_bytes += metadata.st_size
                total_objects += 1
        return total_bytes, total_objects


def _hash_source(path: Path, max_bytes: int | None) -> tuple[str, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as error:
        raise ArtifactPromotionError("invalid_source", "Artifact source is unavailable") from error
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ArtifactPromotionError("invalid_source", "Artifact source must be a regular file")
    if max_bytes is not None and before.st_size > max_bytes:
        raise ArtifactPromotionError("object_limit", "Artifact object-byte limit exceeded")

    descriptor = _open_read_no_follow(path)
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not _same_file_snapshot(before, opened):
            raise ArtifactPromotionError("source_changed", "Artifact source changed before hashing")
        while chunk := os.read(descriptor, _COPY_CHUNK_BYTES):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ArtifactPromotionError("invalid_source", "Artifact source could not be read") from error
    finally:
        os.close(descriptor)
    if not _same_file_snapshot(opened, after):
        raise ArtifactPromotionError("source_changed", "Artifact source changed during hashing")
    return digest.hexdigest(), after


def _copy_source_to_temp(
    source: Path,
    expected: os.stat_result,
    temporary: Path,
) -> tuple[str, int]:
    source_descriptor = _open_read_no_follow(source)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    temporary_descriptor: int | None = None
    digest = hashlib.sha256()
    byte_size = 0
    try:
        opened = os.fstat(source_descriptor)
        if not _same_file_snapshot(expected, opened):
            raise ArtifactPromotionError("source_changed", "Artifact source changed before copy")
        temporary_descriptor = os.open(temporary, flags, 0o600)
        os.fchmod(temporary_descriptor, 0o600)
        while chunk := os.read(source_descriptor, _COPY_CHUNK_BYTES):
            digest.update(chunk)
            byte_size += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(temporary_descriptor, view)
                view = view[written:]
        after = os.fstat(source_descriptor)
        if not _same_file_snapshot(opened, after):
            raise ArtifactPromotionError("source_changed", "Artifact source changed during copy")
        os.fsync(temporary_descriptor)
    except OSError as error:
        raise ArtifactPromotionError("io_error", "Artifact object could not be written") from error
    finally:
        os.close(source_descriptor)
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
    return digest.hexdigest(), byte_size


def _open_read_no_follow(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except OSError as error:
        raise ArtifactPromotionError("invalid_source", "Artifact source could not be opened") from error


def _same_file_snapshot(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ArtifactPromotionError("invalid_path", "Artifact directory is invalid")
    path.chmod(0o700)


def fsync_artifact_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "ArtifactLimits",
    "ArtifactMaintenanceLock",
    "ArtifactPromotionError",
    "DurableArtifactStore",
    "StoredArtifactObject",
    "fsync_artifact_directory",
]
