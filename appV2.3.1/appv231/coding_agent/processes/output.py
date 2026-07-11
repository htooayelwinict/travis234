"""Private append-only output with safe, deterministic cursor reads."""

from __future__ import annotations

import codecs
import os
import shutil
import tempfile
import threading
from pathlib import Path

from appv231.coding_agent.processes.types import InvalidCursorError, OutputSlice


class _TerminalSanitizer:
    _STRING_INTRODUCERS = {"]", "P", "X", "^", "_"}

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
        elif character in {"\t", "\n"}:
            output.append(character)
        elif ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F:
            return
        else:
            output.append(character)


class SanitizedOutputSpool:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._directory.chmod(0o700)
        fd, path = tempfile.mkstemp(prefix="output-", suffix=".log", dir=self._directory)
        os.fchmod(fd, 0o600)
        self._path = Path(path)
        self._file = os.fdopen(fd, "w+b", buffering=0)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._sanitizer = _TerminalSanitizer()
        self._written_bytes = 0
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
            text = self._decoder.decode(b"", final=True)
            self._write_text(self._sanitizer.feed(text, final=True))
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

    def close(self, *, remove: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self.finish()
            self._file.close()
            self._closed = True
        if remove:
            self._path.unlink(missing_ok=True)

    def _write_text(self, text: str) -> None:
        if not text:
            return
        encoded = text.encode("utf-8")
        self._file.seek(0, os.SEEK_END)
        self._file.write(encoded)
        self._written_bytes += len(encoded)

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


__all__ = ["SanitizedOutputSpool"]
