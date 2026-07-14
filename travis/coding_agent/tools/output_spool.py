"""Bounded in-memory command output with a complete private disk spool."""

from __future__ import annotations

import codecs
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from travis.coding_agent.artifacts import ArtifactRef, ArtifactRegistry
from travis.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    truncate_tail,
)


@dataclass(frozen=True)
class OutputSnapshot:
    content: str
    truncation: TruncationResult
    full_output_path: str | None = None
    artifact_id: str | None = None


class OutputSpool:
    def __init__(
        self,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        temp_file_prefix: str = "travis-output",
        directory: str | os.PathLike[str] | None = None,
        artifact_registry: ArtifactRegistry | None = None,
        artifact_kind: str = "command-output",
    ) -> None:
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self.temp_file_prefix = temp_file_prefix
        self._artifact_registry = artifact_registry
        self._artifact_kind = artifact_kind
        self._artifact_ref: ArtifactRef | None = None
        if directory is not None:
            Path(directory).mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=f"{temp_file_prefix}-", suffix=".log", dir=directory)
        os.fchmod(fd, 0o600)
        self._file = os.fdopen(fd, "wb")
        self._path = path
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._tail = ""
        self._tail_bytes = 0
        self._max_rolling_bytes = max(self.max_bytes * 2, 1)
        self._tail_starts_at_line_boundary = True
        self._total_text_bytes = 0
        self._completed_lines = 0
        self._total_lines = 0
        self._has_open_line = False
        self._last_line_bytes = 0
        self._was_truncated = False
        self._truncated_by: str | None = None
        self._preserve_artifact = False
        self._finished = False
        self._closed = False
        self._lock = threading.RLock()

    def append(self, data: bytes) -> None:
        with self._lock:
            if self._finished:
                raise RuntimeError("Cannot append to a finished output spool")
            self._file.write(data)
            self._append_text(self._decoder.decode(data, final=False))

    def finish(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._append_text(self._decoder.decode(b"", final=True))
            self._file.flush()

    def snapshot(self, persist_if_truncated: bool = False) -> OutputSnapshot:
        with self._lock:
            snapshot_text = self._snapshot_text()
            tail = truncate_tail(snapshot_text, max_lines=self.max_lines, max_bytes=self.max_bytes)
            truncated = self._total_lines > self.max_lines or self._total_text_bytes > self.max_bytes
            truncated_by = (
                tail.truncated_by
                if truncated and tail.truncated_by is not None
                else "bytes"
                if truncated and self._total_text_bytes > self.max_bytes
                else "lines"
                if truncated
                else None
            )
            self._was_truncated = truncated
            self._truncated_by = truncated_by
            if persist_if_truncated and truncated:
                self._preserve_artifact = True
                self._file.flush()
                if self._artifact_registry is not None and self._artifact_ref is None:
                    self._artifact_ref = self._artifact_registry.register(
                        Path(self._path),
                        kind=self._artifact_kind,
                        access="read",
                    )
            truncation = TruncationResult(
                content=tail.content,
                truncated=truncated,
                truncated_by=truncated_by,
                output_lines=tail.output_lines,
                total_lines=self._total_lines,
                first_line_exceeds_limit=False,
                total_bytes=self._total_text_bytes,
                output_bytes=tail.output_bytes,
                last_line_partial=tail.last_line_partial,
                max_lines=self.max_lines,
                max_bytes=self.max_bytes,
            )
            return OutputSnapshot(
                content=tail.content,
                truncation=truncation,
                full_output_path=self._path if self._preserve_artifact else None,
                artifact_id=self._artifact_ref.id if self._artifact_ref is not None else None,
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self.finish()
            self._file.close()
            self._closed = True
            if not self._preserve_artifact:
                try:
                    os.unlink(self._path)
                except FileNotFoundError:
                    pass

    def get_last_line_bytes(self) -> int:
        with self._lock:
            return self._last_line_bytes

    def _append_text(self, text: str) -> None:
        if not text:
            return
        encoded_size = len(text.encode("utf-8"))
        self._total_text_bytes += encoded_size
        self._tail += text
        self._tail_bytes += encoded_size
        if self._tail_bytes > self._max_rolling_bytes * 2:
            self._trim_tail()

        newline_count = text.count("\n")
        if newline_count == 0:
            self._last_line_bytes += encoded_size
            self._has_open_line = True
        else:
            self._completed_lines += newline_count
            tail = text.rsplit("\n", 1)[-1]
            self._last_line_bytes = len(tail.encode("utf-8"))
            self._has_open_line = bool(tail)
        self._total_lines = self._completed_lines + int(self._has_open_line)
        self._was_truncated = self._total_lines > self.max_lines or self._total_text_bytes > self.max_bytes

    def _trim_tail(self) -> None:
        buffer = self._tail.encode("utf-8")
        if len(buffer) <= self._max_rolling_bytes:
            self._tail_bytes = len(buffer)
            return

        start = len(buffer) - self._max_rolling_bytes
        while start < len(buffer) and (buffer[start] & 0xC0) == 0x80:
            start += 1
        if start > 0:
            self._tail_starts_at_line_boundary = buffer[start - 1] == 0x0A
        self._tail = buffer[start:].decode("utf-8")
        self._tail_bytes = len(self._tail.encode("utf-8"))

    def _snapshot_text(self) -> str:
        if self._tail_starts_at_line_boundary:
            return self._tail
        first_newline = self._tail.find("\n")
        return self._tail if first_newline < 0 else self._tail[first_newline + 1 :]

__all__ = [
    "OutputSnapshot",
    "OutputSpool",
]
