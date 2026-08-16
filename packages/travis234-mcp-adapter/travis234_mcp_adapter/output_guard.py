from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import threading


MAX_INLINE_BYTES = 50 * 1024
MAX_INLINE_LINES = 2_000
_PREVIEW_BYTES = 4_096


@dataclass(frozen=True)
class GuardedText:
    text: str
    spill_path: Path | None
    truncated_by: str | None


class SpillRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else None
        self._paths: set[Path] = set()
        self._lock = threading.Lock()

    def write(self, text: str) -> Path:
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="travis234-mcp-",
            suffix=".txt",
            dir=str(self.directory) if self.directory is not None else None,
            text=True,
        )
        path = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                stream.write(text)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            path.unlink(missing_ok=True)
            raise
        with self._lock:
            self._paths.add(path)
        return path

    def write_bytes(self, content: bytes, *, suffix: str = ".bin") -> Path:
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix="travis234-mcp-",
            suffix=suffix,
            dir=str(self.directory) if self.directory is not None else None,
        )
        path = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            path.unlink(missing_ok=True)
            raise
        with self._lock:
            self._paths.add(path)
        return path

    def cleanup(self) -> None:
        with self._lock:
            paths = tuple(self._paths)
            self._paths.clear()
        for path in paths:
            path.unlink(missing_ok=True)


class OutputGuard:
    def __init__(self, spills: SpillRegistry) -> None:
        self.spills = spills

    def guard(self, text: str) -> GuardedText:
        byte_overflow = len(text.encode("utf-8")) > MAX_INLINE_BYTES
        line_overflow = _line_count(text) > MAX_INLINE_LINES
        if not byte_overflow and not line_overflow:
            return GuardedText(text=text, spill_path=None, truncated_by=None)
        reasons = []
        if byte_overflow:
            reasons.append("bytes")
        if line_overflow:
            reasons.append("lines")
        truncated_by = "+".join(reasons)
        spill_path = self.spills.write(text)
        preview = _utf8_prefix(text, _PREVIEW_BYTES)
        guarded = (
            f"MCP output exceeded the inline {truncated_by} limit. "
            f"Full output retained as {spill_path.name}.\nPreview:\n{preview}"
        )
        return GuardedText(
            text=guarded,
            spill_path=spill_path,
            truncated_by=truncated_by,
        )


def _line_count(text: str) -> int:
    return 0 if not text else text.count("\n") + 1


def _utf8_prefix(text: str, maximum_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return text
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore") + "…"
