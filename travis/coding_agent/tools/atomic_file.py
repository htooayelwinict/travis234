"""Crash-resistant sibling-temp replacement for text files."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def atomic_replace_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    target = path.resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, existing_mode if existing_mode is not None else 0o644)
        os.replace(temporary, target)
        replaced = True
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if not replaced:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


__all__ = [
    "atomic_replace_text",
]
