"""Cross-process lock for one session JSONL file."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class SessionFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(f"{path.name}.lock")
        self._handle = None

    def __enter__(self) -> "SessionFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.lock_path.open("a+b")
        os.fchmod(self._handle.fileno(), 0o600)
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


__all__ = ["SessionFileLock"]
