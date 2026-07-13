"""Streaming output accumulator. Port of pi tools/output-accumulator.ts."""

from __future__ import annotations

import codecs
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from appv23.coding_agent.tools.truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, TruncationResult, truncate_tail


@dataclass
class OutputSnapshot:
    content: str
    truncation: TruncationResult
    full_output_path: str | None = None


class OutputAccumulator:
    def __init__(
        self,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        temp_file_prefix: str = "pi-output",
    ) -> None:
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self.temp_file_prefix = temp_file_prefix
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._raw = bytearray()
        self._text = ""
        self._finished = False
        self._temp_file_path: str | None = None

    def append(self, data: bytes) -> None:
        if self._finished:
            raise RuntimeError("Cannot append to a finished output accumulator")
        self._raw.extend(data)
        self._text += self._decoder.decode(data, final=False)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._text += self._decoder.decode(b"", final=True)

    def snapshot(self, persist_if_truncated: bool = False) -> OutputSnapshot:
        truncation = truncate_tail(self._text, max_lines=self.max_lines, max_bytes=self.max_bytes)
        if persist_if_truncated and truncation.truncated:
            self._ensure_temp_file()
        return OutputSnapshot(content=truncation.content, truncation=truncation, full_output_path=self._temp_file_path)

    def close_temp_file(self) -> None:
        return None

    def get_last_line_bytes(self) -> int:
        if self._text.endswith("\n"):
            return 0
        return len(self._text.rsplit("\n", 1)[-1].encode("utf-8"))

    def _ensure_temp_file(self) -> None:
        if self._temp_file_path is not None:
            return
        temp_dir = Path(tempfile.gettempdir())
        self._temp_file_path = str(temp_dir / f"{self.temp_file_prefix}-{secrets.token_hex(8)}.log")
        Path(self._temp_file_path).write_bytes(bytes(self._raw))
