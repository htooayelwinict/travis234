"""Terminal abstraction. Port of pi/packages/tui/src/terminal.ts (subset)."""

from __future__ import annotations

import sys
from typing import Protocol


class Terminal(Protocol):
    columns: int
    rows: int

    def write(self, data: str) -> None: ...

    def set_title(self, title: str) -> None: ...


class FakeTerminal:
    """Records writes for tests."""

    def __init__(self, columns: int = 80, rows: int = 24) -> None:
        self.columns = columns
        self.rows = rows
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)

    def set_title(self, title: str) -> None:
        self.write(f"\x1b]0;{title}\x07")

    setTitle = set_title

    @property
    def output(self) -> str:
        return "".join(self.writes)


class ProcessTerminal:
    """Real stdout-backed terminal."""

    def __init__(self) -> None:
        size = _terminal_size()
        self.columns = size[0]
        self.rows = size[1]

    def write(self, data: str) -> None:  # pragma: no cover - real IO
        sys.stdout.write(data)
        sys.stdout.flush()

    def set_title(self, title: str) -> None:  # pragma: no cover - real IO
        self.write(f"\x1b]0;{title}\x07")

    setTitle = set_title


def _terminal_size() -> tuple[int, int]:
    try:
        import shutil

        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines
    except Exception:  # pragma: no cover
        return 80, 24
