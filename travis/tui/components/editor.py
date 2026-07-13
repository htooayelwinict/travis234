"""Components."""

from __future__ import annotations

import inspect
import math
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Optional

from travis.agent.types import AbortSignal
from travis.tui.fuzzy import fuzzy_filter
from travis.tui.keybindings import get_keybindings
from travis.tui.terminal_image import (
    allocate_image_id,
    get_capabilities,
    get_cell_dimensions,
    get_image_dimensions,
    image_fallback,
    render_image,
)
from travis.tui.utils import slice_by_column, truncate_to_width, visible_width, wrap_text

from travis.tui.components.autocomplete import (
    _call_autocomplete_method, _matched_sequence_length, _settle_autocomplete_result,
)
from travis.tui.components.base import Component


def _find_word_backward(value: str, cursor: int) -> int:
    index = max(0, min(cursor, len(value)))
    while index > 0 and value[index - 1].isspace():
        index -= 1
    if index == 0:
        return 0
    if _is_word_char(value[index - 1]):
        while index > 0 and _is_word_char(value[index - 1]):
            index -= 1
        return index
    while index > 0 and not value[index - 1].isspace() and not _is_word_char(value[index - 1]):
        index -= 1
    return index


def _find_word_forward(value: str, cursor: int) -> int:
    index = max(0, min(cursor, len(value)))
    length = len(value)
    while index < length and value[index].isspace():
        index += 1
    if index >= length:
        return length
    if _is_word_char(value[index]):
        while index < length and _is_word_char(value[index]):
            index += 1
        return index
    while index < length and not value[index].isspace() and not _is_word_char(value[index]):
        index += 1
    return index


def _is_word_char(char: str) -> bool:
    return char == "_" or char.isalnum()


CURSOR_MARKER = "\x1b_travis234:c\x07"

def _next_grapheme_text(text: str, cursor: int) -> str:
    cursor = max(0, min(cursor, len(text)))
    end = _next_grapheme_end(text, cursor)
    return text[cursor:end]


def _next_grapheme_end(text: str, cursor: int) -> int:
    cursor = max(0, min(cursor, len(text)))
    if cursor >= len(text):
        return len(text)

    index = cursor + 1
    if _is_regional_indicator(text[cursor]) and index < len(text) and _is_regional_indicator(text[index]):
        return index + 1

    index = _consume_grapheme_extenders(text, index)
    while index < len(text) and text[index] == "\u200d":
        index += 1
        if index >= len(text):
            break
        index += 1
        index = _consume_grapheme_extenders(text, index)
    return index


def _previous_grapheme_start(text: str, cursor: int) -> int:
    cursor = max(0, min(cursor, len(text)))
    if cursor <= 0:
        return 0

    index = cursor - 1
    if _is_regional_indicator(text[index]) and index > 0 and _is_regional_indicator(text[index - 1]):
        return index - 1

    while index > 0 and _is_grapheme_extender(text[index]):
        index -= 1
    while index > 0 and text[index - 1] == "\u200d":
        index = max(0, index - 2)
        while index > 0 and _is_grapheme_extender(text[index]):
            index -= 1
    return index


def _consume_grapheme_extenders(text: str, index: int) -> int:
    while index < len(text) and _is_grapheme_extender(text[index]):
        index += 1
    return index


def _is_plain_ascii_input(value: str) -> bool:
    return value.isascii() and all(" " <= char <= "~" for char in value)


_LEAKED_SGR_MOUSE_FRAGMENT_RE = re.compile(r"(?:\^\[\[|\[)?<\d+;\d+;\d+[Mm]")
_LEAKED_X10_MOUSE_FRAGMENT_RE = re.compile(r"\[M[\x60-\x7f][\x20-\uffff]{2}", re.S)


def _match_leaked_mouse_report_fragment(text: str, index: int) -> re.Match[str] | None:
    return _LEAKED_SGR_MOUSE_FRAGMENT_RE.match(text, index) or _LEAKED_X10_MOUSE_FRAGMENT_RE.match(text, index)


def _is_possible_leaked_mouse_report_fragment_prefix(text: str) -> bool:
    if not text or len(text) > 32:
        return False
    if text in {"^", "^[", "^[[", "[", "[<", "<", "[M"}:
        return True
    if re.fullmatch(r"(?:\^\[\[|\[)?<\d*(?:;\d*){0,2}", text):
        return True
    if re.fullmatch(r"(?:\^\[\[|\[)?<\d+(?:;\d*){0,2}[Mm]?", text):
        return True
    if text.startswith("[M") and len(text) < 5:
        return all(char >= " " for char in text[2:])
    return False


def _is_grapheme_extender(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.combining(char) != 0
        or unicodedata.category(char).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0x1F3FB <= codepoint <= 0x1F3FF
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _is_regional_indicator(char: str) -> bool:
    codepoint = ord(char)
    return 0x1F1E6 <= codepoint <= 0x1F1FF


class Input(Component):
    """Single-line input with basic editing and submit callback."""

    def __init__(
        self,
        value: str = "",
        *,
        prompt: str = "",
        on_submit: Callable[[str], None] | None = None,
        mask: bool = False,
    ) -> None:
        self.value = value
        self.prompt = prompt
        self.mask = mask
        self.cursor = len(value)
        self.on_submit = on_submit
        self.on_escape: Callable[[], None] | None = None
        self.on_escape: Callable[[], None] | None = None
        self.focused = False
        self.autocomplete_provider: object | None = None
        self._history: list[str] = []
        self._history_index = -1
        self._history_draft: tuple[str, int] | None = None
        self._kill_ring: list[str] = []
        self._last_action: str | None = None
        self._undo_stack: list[tuple[str, int]] = []
        self._pending_leaked_mouse_fragment = ""

    def set_autocomplete_provider(self, provider: object | None) -> None:
        self.autocomplete_provider = provider


    def set_value(self, value: str) -> None:
        self.value = value
        self.cursor = len(value)
        self._exit_history_browsing()

    def get_value(self) -> str:
        return self.value

    def set_history(self, history: list[str]) -> None:
        self._history = history
        self._exit_history_browsing()


    def add_to_history(self, text: str) -> None:
        trimmed = text.strip()
        if not trimmed:
            return
        if self._history and self._history[0] == trimmed:
            return
        self._history.insert(0, trimmed)
        del self._history[100:]


    def apply_autocomplete(self, *, force: bool = True) -> bool:
        if self.autocomplete_provider is None:
            return False
        suggestions = _call_autocomplete_method(
            self.autocomplete_provider,
            "get_suggestions",
            "getSuggestions",
            [self.value],
            0,
            self.cursor,
            {"signal": None, "force": force},
        )
        suggestions = _settle_autocomplete_result(suggestions)
        if not isinstance(suggestions, dict):
            return False
        items = suggestions.get("items")
        if not isinstance(items, list) or not items:
            return False
        prefix = str(suggestions.get("prefix", ""))
        result = _call_autocomplete_method(
            self.autocomplete_provider,
            "apply_completion",
            "applyCompletion",
            [self.value],
            0,
            self.cursor,
            items[0],
            prefix,
        )
        result = _settle_autocomplete_result(result)
        if not isinstance(result, dict):
            return False
        lines = result.get("lines")
        if not isinstance(lines, list) or not lines:
            return False
        self._push_undo()
        self.value = str(lines[0])
        self.cursor = int(result.get("cursorCol", len(self.value)))
        self._exit_history_browsing()
        return True

    def handle_input(self, data: str) -> None:
        if self._pending_leaked_mouse_fragment:
            data = self._pending_leaked_mouse_fragment + data
            self._pending_leaked_mouse_fragment = ""

        index = 0
        while index < len(data):
            if data.startswith("\x1b[200~", index):
                end = data.find("\x1b[201~", index + 6)
                if end == -1:
                    paste = data[index + 6 :]
                    index = len(data)
                else:
                    paste = data[index + 6 : end]
                    index = end + 6
                self._insert_paste(paste)
            elif mouse_match := re.match(
                r"\x1b(?:\[<\d+;\d+;\d+[Mm]|\[\d+;\d+;\d+[Mm]|\[M...)",
                data[index:],
            ):
                index += len(mouse_match.group(0))
            elif leaked_mouse_match := _match_leaked_mouse_report_fragment(data, index):
                index = leaked_mouse_match.end()
            elif _is_possible_leaked_mouse_report_fragment_prefix(data[index:]):
                self._pending_leaked_mouse_fragment = data[index:]
                break
            elif data.startswith("\x1b[A", index):
                if self._history:
                    self._navigate_history(-1)
                else:
                    self.cursor = 0
                    self._last_action = None
                index += 3
            elif data.startswith("\x1b[B", index):
                if self._history_index > -1:
                    self._navigate_history(1)
                else:
                    self.cursor = len(self.value)
                    self._last_action = None
                index += 3
            elif data.startswith("\x1b[D", index):
                self.cursor = _previous_grapheme_start(self.value, self.cursor)
                index += 3
            elif data.startswith("\x1b[C", index):
                self.cursor = _next_grapheme_end(self.value, self.cursor)
                self._last_action = None
                index += 3
            elif word_left_match := re.match(r"\x1b\[1;[35](?::[123])?D", data[index:]):
                self._move_word_backward()
                index += len(word_left_match.group(0))
            elif word_right_match := re.match(r"\x1b\[1;[35](?::[123])?C", data[index:]):
                self._move_word_forward()
                index += len(word_right_match.group(0))
            elif data.startswith("\x1b[3~", index):
                self._delete_char_forward()
                index += 4
            elif alt_delete_match := re.match(r"\x1b\[3;3(?::[123])?~", data[index:]):
                self._delete_word_forward()
                index += len(alt_delete_match.group(0))
            elif data.startswith(("\x1b[H", "\x1bOH", "\x1b[1~", "\x1b[7~"), index):
                self.cursor = 0
                self._last_action = None
                index += _matched_sequence_length(data, index, ("\x1b[H", "\x1bOH", "\x1b[1~", "\x1b[7~"))
            elif data.startswith(("\x1b[F", "\x1bOF", "\x1b[4~", "\x1b[8~"), index):
                self.cursor = len(self.value)
                self._last_action = None
                index += _matched_sequence_length(data, index, ("\x1b[F", "\x1bOF", "\x1b[4~", "\x1b[8~"))
            elif data.startswith("\x1bb", index):
                self._move_word_backward()
                index += 2
            elif data.startswith("\x1bf", index):
                self._move_word_forward()
                index += 2
            elif data.startswith("\x1b\x7f", index) or data.startswith("\x1b\b", index):
                self._delete_word_backward()
                index += 2
            elif data.startswith("\x1by", index):
                self._yank_pop()
                index += 2
            elif data.startswith("\x1bd", index):
                self._delete_word_forward()
                index += 2
            elif data.startswith("\x1b[45;5u", index):
                self._undo()
                index += 7
            else:
                char = data[index]
                if char == "\t":
                    self.apply_autocomplete(force=True)
                    self._last_action = None
                elif char in ("\x1b", "\x03"):
                    self._notify_escape()
                    self._last_action = None
                elif char in ("\r", "\n"):
                    submitted = self.value
                    if self.on_submit:
                        self.on_submit(submitted)
                    self.value = ""
                    self.cursor = 0
                    self._exit_history_browsing()
                    self._last_action = None
                elif char == "\x01":
                    self.cursor = 0
                    self._last_action = None
                elif char == "\x05":
                    self.cursor = len(self.value)
                    self._last_action = None
                elif char == "\x02":
                    self.cursor = _previous_grapheme_start(self.value, self.cursor)
                    self._last_action = None
                elif char == "\x06":
                    self.cursor = _next_grapheme_end(self.value, self.cursor)
                    self._last_action = None
                elif char == "\x17":
                    self._delete_word_backward()
                elif char == "\x04":
                    self._delete_char_forward()
                elif char == "\x15":
                    self._delete_to_line_start()
                elif char == "\x0b":
                    self._delete_to_line_end()
                elif char == "\x19":
                    self._yank()
                elif char in ("\x7f", "\b"):
                    if self.cursor > 0:
                        self._push_undo()
                        delete_from = _previous_grapheme_start(self.value, self.cursor)
                        self.value = self.value[:delete_from] + self.value[self.cursor :]
                        self.cursor = delete_from
                        self._exit_history_browsing()
                    self._last_action = None
                elif char >= " ":
                    if char.isspace() or self._last_action != "type-word":
                        self._push_undo()
                    self.value = self.value[: self.cursor] + char + self.value[self.cursor :]
                    self.cursor += 1
                    self._exit_history_browsing()
                    self._last_action = "type-word"
                index += 1

    def render(self, width: int) -> list[str]:
        prompt_width = visible_width(self.prompt)
        available_width = width - prompt_width
        if available_width <= 0:
            return [truncate_to_width(self.prompt, width)]
        display_value = ("*" * len(self.value)) if self.mask else self.value
        display_cursor = max(0, min(self.cursor, len(display_value)))
        if _is_plain_ascii_input(display_value):
            return self._render_plain_ascii(width, prompt_width, available_width, display_value, display_cursor)

        visible_text = ""
        cursor_display = display_cursor
        total_width = visible_width(display_value)

        if total_width < available_width:
            visible_text = display_value
        else:
            scroll_width = available_width - 1 if display_cursor == len(display_value) else available_width
            cursor_col = visible_width(display_value[:display_cursor])
            if scroll_width > 0:
                half_width = scroll_width // 2
                if cursor_col < half_width:
                    start_col = 0
                elif cursor_col > total_width - half_width:
                    start_col = max(0, total_width - scroll_width)
                else:
                    start_col = max(0, cursor_col - half_width)
                visible_text = slice_by_column(display_value, start_col, scroll_width, strict=True)
                before_cursor = slice_by_column(display_value, start_col, max(0, cursor_col - start_col), strict=True)
                cursor_display = len(before_cursor)
            else:
                cursor_display = 0

        before_cursor = visible_text[:cursor_display]
        at_cursor = _next_grapheme_text(visible_text, cursor_display) or " "
        after_cursor = visible_text[cursor_display + len(at_cursor) :]
        marker = CURSOR_MARKER if self.focused else ""
        text_with_cursor = before_cursor + marker + f"\x1b[7m{at_cursor}\x1b[27m" + after_cursor
        visual_length = visible_width(text_with_cursor)
        padding = " " * max(0, available_width - visual_length)
        return [truncate_to_width(self.prompt + text_with_cursor + padding, width)]

    def _render_plain_ascii(
        self,
        width: int,
        prompt_width: int,
        available_width: int,
        value: str | None = None,
        cursor: int | None = None,
    ) -> list[str]:
        value = self.value if value is None else value
        cursor = self.cursor if cursor is None else cursor
        cursor = max(0, min(cursor, len(value)))
        total_width = len(value)
        cursor_display = cursor

        if total_width < available_width:
            visible_text = value
        else:
            scroll_width = available_width - 1 if cursor == len(value) else available_width
            if scroll_width > 0:
                half_width = scroll_width // 2
                if cursor < half_width:
                    start_col = 0
                elif cursor > total_width - half_width:
                    start_col = max(0, total_width - scroll_width)
                else:
                    start_col = max(0, cursor - half_width)
                visible_text = value[start_col : start_col + scroll_width]
                cursor_display = max(0, min(len(visible_text), cursor - start_col))
            else:
                visible_text = ""
                cursor_display = 0

        before_cursor = visible_text[:cursor_display]
        at_cursor = visible_text[cursor_display : cursor_display + 1] or " "
        after_cursor = visible_text[cursor_display + len(at_cursor) :]
        marker = CURSOR_MARKER if self.focused else ""
        text_with_cursor = before_cursor + marker + f"\x1b[7m{at_cursor}\x1b[27m" + after_cursor
        visual_length = visible_width(text_with_cursor)
        padding = " " * max(0, available_width - visual_length)
        return [truncate_to_width(self.prompt + text_with_cursor + padding, width)]

    def _push_kill(self, text: str, *, prepend: bool, accumulate: bool) -> None:
        if not text:
            return
        if accumulate and self._kill_ring:
            if prepend:
                self._kill_ring[0] = text + self._kill_ring[0]
            else:
                self._kill_ring[0] = self._kill_ring[0] + text
        else:
            self._kill_ring.insert(0, text)
        del self._kill_ring[32:]

    def _push_undo(self) -> None:
        self._undo_stack.append((self.value, self.cursor))

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self.value, self.cursor = self._undo_stack.pop()
        self._last_action = None

    def _insert_paste(self, paste: str) -> None:
        self._push_undo()
        clean = paste.replace("\r\n", "").replace("\r", "").replace("\n", "").replace("\t", "    ")
        self.value = self.value[: self.cursor] + clean + self.value[self.cursor :]
        self.cursor += len(clean)
        self._exit_history_browsing()
        self._last_action = None

    def _delete_char_forward(self) -> None:
        self._last_action = None
        if self.cursor < len(self.value):
            self._push_undo()
            delete_to = _next_grapheme_end(self.value, self.cursor)
            self.value = self.value[: self.cursor] + self.value[delete_to:]
            self._exit_history_browsing()

    def _move_word_backward(self) -> None:
        if self.cursor > 0:
            self.cursor = _find_word_backward(self.value, self.cursor)
        self._last_action = None

    def _move_word_forward(self) -> None:
        if self.cursor < len(self.value):
            self.cursor = _find_word_forward(self.value, self.cursor)
        self._last_action = None

    def _delete_word_backward(self) -> None:
        if self.cursor == 0:
            return
        was_kill = self._last_action == "kill"
        self._push_undo()
        delete_from = _find_word_backward(self.value, self.cursor)
        deleted = self.value[delete_from : self.cursor]
        self._push_kill(deleted, prepend=True, accumulate=was_kill)
        self.value = self.value[:delete_from] + self.value[self.cursor :]
        self.cursor = delete_from
        self._exit_history_browsing()
        self._last_action = "kill"

    def _delete_word_forward(self) -> None:
        if self.cursor >= len(self.value):
            return
        was_kill = self._last_action == "kill"
        self._push_undo()
        delete_to = _find_word_forward(self.value, self.cursor)
        deleted = self.value[self.cursor : delete_to]
        self._push_kill(deleted, prepend=False, accumulate=was_kill)
        self.value = self.value[: self.cursor] + self.value[delete_to:]
        self._exit_history_browsing()
        self._last_action = "kill"

    def _delete_to_line_start(self) -> None:
        if self.cursor == 0:
            return
        self._push_undo()
        deleted = self.value[: self.cursor]
        self._push_kill(deleted, prepend=True, accumulate=self._last_action == "kill")
        self.value = self.value[self.cursor :]
        self.cursor = 0
        self._exit_history_browsing()
        self._last_action = "kill"

    def _delete_to_line_end(self) -> None:
        if self.cursor >= len(self.value):
            return
        self._push_undo()
        deleted = self.value[self.cursor :]
        self._push_kill(deleted, prepend=False, accumulate=self._last_action == "kill")
        self.value = self.value[: self.cursor]
        self._exit_history_browsing()
        self._last_action = "kill"

    def _yank(self) -> None:
        if not self._kill_ring:
            return
        self._push_undo()
        text = self._kill_ring[0]
        self.value = self.value[: self.cursor] + text + self.value[self.cursor :]
        self.cursor += len(text)
        self._exit_history_browsing()
        self._last_action = "yank"

    def _yank_pop(self) -> None:
        if self._last_action != "yank" or len(self._kill_ring) <= 1:
            return
        self._push_undo()
        previous = self._kill_ring[0]
        start = max(0, self.cursor - len(previous))
        if self.value[start : self.cursor] == previous:
            self.value = self.value[:start] + self.value[self.cursor :]
            self.cursor = start
        self._kill_ring.append(self._kill_ring.pop(0))
        text = self._kill_ring[0]
        self.value = self.value[: self.cursor] + text + self.value[self.cursor :]
        self.cursor += len(text)
        self._exit_history_browsing()
        self._last_action = "yank"

    def _navigate_history(self, direction: int) -> None:
        self._last_action = None
        if not self._history:
            return

        new_index = self._history_index - direction
        if new_index < -1 or new_index >= len(self._history):
            return

        if self._history_index == -1 and new_index >= 0:
            self._push_undo()
            self._history_draft = (self.value, self.cursor)

        self._history_index = new_index
        if self._history_index == -1:
            draft = self._history_draft
            self._history_draft = None
            if draft is None:
                self.value = ""
                self.cursor = 0
            else:
                self.value, self.cursor = draft
            return

        self.value = self._history[self._history_index] or ""
        self.cursor = 0 if direction == -1 else len(self.value)

    def _exit_history_browsing(self) -> None:
        self._history_index = -1
        self._history_draft = None

    def _notify_escape(self) -> None:
        callbacks: list[Callable[[], None]] = []
        seen: set[int] = set()
        for name in ("on_escape", "onEscape"):
            callback = getattr(self, name, None)
            if not callable(callback):
                continue
            marker = id(callback)
            if marker in seen:
                continue
            seen.add(marker)
            callbacks.append(callback)
        for callback in callbacks:
            callback()
