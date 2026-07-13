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
        self._tail_starts_partial = False
        self._total_text_bytes = 0
        self._newline_count = 0
        self._saw_text = False
        self._ends_with_newline = False
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
            if persist_if_truncated and self._was_truncated:
                self._preserve_artifact = True
                self._file.flush()
                if self._artifact_registry is not None and self._artifact_ref is None:
                    self._artifact_ref = self._artifact_registry.register(
                        Path(self._path),
                        kind=self._artifact_kind,
                        access="read",
                    )
            total_lines = self._newline_count + int(self._saw_text and not self._ends_with_newline)
            output_lines = self._line_count(self._tail)
            output_bytes = len(self._tail.encode("utf-8"))
            truncation = TruncationResult(
                content=self._tail,
                truncated=self._was_truncated,
                truncated_by=self._truncated_by,
                output_lines=output_lines,
                total_lines=total_lines,
                first_line_exceeds_limit=False,
                total_bytes=self._total_text_bytes,
                output_bytes=output_bytes,
                last_line_partial=self._tail_starts_partial,
                max_lines=self.max_lines,
                max_bytes=self.max_bytes,
            )
            return OutputSnapshot(
                content=self._tail,
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
        self._newline_count += text.count("\n")
        self._saw_text = True
        self._ends_with_newline = text.endswith("\n")
        if "\n" in text:
            self._last_line_bytes = len(text.rsplit("\n", 1)[-1].encode("utf-8"))
        else:
            self._last_line_bytes += encoded_size

        candidate = self._tail + text
        prior_starts_partial = self._tail_starts_partial
        result = truncate_tail(candidate, max_lines=self.max_lines, max_bytes=self.max_bytes)
        start_index = len(candidate) - len(result.content)
        if start_index == 0:
            starts_partial = prior_starts_partial
        else:
            starts_partial = candidate[start_index - 1] != "\n"
        self._tail = result.content
        self._tail_starts_partial = starts_partial
        if result.truncated:
            self._was_truncated = True
            self._truncated_by = result.truncated_by

    @staticmethod
    def _line_count(content: str) -> int:
        if not content:
            return 0
        return content.count("\n") + int(not content.endswith("\n"))


__all__ = ["OutputSnapshot", "OutputSpool"]
