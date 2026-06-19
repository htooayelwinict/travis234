"""Differential rendering engine. Port of pi/packages/tui/src/tui.ts (TUI core)."""

from __future__ import annotations

from dataclasses import dataclass

from appv22.tui.component import Container
from appv22.tui.terminal import Terminal
from appv22.tui.utils import truncate_to_width

_CLEAR_SCREEN = "\x1b[2J\x1b[H"


def _move_to_line(index: int) -> str:
    return f"\x1b[{index + 1};1H"


def _clear_line() -> str:
    return "\x1b[2K"


@dataclass
class RenderInfo:
    full: bool
    first_changed: int
    last_changed: int
    lines: list[str]


class TUI(Container):
    """Container that diff-renders its frame to a Terminal, emitting minimal ANSI."""

    def __init__(self, terminal: Terminal) -> None:
        super().__init__()
        self.terminal = terminal
        self.previous_lines: list[str] = []
        self._last_width: int | None = None
        self.last_render: RenderInfo | None = None

    def request_render(self, force: bool = False) -> RenderInfo:
        return self._do_render(force)

    def _do_render(self, force: bool) -> RenderInfo:
        width = self.terminal.columns
        new_lines = [truncate_to_width(line, width) for line in self.render(width)]

        size_changed = self._last_width is not None and self._last_width != width
        first_render = not self.previous_lines

        if force or first_render or size_changed:
            info = self._full_render(new_lines)
        else:
            info = self._diff_render(new_lines)

        self.previous_lines = new_lines
        self._last_width = width
        self.last_render = info
        return info

    def _full_render(self, new_lines: list[str]) -> RenderInfo:
        self.terminal.write(_CLEAR_SCREEN + "\r\n".join(new_lines))
        return RenderInfo(full=True, first_changed=0, last_changed=max(0, len(new_lines) - 1), lines=new_lines)

    def _diff_render(self, new_lines: list[str]) -> RenderInfo:
        old_lines = self.previous_lines
        max_len = max(len(old_lines), len(new_lines))

        first_changed = -1
        for index in range(max_len):
            old = old_lines[index] if index < len(old_lines) else None
            new = new_lines[index] if index < len(new_lines) else None
            if old != new:
                first_changed = index
                break
        if first_changed == -1:
            return RenderInfo(full=False, first_changed=-1, last_changed=-1, lines=new_lines)

        last_changed = first_changed
        for index in range(max_len - 1, first_changed - 1, -1):
            old = old_lines[index] if index < len(old_lines) else None
            new = new_lines[index] if index < len(new_lines) else None
            if old != new:
                last_changed = index
                break

        buffer: list[str] = []
        for index in range(first_changed, last_changed + 1):
            line = new_lines[index] if index < len(new_lines) else ""
            buffer.append(_move_to_line(index) + _clear_line() + line)
        self.terminal.write("".join(buffer))
        return RenderInfo(full=False, first_changed=first_changed, last_changed=last_changed, lines=new_lines)
