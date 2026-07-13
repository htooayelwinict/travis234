"""Private append-only output with safe, deterministic cursor reads."""

from __future__ import annotations

import codecs
import os
import shutil
import tempfile
import threading
from pathlib import Path

from travis.coding_agent.processes.types import InvalidCursorError, OutputSlice
from travis.coding_agent.processes.types import ProcessOutputLimitError
from travis.coding_agent.output_utils import line_count as _line_count
from travis.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    truncate_tail,
)

DEFAULT_MAX_PROCESS_SPOOL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_LIVE_SPOOL_BYTES = 512 * 1024 * 1024


class LiveSpoolBudget:
    def __init__(self, limit: int) -> None:
        self._limit = max(0, limit)
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def available(self) -> int:
        with self._lock:
            return self._limit - self._used

    def reserve_up_to(self, requested: int) -> int:
        with self._lock:
            granted = min(max(0, requested), self._limit - self._used)
            self._used += granted
            return granted

    def release(self, count: int) -> None:
        with self._lock:
            if count < 0 or count > self._used:
                raise RuntimeError("live-spool accounting invariant violated")
            self._used -= count


class _TerminalSanitizer:
    _STRING_INTRODUCERS = {"]", "P", "X", "^", "_"}
    _C1_STRING_INTRODUCERS = {"\x90", "\x98", "\x9d", "\x9e", "\x9f"}

    def __init__(self) -> None:
        self._state = "normal"
        self._pending_cr = False

    def feed(self, text: str, *, final: bool = False) -> str:
        output: list[str] = []
        for character in text:
            if self._state == "normal":
                self._normal(character, output)
            elif self._state == "escape":
                if character == "[":
                    self._state = "csi"
                elif character in self._STRING_INTRODUCERS:
                    self._state = "string"
                else:
                    self._state = "normal"
            elif self._state == "csi":
                if 0x40 <= ord(character) <= 0x7E:
                    self._state = "normal"
            elif self._state == "string":
                if character == "\x07" or character == "\x9c":
                    self._state = "normal"
                elif character == "\x1b":
                    self._state = "string_escape"
            elif self._state == "string_escape":
                if character == "\\":
                    self._state = "normal"
                elif character != "\x1b":
                    self._state = "string"
        if final:
            if self._pending_cr:
                output.append("\n")
                self._pending_cr = False
            self._state = "normal"
        return "".join(output)

    def _normal(self, character: str, output: list[str]) -> None:
        if self._pending_cr:
            output.append("\n")
            self._pending_cr = False
            if character == "\n":
                return
        if character == "\r":
            self._pending_cr = True
        elif character == "\x1b":
            self._state = "escape"
        elif character == "\x9b":
            self._state = "csi"
        elif character in self._C1_STRING_INTRODUCERS:
            self._state = "string"
        elif character in {"\t", "\n"}:
            output.append(character)
        elif ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F:
            return
        else:
            output.append(character)


class SanitizedOutputSpool:
    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        max_bytes: int = DEFAULT_MAX_PROCESS_SPOOL_BYTES,
        live_budget: LiveSpoolBudget | None = None,
        pressure_reclaimer=None,
    ) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._directory.chmod(0o700)
        fd, path = tempfile.mkstemp(prefix="output-", suffix=".log", dir=self._directory)
        os.fchmod(fd, 0o600)
        self._path = Path(path)
        self._file = os.fdopen(fd, "w+b", buffering=0)
        self._max_bytes = max(0, max_bytes)
        self._live_budget = live_budget or LiveSpoolBudget(self._max_bytes)
        self._pressure_reclaimer = pressure_reclaimer
        self._reserved_bytes = 0
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._sanitizer = _TerminalSanitizer()
        self._written_bytes = 0
        self._tail = ""
        self._tail_starts_partial = False
        self._newline_count = 0
        self._saw_text = False
        self._ends_with_newline = False
        self._last_line_bytes = 0
        self._was_truncated = False
        self._truncated_by: str | None = None
        self._finished = False
        self._closed = False
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def size(self) -> int:
        with self._lock:
            return self._written_bytes

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    def append(self, data: bytes) -> None:
        with self._lock:
            if self._finished:
                raise RuntimeError("Cannot append to a finished output spool")
            text = self._decoder.decode(data, final=False)
            self._write_text(self._sanitizer.feed(text))

    def finish(self) -> None:
        with self._lock:
            if self._finished:
                return
            try:
                text = self._decoder.decode(b"", final=True)
                self._write_text(self._sanitizer.feed(text, final=True))
            finally:
                self._file.flush()
                self._finished = True

    def read(self, cursor: int, max_bytes: int) -> OutputSlice:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        with self._lock:
            if cursor < 0 or cursor > self._written_bytes:
                raise InvalidCursorError(cursor, self._written_bytes)
            self._file.flush()
            self._file.seek(cursor)
            data = self._file.read(min(max_bytes, self._written_bytes - cursor))
            if cursor < self._written_bytes:
                self._file.seek(cursor)
                first = self._file.read(1)
                if first and first[0] & 0xC0 == 0x80:
                    raise InvalidCursorError(cursor, self._written_bytes)
            valid = self._valid_utf8_prefix(data)
            return OutputSlice(
                text=valid.decode("utf-8"),
                cursor=cursor,
                next_cursor=cursor + len(valid),
            )

    def export_copy(self, directory: str | os.PathLike[str]) -> Path:
        with self._lock:
            if not self._finished:
                raise RuntimeError("Cannot export active output spool")
            destination_dir = Path(directory)
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, destination = tempfile.mkstemp(prefix="process-output-", suffix=".log", dir=destination_dir)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as target, self._path.open("rb") as source:
                    shutil.copyfileobj(source, target)
            except BaseException:
                Path(destination).unlink(missing_ok=True)
                raise
            return Path(destination)

    def tail_snapshot(
        self,
        *,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> TruncationResult:
        if max_lines != DEFAULT_MAX_LINES or max_bytes != DEFAULT_MAX_BYTES:
            raise ValueError("Managed process tail uses the coding-agent output limits")
        with self._lock:
            total_lines = self._newline_count + int(self._saw_text and not self._ends_with_newline)
            output_lines = _line_count(self._tail)
            output_bytes = len(self._tail.encode("utf-8"))
            return TruncationResult(
                content=self._tail,
                truncated=self._was_truncated,
                truncated_by=self._truncated_by,
                output_lines=output_lines,
                total_lines=total_lines,
                first_line_exceeds_limit=False,
                total_bytes=self._written_bytes,
                output_bytes=output_bytes,
                last_line_partial=self._tail_starts_partial,
                max_lines=max_lines,
                max_bytes=max_bytes,
            )

    def get_last_line_bytes(self) -> int:
        with self._lock:
            return self._last_line_bytes

    def close(self, *, remove: bool = True) -> None:
        failure: BaseException | None = None
        with self._lock:
            if self._closed:
                return
            try:
                self.finish()
            except BaseException as error:  # noqa: BLE001 - cleanup remains mandatory.
                failure = error
            finally:
                self._file.close()
                self._live_budget.release(self._reserved_bytes)
                self._reserved_bytes = 0
                self._closed = True
        if remove:
            self._path.unlink(missing_ok=True)
        if failure is not None:
            raise failure

    def _write_text(self, text: str) -> None:
        if not text:
            return
        encoded = text.encode("utf-8")
        remaining = max(0, self._max_bytes - self._written_bytes)
        requested = min(len(encoded), remaining)
        granted = self._live_budget.reserve_up_to(requested)
        if granted < requested and self._pressure_reclaimer is not None:
            self._live_budget.release(granted)
            self._pressure_reclaimer(requested)
            granted = self._live_budget.reserve_up_to(requested)
        prefix = self._valid_utf8_prefix(encoded[:granted])
        self._live_budget.release(granted - len(prefix))
        if prefix:
            start = self._written_bytes
            try:
                self._file.seek(start)
                written = 0
                while written < len(prefix):
                    count = self._file.write(prefix[written:])
                    if count is None or count <= 0:
                        raise OSError("output spool write made no progress")
                    written += count
            except BaseException:
                self._file.seek(start)
                self._file.truncate(start)
                self._live_budget.release(len(prefix))
                raise
            self._reserved_bytes += len(prefix)
            self._written_bytes += len(prefix)
            text = prefix.decode("utf-8")
        else:
            text = ""
        self._newline_count += text.count("\n")
        if text:
            self._saw_text = True
            self._ends_with_newline = text.endswith("\n")
            if "\n" in text:
                self._last_line_bytes = len(text.rsplit("\n", 1)[-1].encode("utf-8"))
            else:
                self._last_line_bytes += len(prefix)
            candidate = self._tail + text
            prior_starts_partial = self._tail_starts_partial
            result = truncate_tail(candidate, max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES)
            start_index = len(candidate) - len(result.content)
            self._tail_starts_partial = (
                prior_starts_partial if start_index == 0 else candidate[start_index - 1] != "\n"
            ) or result.last_line_partial
            self._tail = result.content
            if result.truncated:
                self._was_truncated = True
                self._truncated_by = result.truncated_by
        if len(prefix) < len(encoded):
            raise ProcessOutputLimitError("Process sanitized output limit reached")

    @staticmethod
    def _valid_utf8_prefix(data: bytes) -> bytes:
        if not data:
            return b""
        try:
            data.decode("utf-8")
            return data
        except UnicodeDecodeError as error:
            if error.reason == "unexpected end of data":
                prefix = data[: error.start]
                prefix.decode("utf-8")
                return prefix
            raise

__all__ = [
    "DEFAULT_MAX_LIVE_SPOOL_BYTES",
    "DEFAULT_MAX_PROCESS_SPOOL_BYTES",
    "LiveSpoolBudget",
    "SanitizedOutputSpool",
]
