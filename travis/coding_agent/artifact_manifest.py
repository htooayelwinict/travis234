"""Append-only authorization manifests for durable session artifacts."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from travis.coding_agent.artifact_store import ArtifactLimits, ArtifactPromotionError
from travis.coding_agent.session_lock import SessionFileLock

_ARTIFACT_ID_PATTERN = re.compile(r"artifact-[0-9a-f]{32}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class ArtifactManifestCorruptionError(ValueError):
    def __init__(self, path: Path, line_number: int, detail: str) -> None:
        self.path = path
        self.line_number = line_number
        self.detail = detail
        super().__init__(f"Artifact manifest {path} is corrupt at line {line_number}: {detail}")


@dataclass(frozen=True)
class ArtifactProducer:
    session_entry_id: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ArtifactManifestEntry:
    id: str
    digest: str
    kind: str
    byte_size: int
    created_at_ms: int
    producer: ArtifactProducer
    retained: bool = False


class ArtifactManifest:
    def __init__(self, path: Path, limits: ArtifactLimits) -> None:
        self.path = path
        self.limits = limits
        self._entries: list[ArtifactManifestEntry] = []
        self._by_id: dict[str, ArtifactManifestEntry] = {}
        self._thread_lock = threading.RLock()
        self.recovered_tail_path: Path | None = None
        with self._thread_lock, SessionFileLock(self.path):
            self._load_unlocked()

    @classmethod
    def for_session(
        cls,
        session_path: str | Path,
        *,
        limits: ArtifactLimits | None = None,
    ) -> "ArtifactManifest":
        path = Path(f"{Path(session_path)}.artifacts.jsonl")
        return cls(path, limits or ArtifactLimits())

    @property
    def entries(self) -> tuple[ArtifactManifestEntry, ...]:
        with self._thread_lock:
            return tuple(self._entries)

    def get(self, artifact_id: str) -> ArtifactManifestEntry | None:
        with self._thread_lock:
            return self._by_id.get(artifact_id)

    def append(self, entry: ArtifactManifestEntry) -> None:
        _validate_entry(entry)
        with self._thread_lock, SessionFileLock(self.path):
            self._load_unlocked()
            existing = self._by_id.get(entry.id)
            if existing is not None:
                if existing == entry:
                    return
                raise ArtifactPromotionError(
                    "duplicate_artifact_id",
                    "Artifact ID is already authorized with different metadata",
                )
            self._check_append_limits(entry)
            self._write_record(_entry_to_record(entry))
            self._entries.append(entry)
            self._by_id[entry.id] = entry

    def fork_to(
        self,
        target_session_path: str | Path,
        *,
        allowed_entry_ids: set[str],
        allowed_tool_call_ids: set[str],
    ) -> "ArtifactManifest":
        with self._thread_lock, SessionFileLock(self.path):
            self._load_unlocked()
            selected = tuple(
                entry
                for entry in self._entries
                if _entry_is_reachable(entry, allowed_entry_ids, allowed_tool_call_ids)
            )

        target_path = Path(f"{Path(target_session_path)}.artifacts.jsonl")
        _check_entries_fit(selected, self.limits)
        with SessionFileLock(target_path):
            if target_path.exists() and target_path.stat().st_size:
                raise ArtifactPromotionError(
                    "target_exists",
                    "Target artifact manifest already exists",
                )
            target_path.parent.mkdir(parents=True, exist_ok=True)
            payload = b"".join(
                (_encode_record(_entry_to_record(entry)) for entry in selected),
            )
            _atomic_write(target_path, payload)
        return ArtifactManifest(target_path, self.limits)

    def _load_unlocked(self) -> None:
        self._entries = []
        self._by_id = {}
        self.recovered_tail_path = None
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        raw = self.path.read_bytes()
        lines = raw.splitlines(keepends=True)
        for index, raw_line in enumerate(lines):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                incomplete_tail = index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r"))
                if incomplete_tail:
                    self.recovered_tail_path = self._recover_truncated_tail(
                        valid_prefix=b"".join(lines[:index]),
                        tail=raw_line,
                    )
                    break
                raise ArtifactManifestCorruptionError(self.path, index + 1, str(error)) from error
            try:
                entry = _entry_from_record(value)
            except (TypeError, ValueError) as error:
                raise ArtifactManifestCorruptionError(self.path, index + 1, str(error)) from error
            existing = self._by_id.get(entry.id)
            if existing is not None and existing != entry:
                raise ArtifactManifestCorruptionError(
                    self.path,
                    index + 1,
                    "duplicate artifact ID has different metadata",
                )
            if existing is None:
                self._entries.append(entry)
                self._by_id[entry.id] = entry

    def _check_append_limits(self, entry: ArtifactManifestEntry) -> None:
        logical_bytes = sum(item.byte_size for item in self._entries)
        if len(self._entries) + 1 > self.limits.max_session_objects:
            raise ArtifactPromotionError("session_limit", "Artifact session reference limit exceeded")
        if logical_bytes + entry.byte_size > self.limits.max_session_logical_bytes:
            raise ArtifactPromotionError("session_limit", "Artifact session byte limit exceeded")

    def _write_record(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            payload = _encode_record(record)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _recover_truncated_tail(self, *, valid_prefix: bytes, tail: bytes) -> Path:
        quarantine = self.path.with_name(
            f"{self.path.name}.truncated-{uuid.uuid4().hex}.partial",
        )
        descriptor = os.open(quarantine, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(tail)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            quarantine.unlink(missing_ok=True)
            raise
        _atomic_write(self.path, valid_prefix)
        return quarantine


def read_artifact_manifest_strict(path: str | Path) -> tuple[ArtifactManifestEntry, ...]:
    manifest_path = Path(path)
    metadata = manifest_path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ArtifactManifestCorruptionError(manifest_path, 0, "manifest is not a regular file")
    raw = manifest_path.read_bytes()
    entries: list[ArtifactManifestEntry] = []
    by_id: dict[str, ArtifactManifestEntry] = {}
    for index, raw_line in enumerate(raw.splitlines(keepends=True)):
        if not raw_line.strip():
            continue
        if not raw_line.endswith((b"\n", b"\r")):
            raise ArtifactManifestCorruptionError(
                manifest_path,
                index + 1,
                "record is not newline terminated",
            )
        try:
            value = json.loads(raw_line.decode("utf-8"))
            entry = _entry_from_record(value)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ArtifactManifestCorruptionError(manifest_path, index + 1, str(error)) from error
        existing = by_id.get(entry.id)
        if existing is not None and existing != entry:
            raise ArtifactManifestCorruptionError(
                manifest_path,
                index + 1,
                "duplicate artifact ID has different metadata",
            )
        if existing is None:
            entries.append(entry)
            by_id[entry.id] = entry
    return tuple(entries)


def _validate_entry(entry: ArtifactManifestEntry) -> None:
    if _ARTIFACT_ID_PATTERN.fullmatch(entry.id) is None:
        raise ValueError("artifact ID must use artifact- plus 32 lowercase hex characters")
    if _DIGEST_PATTERN.fullmatch(entry.digest) is None:
        raise ValueError("artifact digest must contain 64 lowercase hex characters")
    if not entry.kind or not isinstance(entry.kind, str):
        raise ValueError("artifact kind must be a non-empty string")
    if not isinstance(entry.byte_size, int) or isinstance(entry.byte_size, bool) or entry.byte_size < 0:
        raise ValueError("artifact byte size must be a non-negative integer")
    if not isinstance(entry.created_at_ms, int) or isinstance(entry.created_at_ms, bool):
        raise ValueError("artifact creation time must be an integer")
    for value in (entry.producer.session_entry_id, entry.producer.tool_call_id):
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError("artifact producer IDs must be non-empty strings")


def _entry_to_record(entry: ArtifactManifestEntry) -> dict[str, Any]:
    producer = asdict(entry.producer)
    return {
        "type": "artifact",
        "id": entry.id,
        "digest": entry.digest,
        "kind": entry.kind,
        "byteSize": entry.byte_size,
        "createdAtMs": entry.created_at_ms,
        "producer": {
            "sessionEntryId": producer["session_entry_id"],
            "toolCallId": producer["tool_call_id"],
        },
        "retained": entry.retained,
    }


def _entry_from_record(value: Any) -> ArtifactManifestEntry:
    if not isinstance(value, dict) or value.get("type") != "artifact":
        raise ValueError("record type must be artifact")
    producer = value.get("producer")
    if not isinstance(producer, dict):
        raise ValueError("producer must be an object")
    retained = value.get("retained", False)
    if not isinstance(retained, bool):
        raise ValueError("retained must be a boolean")
    entry = ArtifactManifestEntry(
        id=value.get("id"),
        digest=value.get("digest"),
        kind=value.get("kind"),
        byte_size=value.get("byteSize"),
        created_at_ms=value.get("createdAtMs"),
        producer=ArtifactProducer(
            session_entry_id=producer.get("sessionEntryId"),
            tool_call_id=producer.get("toolCallId"),
        ),
        retained=retained,
    )
    _validate_entry(entry)
    return entry


def _entry_is_reachable(
    entry: ArtifactManifestEntry,
    allowed_entry_ids: set[str],
    allowed_tool_call_ids: set[str],
) -> bool:
    if entry.retained:
        return True
    producer = entry.producer
    return (
        producer.session_entry_id is not None
        and producer.session_entry_id in allowed_entry_ids
    ) or (
        producer.tool_call_id is not None
        and producer.tool_call_id in allowed_tool_call_ids
    )


def _check_entries_fit(entries: tuple[ArtifactManifestEntry, ...], limits: ArtifactLimits) -> None:
    if len(entries) > limits.max_session_objects:
        raise ArtifactPromotionError("session_limit", "Fork artifact reference limit exceeded")
    if sum(entry.byte_size for entry in entries) > limits.max_session_logical_bytes:
        raise ArtifactPromotionError("session_limit", "Fork artifact byte limit exceeded")


def _encode_record(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "ArtifactManifest",
    "ArtifactManifestCorruptionError",
    "ArtifactManifestEntry",
    "ArtifactProducer",
    "read_artifact_manifest_strict",
]
