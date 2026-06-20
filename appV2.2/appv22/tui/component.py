"""Components. Port of pi/packages/tui/src/tui.ts (Component/Container) + components/*."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from appv22.tui.utils import truncate_to_width, visible_width, wrap_text

CURSOR_MARKER = "\x1b_pi:c\x07"


class Component:
    """Base component: render(width) -> list of lines (one string per visual line)."""

    def render(self, width: int) -> list[str]:
        raise NotImplementedError

    def handle_input(self, data: str) -> None:  # pragma: no cover - optional
        pass

    def invalidate(self) -> None:
        pass


class Container(Component):
    def __init__(self, children: Optional[list[Component]] = None) -> None:
        self.children: list[Component] = list(children or [])

    def add(self, component: Component) -> Component:
        self.children.append(component)
        return component

    def remove(self, component: Component) -> None:
        if component in self.children:
            self.children.remove(component)

    def clear(self) -> None:
        self.children = []

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self.children:
            lines.extend(child.render(width))
        return lines

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()


class Text(Component):
    def __init__(self, text: str = "") -> None:
        self._text = text
        self._cache: list[str] | None = None
        self._cache_key: tuple[str, int] | None = None

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._cache = None

    def invalidate(self) -> None:
        self._cache = None

    def render(self, width: int) -> list[str]:
        key = (self._text, width)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        lines: list[str] = []
        for raw in self._text.split("\n"):
            lines.extend(wrap_text(raw, width))
        self._cache = lines
        self._cache_key = key
        return lines


class Markdown(Text):
    """Small terminal markdown renderer for assistant/user content."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)

    def render(self, width: int) -> list[str]:
        rendered = _render_markdown_text(self._text)
        lines: list[str] = []
        for raw in rendered.split("\n"):
            lines.extend(wrap_text(raw, width))
        return lines


class SimpleAutocompleteProvider:
    """Small Python equivalent of pi-tui's CombinedAutocompleteProvider."""

    def __init__(self, commands: list[dict[str, Any]] | None = None) -> None:
        self.commands = list(commands or [])
        self.triggerCharacters: list[str] = []

    @property
    def trigger_characters(self) -> list[str]:
        return self.triggerCharacters

    @trigger_characters.setter
    def trigger_characters(self, value: list[str]) -> None:
        self.triggerCharacters = list(value)

    def get_suggestions(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        before_cursor = current_line[:cursor_col]
        if not before_cursor.startswith("/"):
            return None

        command_text = before_cursor[1:]
        if " " not in command_text:
            prefix = command_text
            items = []
            for command in self.commands:
                name = str(command.get("name", ""))
                if not name or not name.lower().startswith(prefix.lower()):
                    continue
                item = {"value": name, "label": name}
                description = command.get("description")
                if description:
                    item["description"] = str(description)
                items.append(item)
            if not items:
                return None
            return {"prefix": before_cursor, "items": items}

        command_name, argument_prefix = command_text.split(" ", 1)
        command = next((item for item in self.commands if item.get("name") == command_name), None)
        if command is None:
            return None
        get_argument_completions = command.get("getArgumentCompletions") or command.get("get_argument_completions")
        if not callable(get_argument_completions):
            return None
        items = _settle_autocomplete_result(get_argument_completions(argument_prefix))
        if not isinstance(items, list) or not items:
            return None
        return {"prefix": argument_prefix, "items": items}

    getSuggestions = get_suggestions

    def apply_completion(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        item: object,
        prefix: str,
    ) -> dict[str, Any]:
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        replacement = _autocomplete_item_value(item)
        prefix_start = max(0, cursor_col - len(prefix))
        new_line = current_line[:prefix_start] + replacement + current_line[cursor_col:]
        new_lines = list(lines)
        if 0 <= cursor_line < len(new_lines):
            new_lines[cursor_line] = new_line
        else:
            new_lines.append(new_line)
            cursor_line = len(new_lines) - 1
        return {"lines": new_lines, "cursorLine": cursor_line, "cursorCol": prefix_start + len(replacement)}

    applyCompletion = apply_completion

    def should_trigger_file_completion(self, lines: list[str], cursor_line: int, cursor_col: int) -> bool:
        return True

    shouldTriggerFileCompletion = should_trigger_file_completion


class Input(Component):
    """Single-line input with basic editing and submit callback."""

    def __init__(self, value: str = "", *, prompt: str = "", on_submit: Callable[[str], None] | None = None) -> None:
        self.value = value
        self.prompt = prompt
        self.cursor = len(value)
        self.on_submit = on_submit
        self.focused = False
        self.autocomplete_provider: object | None = None
        self._kill_ring: list[str] = []
        self._last_action: str | None = None
        self._undo_stack: list[tuple[str, int]] = []

    def set_autocomplete_provider(self, provider: object | None) -> None:
        self.autocomplete_provider = provider

    setAutocompleteProvider = set_autocomplete_provider

    def set_value(self, value: str) -> None:
        self.value = value
        self.cursor = len(value)

    def get_value(self) -> str:
        return self.value

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
        self.value = str(lines[0])
        self.cursor = int(result.get("cursorCol", len(self.value)))
        return True

    def handle_input(self, data: str) -> None:
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
            elif data.startswith("\x1b[D", index):
                self.cursor = max(0, self.cursor - 1)
                index += 3
            elif data.startswith("\x1b[C", index):
                self.cursor = min(len(self.value), self.cursor + 1)
                self._last_action = None
                index += 3
            elif data.startswith("\x1b[3~", index):
                self._delete_char_forward()
                index += 4
            elif data.startswith("\x1b[H", index):
                self.cursor = 0
                self._last_action = None
                index += 3
            elif data.startswith("\x1b[F", index):
                self.cursor = len(self.value)
                self._last_action = None
                index += 3
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
                elif char in ("\r", "\n"):
                    submitted = self.value
                    if self.on_submit:
                        self.on_submit(submitted)
                    self.value = ""
                    self.cursor = 0
                    self._last_action = None
                elif char == "\x01":
                    self.cursor = 0
                    self._last_action = None
                elif char == "\x05":
                    self.cursor = len(self.value)
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
                        self.value = self.value[: self.cursor - 1] + self.value[self.cursor :]
                        self.cursor -= 1
                    self._last_action = None
                elif char >= " ":
                    if char.isspace() or self._last_action != "type-word":
                        self._push_undo()
                    self.value = self.value[: self.cursor] + char + self.value[self.cursor :]
                    self.cursor += 1
                    self._last_action = "type-word"
                index += 1

    def render(self, width: int) -> list[str]:
        prompt_width = visible_width(self.prompt)
        available_width = width - prompt_width
        if available_width <= 0:
            return [truncate_to_width(self.prompt, width)]

        visible_text = ""
        cursor_display = self.cursor
        total_width = visible_width(self.value)

        if total_width < available_width:
            visible_text = self.value
        else:
            scroll_width = available_width - 1 if self.cursor == len(self.value) else available_width
            cursor_col = visible_width(self.value[: self.cursor])
            if scroll_width > 0:
                half_width = scroll_width // 2
                if cursor_col < half_width:
                    start_col = 0
                elif cursor_col > total_width - half_width:
                    start_col = max(0, total_width - scroll_width)
                else:
                    start_col = max(0, cursor_col - half_width)
                visible_text = _slice_by_column(self.value, start_col, scroll_width)
                before_cursor = _slice_by_column(self.value, start_col, max(0, cursor_col - start_col))
                cursor_display = len(before_cursor)
            else:
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
        self._last_action = None

    def _delete_char_forward(self) -> None:
        self._last_action = None
        if self.cursor < len(self.value):
            self._push_undo()
            self.value = self.value[: self.cursor] + self.value[self.cursor + 1 :]

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
        self._last_action = "kill"

    def _delete_to_line_start(self) -> None:
        if self.cursor == 0:
            return
        self._push_undo()
        deleted = self.value[: self.cursor]
        self._push_kill(deleted, prepend=True, accumulate=self._last_action == "kill")
        self.value = self.value[self.cursor :]
        self.cursor = 0
        self._last_action = "kill"

    def _delete_to_line_end(self) -> None:
        if self.cursor >= len(self.value):
            return
        self._push_undo()
        deleted = self.value[self.cursor :]
        self._push_kill(deleted, prepend=False, accumulate=self._last_action == "kill")
        self.value = self.value[: self.cursor]
        self._last_action = "kill"

    def _yank(self) -> None:
        if not self._kill_ring:
            return
        self._push_undo()
        text = self._kill_ring[0]
        self.value = self.value[: self.cursor] + text + self.value[self.cursor :]
        self.cursor += len(text)
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
        self._last_action = "yank"


def _call_autocomplete_method(provider: object, snake_name: str, camel_name: str, *args: object) -> object:
    method = getattr(provider, snake_name, None) or getattr(provider, camel_name, None)
    if not callable(method):
        raise AttributeError(f"Autocomplete provider is missing {camel_name}")
    return method(*args)


def _settle_autocomplete_result(result: object) -> object:
    if not inspect.isawaitable(result):
        return result
    import asyncio

    return asyncio.run(result)


def _autocomplete_item_value(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("value", item.get("label", "")))
    return str(getattr(item, "value", getattr(item, "label", item)))


@dataclass
class SelectItem:
    value: str
    label: str
    description: str | None = None


class SelectList(Component):
    """Keyboard-navigable list with simple prefix filtering."""

    def __init__(self, items: list[SelectItem], max_visible: int = 5) -> None:
        self.items = list(items)
        self.filtered_items = list(items)
        self.max_visible = max(1, max_visible)
        self.selected_index = 0
        self.on_select: Callable[[SelectItem], None] | None = None
        self.on_cancel: Callable[[], None] | None = None

    def set_filter(self, value: str) -> None:
        needle = value.lower()
        self.filtered_items = [item for item in self.items if item.value.lower().startswith(needle)]
        self.selected_index = 0

    def handle_input(self, data: str) -> None:
        if data == "\x1b" and self.on_cancel:
            self.on_cancel()
            return
        if not self.filtered_items:
            return
        if data in ("\x1b[A", "k"):
            self.selected_index = (self.selected_index - 1) % len(self.filtered_items)
        elif data in ("\x1b[B", "j"):
            self.selected_index = (self.selected_index + 1) % len(self.filtered_items)
        elif data in ("\r", "\n") and self.on_select:
            self.on_select(self.filtered_items[self.selected_index])

    def render(self, width: int) -> list[str]:
        if not self.filtered_items:
            return ["  No matching commands"]
        start = max(0, min(self.selected_index - self.max_visible // 2, len(self.filtered_items) - self.max_visible))
        end = min(start + self.max_visible, len(self.filtered_items))
        lines: list[str] = []
        for index in range(start, end):
            item = self.filtered_items[index]
            prefix = "> " if index == self.selected_index else "  "
            description = f"  {item.description}" if item.description else ""
            lines.append(truncate_to_width(f"{prefix}{item.label}{description}", width))
        if start > 0 or end < len(self.filtered_items):
            lines.append(truncate_to_width(f"  ({self.selected_index + 1}/{len(self.filtered_items)})", width))
        return lines


class StatusLine(Text):
    def __init__(self, message: str = "", kind: str = "status") -> None:
        self.kind = kind
        self.visible = True
        self._message = ""
        self._indicator: str | None = None
        super().__init__("")
        self.set_message(message)

    def set_message(self, message: str, kind: str | None = None) -> None:
        if kind is not None:
            self.kind = kind
        self._message = _single_line(message)
        self._refresh_text()

    def set_indicator(self, indicator: str | None = None) -> None:
        self._indicator = _single_line(indicator) if indicator is not None else None
        self._refresh_text()

    def set_visible(self, visible: bool) -> None:
        if self.visible != bool(visible):
            self.visible = bool(visible)
            self.invalidate()

    def render(self, width: int) -> list[str]:
        if not self.visible:
            return []
        return super().render(width)

    def _refresh_text(self) -> None:
        clean = self._message
        if clean and self._indicator:
            clean = f"{self._indicator} {clean}"
        self.set_text(f"{self.kind}: {clean}" if clean else "")


class FooterComponent(Component):
    def __init__(
        self,
        *,
        cwd: str,
        model: str,
        thinking_level: str = "off",
        pending: int = 0,
        context_tokens: int | None = None,
        context_threshold: int | None = None,
        compression_count: int = 0,
        extension_statuses: dict[str, str] | None = None,
    ) -> None:
        self.cwd = cwd
        self.model = model
        self.thinking_level = thinking_level
        self.pending = pending
        self.context_tokens = context_tokens
        self.context_threshold = context_threshold
        self.compression_count = compression_count
        self.extension_statuses = dict(extension_statuses or {})

    def render(self, width: int) -> list[str]:
        if self.context_tokens is not None and self.context_threshold is not None:
            parts = [
                f"model: {self.model}",
                f"think: {self.thinking_level}",
                f"ctx: {self.context_tokens:,}/{self.context_threshold:,}",
                f"compactions: {self.compression_count:,}",
            ]
            if self.pending:
                parts.append(f"pending: {self.pending}")
            parts.extend(
                f"{key}: {value}"
                for key, value in sorted(self.extension_statuses.items())
                if value
            )
            parts.append(f"cwd: {self.cwd}")
            return [truncate_to_width(" | ".join(parts), width)]

        parts = [f"cwd: {self.cwd}", f"model: {self.model}", f"think: {self.thinking_level}"]
        if self.pending:
            parts.append(f"pending: {self.pending}")
        parts.extend(
            f"{key}: {value}"
            for key, value in sorted(self.extension_statuses.items())
            if value
        )
        return [truncate_to_width(" | ".join(parts), width)]


class Spacer(Component):
    def __init__(self, height: int = 1) -> None:
        self.height = height

    def render(self, width: int) -> list[str]:
        return ["" for _ in range(self.height)]


class Box(Component):
    """A child wrapped in a simple single-line border (optional title)."""

    def __init__(self, child: Component, title: str = "") -> None:
        self.child = child
        self.title = title

    def invalidate(self) -> None:
        self.child.invalidate()

    def render(self, width: int) -> list[str]:
        inner_width = max(1, width - 2)
        top_label = f" {self.title} " if self.title else ""
        top = "+" + top_label + "-" * max(0, inner_width - visible_width(top_label)) + "+"
        rows = [top]
        for line in self.child.render(inner_width):
            padded = truncate_to_width(line, inner_width)
            pad = inner_width - visible_width(padded)
            rows.append("|" + padded + (" " * max(0, pad)) + "|")
        rows.append("+" + "-" * inner_width + "+")
        return rows


_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]*)`")


def _render_markdown_text(text: str) -> str:
    lines: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(raw)
            continue
        if stripped.startswith("#"):
            lines.append(stripped.lstrip("#").strip())
            continue
        if stripped.startswith(("- ", "* ")):
            lines.append("- " + _inline_markdown(stripped[2:]))
            continue
        lines.append(_inline_markdown(raw))
    return "\n".join(lines)


def _inline_markdown(text: str) -> str:
    text = _CODE_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\1", text)
    return _ITALIC_RE.sub(r"\1", text)


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


def _slice_by_column(text: str, start_col: int, width: int) -> str:
    if width <= 0:
        return ""
    result: list[str] = []
    col = 0
    end_col = start_col + width
    for char in text:
        char_width = visible_width(char)
        next_col = col + char_width
        if next_col > start_col and col < end_col:
            result.append(char)
        if next_col >= end_col:
            break
        col = next_col
    return "".join(result)


def _single_line(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").replace("\t", " ").split())
