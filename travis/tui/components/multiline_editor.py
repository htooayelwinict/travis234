"""Multiline main-prompt editor."""

from __future__ import annotations

import bisect
from typing import Callable

from travis.tui.components.editor import CURSOR_MARKER, Input, _next_grapheme_end
from travis.tui.utils import truncate_to_width, visible_width, wrap_text


class Editor(Input):
    """Multiline main-prompt editor built on the existing Input editing owners."""

    _INSERT_NEWLINE_KEYS = {"\x1b[13;2u", "\x1b[13;3u", "\x1b\r", "\x1b\n"}
    _HOME_KEYS = ("\x1b[H", "\x1bOH", "\x1b[1~", "\x1b[7~")
    _END_KEYS = ("\x1b[F", "\x1bOF", "\x1b[4~", "\x1b[8~")

    def __init__(
        self,
        value: str = "",
        *,
        prompt: str = "",
        on_submit: Callable[[str], None] | None = None,
        max_visible_lines: int = 8,
        theme_context: object | None = None,
    ) -> None:
        super().__init__(
            value=value,
            prompt=prompt,
            on_submit=on_submit,
            theme_context=theme_context,
        )
        self.max_visible_lines = max(1, int(max_visible_lines))
        self._sticky_visual_column: int | None = None

    def set_value(self, value: str) -> None:
        super().set_value(value)
        self._sticky_visual_column = None

    def handle_input(self, data: str) -> None:
        if data in self._INSERT_NEWLINE_KEYS:
            self._insert_newline()
            return
        if data == "\x1b[A" and "\n" in self.value:
            self._move_vertical(-1)
            return
        if data == "\x1b[B" and "\n" in self.value:
            self._move_vertical(1)
            return
        if data == "\x1b[5~" and "\n" in self.value:
            self._move_vertical(-(self.max_visible_lines - 1 or 1))
            return
        if data == "\x1b[6~" and "\n" in self.value:
            self._move_vertical(self.max_visible_lines - 1 or 1)
            return
        if data in self._HOME_KEYS or data == "\x01":
            self.cursor = self._line_bounds()[0]
            self._sticky_visual_column = None
            self._last_action = None
            return
        if data in self._END_KEYS or data == "\x05":
            self.cursor = self._line_bounds()[1]
            self._sticky_visual_column = None
            self._last_action = None
            return
        super().handle_input(data)
        self._sticky_visual_column = None

    def render(self, width: int) -> list[str]:
        width = max(4, int(width))
        body_width = max(1, width - 4)
        prompt_width = min(visible_width(self.prompt), max(0, body_width - 1))
        content_width = max(1, body_width - prompt_width)
        display_value = self.value
        visual_rows: list[str] = []
        cursor_visual_row = 0
        line_start = 0
        logical_lines = display_value.split("\n")
        for logical_index, logical_line in enumerate(logical_lines):
            line_end = line_start + len(logical_line)
            cursor_on_line = line_start <= self.cursor <= line_end
            local_cursor = self.cursor - line_start if cursor_on_line else None
            display_line = logical_line
            if local_cursor is not None and self.focused:
                next_end = _next_grapheme_end(display_line, local_cursor)
                at_cursor = display_line[local_cursor:next_end] or " "
                display_line = (
                    display_line[:local_cursor]
                    + CURSOR_MARKER
                    + "\x1b[7m"
                    + at_cursor
                    + "\x1b[27m"
                    + display_line[next_end:]
                )
            chunks = wrap_text(display_line, content_width)
            for chunk in chunks:
                if CURSOR_MARKER in chunk:
                    cursor_visual_row = len(visual_rows)
                visual_rows.append(chunk)
            line_start = line_end + (1 if logical_index < len(logical_lines) - 1 else 0)

        if not visual_rows:
            visual_rows = [CURSOR_MARKER + "\x1b[7m \x1b[27m" if self.focused else ""]
        if len(visual_rows) > self.max_visible_lines:
            start = max(
                0,
                min(cursor_visual_row - self.max_visible_lines + 1, len(visual_rows) - self.max_visible_lines),
            )
            visual_rows = visual_rows[start : start + self.max_visible_lines]

        theme = getattr(self.theme_context, "theme", None)
        horizontal = "─" * (width - 2)
        top = "╭" + horizontal + "╮"
        bottom = "╰" + horizontal + "╯"
        if theme is not None:
            top = theme.fg("borderAccent", top)
            bottom = theme.fg("borderAccent", bottom)
        rows = [top]
        for index, chunk in enumerate(visual_rows):
            prompt = self.prompt if index == 0 else " " * prompt_width
            prompt = truncate_to_width(prompt, prompt_width)
            if theme is not None:
                prompt = theme.fg("accent", prompt)
                chunk = theme.fg("text", chunk)
                left = theme.fg("borderAccent", "│")
                right = theme.fg("borderAccent", "│")
            else:
                left = right = "│"
            padding = " " * max(0, body_width - prompt_width - visible_width(chunk))
            rows.append(truncate_to_width(f"{left} {prompt}{chunk}{padding} {right}", width))
        rows.append(bottom)
        return rows

    def _insert_paste(self, paste: str) -> None:
        self._push_undo()
        clean = paste.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
        self.value = self.value[: self.cursor] + clean + self.value[self.cursor :]
        self.cursor += len(clean)
        self._exit_history_browsing()
        self._last_action = None
        self._sticky_visual_column = None

    def _insert_newline(self) -> None:
        self._push_undo()
        self.value = self.value[: self.cursor] + "\n" + self.value[self.cursor :]
        self.cursor += 1
        self._exit_history_browsing()
        self._last_action = None
        self._sticky_visual_column = None

    def _line_starts(self) -> list[int]:
        return [0, *(index + 1 for index, char in enumerate(self.value) if char == "\n")]

    def _line_bounds(self) -> tuple[int, int]:
        starts = self._line_starts()
        line_index = max(0, bisect.bisect_right(starts, self.cursor) - 1)
        start = starts[line_index]
        end = starts[line_index + 1] - 1 if line_index + 1 < len(starts) else len(self.value)
        return start, end

    def _move_vertical(self, delta: int) -> None:
        starts = self._line_starts()
        current_index = max(0, bisect.bisect_right(starts, self.cursor) - 1)
        current_start, current_end = self._line_bounds()
        current_column = visible_width(self.value[current_start : min(self.cursor, current_end)])
        if self._sticky_visual_column is None:
            self._sticky_visual_column = current_column
        target_index = max(0, min(current_index + int(delta), len(starts) - 1))
        target_start = starts[target_index]
        target_end = starts[target_index + 1] - 1 if target_index + 1 < len(starts) else len(self.value)
        target = self.value[target_start:target_end]
        self.cursor = target_start + _index_for_visual_column(target, self._sticky_visual_column)
        self._last_action = None

    def _delete_to_line_start(self) -> None:
        line_start, _line_end = self._line_bounds()
        if self.cursor <= line_start:
            return
        self._push_undo()
        deleted = self.value[line_start : self.cursor]
        self._push_kill(deleted, prepend=True, accumulate=self._last_action == "kill")
        self.value = self.value[:line_start] + self.value[self.cursor :]
        self.cursor = line_start
        self._exit_history_browsing()
        self._last_action = "kill"

    def _delete_to_line_end(self) -> None:
        _line_start, line_end = self._line_bounds()
        if self.cursor >= line_end:
            return
        self._push_undo()
        deleted = self.value[self.cursor:line_end]
        self._push_kill(deleted, prepend=False, accumulate=self._last_action == "kill")
        self.value = self.value[: self.cursor] + self.value[line_end:]
        self._exit_history_browsing()
        self._last_action = "kill"


def _index_for_visual_column(text: str, target_column: int) -> int:
    index = 0
    column = 0
    while index < len(text):
        next_index = _next_grapheme_end(text, index)
        grapheme_width = visible_width(text[index:next_index])
        if column + grapheme_width > target_column:
            break
        column += grapheme_width
        index = next_index
    return index
