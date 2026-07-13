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

CURSOR_MARKER = "\x1b_travis234:c\x07"
_ANSI_RESET = "\x1b[0m"
_SIGNAL_GLASS_STATUS_COLORS = {
    "compact": "38;2;86;240;182",
    "info": "38;2;191;231;255",
    "note": "38;2;255;229;166",
    "warning": "38;2;255;182;72",
    "error": "38;2;255;112;112",
    "help": "38;2;120;255;208",
    "status": "38;2;217;255;242",
    "select": "38;2;120;255;208",
    "auth": "38;2;120;255;208",
    "model": "38;2;120;255;208",
}
_SIGNAL_GLASS_FOOTER_COLOR = "38;2;120;255;208"


def _tui_color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return os.environ.get("TERM", "").lower() != "dumb"


def _ansi_color(text: str, color: str | None) -> str:
    if not text or not color or not _tui_color_enabled():
        return text
    return f"\x1b[{color}m{text}{_ANSI_RESET}"


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


class TruncatedText(Component):
    """Text component that truncates to one padded viewport line, matching Travis."""

    def __init__(self, text: str, padding_x: int = 0, padding_y: int = 0) -> None:
        self.text = text
        self.padding_x = max(0, int(padding_x))
        self.padding_y = max(0, int(padding_y))

    def render(self, width: int) -> list[str]:
        width = max(0, int(width))
        result: list[str] = []
        empty_line = " " * width

        for _ in range(self.padding_y):
            result.append(empty_line)

        available_width = max(1, width - self.padding_x * 2)
        single_line_text = self.text.split("\n", 1)[0]
        display_text = truncate_to_width(single_line_text, available_width, "...")
        line_with_padding = (" " * self.padding_x) + display_text + (" " * self.padding_x)
        padding_needed = max(0, width - visible_width(line_with_padding))
        result.append(line_with_padding + (" " * padding_needed))

        for _ in range(self.padding_y):
            result.append(empty_line)

        return result


_LOADER_DEFAULT_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_LOADER_DEFAULT_INTERVAL_MS = 80


class Loader(Text):
    """Loader component with optional spinning indicator."""

    def __init__(
        self,
        ui: object | None,
        spinner_color_fn: Callable[[str], str] | None = None,
        message_color_fn: Callable[[str], str] | None = None,
        message: str = "Loading...",
        indicator: dict[str, Any] | None = None,
    ) -> None:
        super().__init__("")
        self.ui = ui
        self.spinner_color_fn = spinner_color_fn or (lambda value: value)
        self.message_color_fn = message_color_fn or (lambda value: value)
        self.message = message
        self.frames = list(_LOADER_DEFAULT_FRAMES)
        self.interval_ms = _LOADER_DEFAULT_INTERVAL_MS
        self.current_frame = 0
        self._timer: threading.Timer | None = None
        self._stopped = True
        self._render_indicator_verbatim = False
        self.set_indicator(indicator)

    def render(self, width: int) -> list[str]:
        return ["", *super().render(width)]

    def start(self) -> None:
        self._stopped = False
        self._update_display()
        self._restart_animation()

    def stop(self) -> None:
        self._stopped = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def set_message(self, message: str) -> None:
        self.message = message
        self._update_display()

    setMessage = set_message

    def set_indicator(self, indicator: dict[str, Any] | None = None) -> None:
        self._render_indicator_verbatim = indicator is not None
        frames = indicator.get("frames") if isinstance(indicator, dict) else None
        interval = (
            indicator.get("intervalMs", indicator.get("interval_ms"))
            if isinstance(indicator, dict)
            else None
        )
        self.frames = list(frames) if isinstance(frames, list) else list(_LOADER_DEFAULT_FRAMES)
        self.interval_ms = int(interval) if isinstance(interval, (int, float)) and interval > 0 else _LOADER_DEFAULT_INTERVAL_MS
        self.current_frame = 0
        self.start()

    setIndicator = set_indicator

    def _restart_animation(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._stopped or len(self.frames) <= 1:
            return
        self._timer = threading.Timer(self.interval_ms / 1000.0, self._advance_frame)
        self._timer.daemon = True
        self._timer.start()

    def _advance_frame(self) -> None:
        if self._stopped or not self.frames:
            return
        self.current_frame = (self.current_frame + 1) % len(self.frames)
        self._update_display()
        self._restart_animation()

    def _update_display(self) -> None:
        frame = self.frames[self.current_frame] if self.frames else ""
        rendered_frame = frame if self._render_indicator_verbatim else self.spinner_color_fn(frame)
        indicator = f"{rendered_frame} " if frame else ""
        self.set_text(f"{indicator}{self.message_color_fn(self.message)}")
        request_render = getattr(self.ui, "request_render", None) or getattr(self.ui, "requestRender", None)
        if callable(request_render):
            request_render()


class CancellableLoader(Loader):
    """Loader that aborts when the Travis cancel keybinding is pressed."""

    def __init__(self, *args, **kwargs) -> None:
        self._abort_signal = AbortSignal()
        self.onAbort: Callable[[], object] | None = None
        super().__init__(*args, **kwargs)

    @property
    def signal(self) -> AbortSignal:
        return self._abort_signal

    @property
    def aborted(self) -> bool:
        return self._abort_signal.aborted

    def handle_input(self, data: str) -> None:
        if get_keybindings().matches(data, "tui.select.cancel"):
            self._abort_signal.abort()
            if callable(self.onAbort):
                self.onAbort()

    handleInput = handle_input

    def dispose(self) -> None:
        self.stop()


class Image(Component):
    """Terminal image component ported from Travis."""

    def __init__(
        self,
        base64_data: str,
        mime_type: str,
        theme: Any,
        options: dict[str, Any] | None = None,
        dimensions: dict[str, int] | None = None,
    ) -> None:
        self.base64_data = base64_data
        self.mime_type = mime_type
        self.theme = theme
        self.options = dict(options or {})
        self.dimensions = dimensions or get_image_dimensions(base64_data, mime_type) or {"widthPx": 800, "heightPx": 600}
        self.image_id = self.options.get("imageId", self.options.get("image_id"))
        self._cached_lines: list[str] | None = None
        self._cached_width: int | None = None

    def get_image_id(self) -> int | None:
        return self.image_id

    getImageId = get_image_id

    def invalidate(self) -> None:
        self._cached_lines = None
        self._cached_width = None

    def render(self, width: int) -> list[str]:
        if self._cached_lines is not None and self._cached_width == width:
            return self._cached_lines

        max_width = max(1, min(width - 2, int(self.options.get("maxWidthCells", self.options.get("max_width_cells", 60)))))
        cell_dimensions = get_cell_dimensions()
        default_max_height = max(1, math.ceil((max_width * cell_dimensions["widthPx"]) / cell_dimensions["heightPx"]))
        max_height = int(self.options.get("maxHeightCells", self.options.get("max_height_cells", default_max_height)))

        caps = get_capabilities()
        if caps["images"]:
            if caps["images"] == "kitty" and self.image_id is None:
                self.image_id = allocate_image_id()
            result = render_image(
                self.base64_data,
                self.dimensions,
                {
                    "maxWidthCells": max_width,
                    "maxHeightCells": max_height,
                    "imageId": self.image_id,
                    "moveCursor": False,
                },
            )

            if result:
                if result.get("imageId"):
                    self.image_id = result["imageId"]
                if caps["images"] == "kitty":
                    lines = [str(result["sequence"])]
                    lines.extend("" for _ in range(max(0, int(result["rows"]) - 1)))
                else:
                    lines = ["" for _ in range(max(0, int(result["rows"]) - 1))]
                    row_offset = int(result["rows"]) - 1
                    move_up = f"\x1b[{row_offset}A" if row_offset > 0 else ""
                    lines.append(move_up + str(result["sequence"]))
            else:
                lines = [self._format_fallback()]
        else:
            lines = [self._format_fallback()]

        self._cached_lines = lines
        self._cached_width = width
        return lines

    def _format_fallback(self) -> str:
        fallback = image_fallback(self.mime_type, self.dimensions, self.options.get("filename"))
        fallback_color = None
        if isinstance(self.theme, dict):
            fallback_color = self.theme.get("fallbackColor") or self.theme.get("fallback_color")
        else:
            fallback_color = getattr(self.theme, "fallbackColor", None) or getattr(self.theme, "fallback_color", None)
        return fallback_color(fallback) if callable(fallback_color) else fallback

class Markdown(Text):
    """Small terminal markdown renderer for assistant/user content."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)

    def render(self, width: int) -> list[str]:
        key = (self._text, width)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        rendered = _render_markdown_text(self._text)
        lines: list[str] = []
        for raw in rendered.split("\n"):
            lines.extend(wrap_text(raw, width))
        self._cache = lines
        self._cache_key = key
        return lines


class SimpleAutocompleteProvider:
    """Small Python equivalent of travis-tui's CombinedAutocompleteProvider."""

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
            command_items = [command for command in self.commands if _autocomplete_command_name(command)]
            items = []
            for command in fuzzy_filter(command_items, prefix, _autocomplete_command_name):
                name = _autocomplete_command_name(command)
                item = {"value": name, "label": name}
                description = _autocomplete_command_description(command)
                if description:
                    item["description"] = description
                items.append(item)
            if not items:
                return None
            return {"prefix": before_cursor, "items": items}

        command_name, argument_prefix = command_text.split(" ", 1)
        command = next((item for item in self.commands if _autocomplete_command_name(item) == command_name), None)
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


class CombinedAutocompleteProvider:
    """provider for slash commands, file paths, and @ attachments."""

    def __init__(
        self,
        commands: list[dict[str, Any]] | None = None,
        base_path: str = ".",
        fd_path: str | None = None,
    ) -> None:
        self.commands = list(commands or [])
        self.base_path = base_path
        self.fd_path = fd_path

    def get_suggestions(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        options = options or {}
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        text_before_cursor = current_line[:cursor_col]

        at_prefix = _extract_at_prefix(text_before_cursor)
        if at_prefix:
            raw_prefix, _is_at_prefix, is_quoted_prefix = _parse_path_prefix(at_prefix)
            suggestions = self._get_fuzzy_file_suggestions(raw_prefix, is_quoted_prefix)
            if not suggestions:
                return None
            return {"items": suggestions, "prefix": at_prefix}

        if not options.get("force") and text_before_cursor.startswith("/"):
            space_index = text_before_cursor.find(" ")
            if space_index == -1:
                prefix = text_before_cursor[1:]
                command_items = []
                for command in self.commands:
                    name = _autocomplete_command_name(command)
                    if not name:
                        continue
                    item: dict[str, Any] = {"name": name, "label": name}
                    description = _autocomplete_command_description(command)
                    if description:
                        item["description"] = description
                    command_items.append(item)
                filtered = []
                for item in fuzzy_filter(command_items, prefix, lambda value: value["name"]):
                    result = {"value": item["name"], "label": item["label"]}
                    if item.get("description"):
                        result["description"] = item["description"]
                    filtered.append(result)
                if not filtered:
                    return None
                return {"items": filtered, "prefix": text_before_cursor}

            command_name = text_before_cursor[1:space_index]
            argument_text = text_before_cursor[space_index + 1 :]
            command = next((item for item in self.commands if _autocomplete_command_name(item) == command_name), None)
            if not command:
                return None
            get_argument_completions = command.get("getArgumentCompletions") or command.get("get_argument_completions")
            if not callable(get_argument_completions):
                return None
            argument_suggestions = _settle_autocomplete_result(get_argument_completions(argument_text))
            if not isinstance(argument_suggestions, list) or not argument_suggestions:
                return None
            return {"items": argument_suggestions, "prefix": argument_text}

        path_prefix = _extract_path_prefix(text_before_cursor, bool(options.get("force")))
        if path_prefix is None:
            return None
        suggestions = self._get_file_suggestions(path_prefix)
        if not suggestions:
            return None
        return {"items": suggestions, "prefix": path_prefix}

    getSuggestions = get_suggestions

    def apply_completion(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        item: dict[str, Any],
        prefix: str,
    ) -> dict[str, Any]:
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        before_prefix = current_line[: max(0, cursor_col - len(prefix))]
        after_cursor = current_line[cursor_col:]
        item_value = _autocomplete_item_value(item)
        item_label = str(item.get("label", item_value)) if isinstance(item, dict) else item_value
        is_quoted_prefix = prefix.startswith('"') or prefix.startswith('@"')
        has_leading_quote_after_cursor = after_cursor.startswith('"')
        has_trailing_quote_in_item = item_value.endswith('"')
        adjusted_after_cursor = (
            after_cursor[1:]
            if is_quoted_prefix and has_trailing_quote_in_item and has_leading_quote_after_cursor
            else after_cursor
        )

        is_slash_command = prefix.startswith("/") and before_prefix.strip() == "" and "/" not in prefix[1:]
        if is_slash_command:
            new_line = f"{before_prefix}/{item_value} {adjusted_after_cursor}"
            new_lines = list(lines)
            new_lines[cursor_line] = new_line
            return {"lines": new_lines, "cursorLine": cursor_line, "cursorCol": len(before_prefix) + len(item_value) + 2}

        if prefix.startswith("@"):
            is_directory = item_label.endswith("/")
            suffix = "" if is_directory else " "
            new_line = f"{before_prefix}{item_value}{suffix}{adjusted_after_cursor}"
            new_lines = list(lines)
            new_lines[cursor_line] = new_line
            cursor_offset = len(item_value) - 1 if is_directory and has_trailing_quote_in_item else len(item_value)
            return {
                "lines": new_lines,
                "cursorLine": cursor_line,
                "cursorCol": len(before_prefix) + cursor_offset + len(suffix),
            }

        text_before_cursor = current_line[:cursor_col]
        if "/" in text_before_cursor and " " in text_before_cursor:
            new_line = f"{before_prefix}{item_value}{adjusted_after_cursor}"
            new_lines = list(lines)
            new_lines[cursor_line] = new_line
            is_directory = item_label.endswith("/")
            cursor_offset = len(item_value) - 1 if is_directory and has_trailing_quote_in_item else len(item_value)
            return {"lines": new_lines, "cursorLine": cursor_line, "cursorCol": len(before_prefix) + cursor_offset}

        new_line = f"{before_prefix}{item_value}{adjusted_after_cursor}"
        new_lines = list(lines)
        new_lines[cursor_line] = new_line
        is_directory = item_label.endswith("/")
        cursor_offset = len(item_value) - 1 if is_directory and has_trailing_quote_in_item else len(item_value)
        return {"lines": new_lines, "cursorLine": cursor_line, "cursorCol": len(before_prefix) + cursor_offset}

    applyCompletion = apply_completion

    def should_trigger_file_completion(self, lines: list[str], cursor_line: int, cursor_col: int) -> bool:
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        text_before_cursor = current_line[:cursor_col]
        stripped = text_before_cursor.strip()
        if stripped.startswith("/") and " " not in stripped:
            return False
        return True

    shouldTriggerFileCompletion = should_trigger_file_completion

    def _get_file_suggestions(self, prefix: str) -> list[dict[str, str]]:
        try:
            raw_prefix, is_at_prefix, is_quoted_prefix = _parse_path_prefix(prefix)
            expanded_prefix = _expand_home_path(raw_prefix)
            is_root_prefix = raw_prefix in {"", "./", "../", "~", "~/", "/"} or (is_at_prefix and raw_prefix == "")
            if is_root_prefix:
                search_dir = expanded_prefix if raw_prefix.startswith("~") or expanded_prefix.startswith("/") else os.path.join(self.base_path, expanded_prefix)
                search_prefix = ""
            elif raw_prefix.endswith("/"):
                search_dir = expanded_prefix if raw_prefix.startswith("~") or expanded_prefix.startswith("/") else os.path.join(self.base_path, expanded_prefix)
                search_prefix = ""
            else:
                directory = os.path.dirname(expanded_prefix)
                search_prefix = os.path.basename(expanded_prefix)
                search_dir = directory if raw_prefix.startswith("~") or expanded_prefix.startswith("/") else os.path.join(self.base_path, directory)

            suggestions: list[dict[str, str]] = []
            for entry in os.scandir(search_dir or "."):
                if not entry.name.lower().startswith(search_prefix.lower()):
                    continue
                try:
                    is_directory = entry.is_dir()
                except OSError:
                    is_directory = False
                display_prefix = raw_prefix
                if display_prefix.endswith("/"):
                    relative_path = display_prefix + entry.name
                elif "/" in display_prefix or "\\" in display_prefix:
                    if display_prefix.startswith("~/"):
                        home_relative_dir = display_prefix[2:]
                        directory_name = os.path.dirname(home_relative_dir)
                        relative_path = f"~/{entry.name}" if directory_name == "" else f"~/{directory_name}/{entry.name}"
                    elif display_prefix.startswith("/"):
                        directory_name = os.path.dirname(display_prefix)
                        relative_path = f"/{entry.name}" if directory_name == "/" else f"{directory_name}/{entry.name}"
                    else:
                        relative_path = os.path.join(os.path.dirname(display_prefix), entry.name)
                        if display_prefix.startswith("./") and not relative_path.startswith("./"):
                            relative_path = f"./{relative_path}"
                else:
                    relative_path = f"~/{entry.name}" if display_prefix.startswith("~") else entry.name
                relative_path = _to_display_path(relative_path)
                path_value = f"{relative_path}/" if is_directory else relative_path
                suggestions.append(
                    {
                        "value": _build_completion_value(
                            path_value,
                            is_directory=is_directory,
                            is_at_prefix=is_at_prefix,
                            is_quoted_prefix=is_quoted_prefix,
                        ),
                        "label": entry.name + ("/" if is_directory else ""),
                    }
                )
            suggestions.sort(key=lambda item: (0 if item["label"].endswith("/") else 1, item["label"].lower()))
            return suggestions
        except OSError:
            return []

    def _get_fuzzy_file_suggestions(self, query: str, is_quoted_prefix: bool) -> list[dict[str, str]]:
        suggestions = []
        lower_query = query.lower()
        for root, dirs, files in os.walk(self.base_path):
            dirs[:] = [directory for directory in dirs if directory != ".git"]
            for name, is_directory in [(directory, True) for directory in dirs] + [(file_name, False) for file_name in files]:
                full_path = os.path.join(root, name)
                relative_path = _to_display_path(os.path.relpath(full_path, self.base_path))
                if lower_query and lower_query not in relative_path.lower() and lower_query not in name.lower():
                    continue
                completion_path = f"{relative_path}/" if is_directory else relative_path
                suggestions.append(
                    {
                        "value": _build_completion_value(
                            completion_path,
                            is_directory=is_directory,
                            is_at_prefix=True,
                            is_quoted_prefix=is_quoted_prefix,
                        ),
                        "label": name + ("/" if is_directory else ""),
                    }
                )
                if len(suggestions) >= 20:
                    return suggestions
        suggestions.sort(key=lambda item: (0 if item["label"].endswith("/") else 1, item["label"].lower()))
        return suggestions


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
        self.onEscape: Callable[[], None] | None = None
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

    setAutocompleteProvider = set_autocomplete_provider

    def set_value(self, value: str) -> None:
        self.value = value
        self.cursor = len(value)
        self._exit_history_browsing()

    def get_value(self) -> str:
        return self.value

    def set_history(self, history: list[str]) -> None:
        self._history = history
        self._exit_history_browsing()

    setHistory = set_history

    def add_to_history(self, text: str) -> None:
        trimmed = text.strip()
        if not trimmed:
            return
        if self._history and self._history[0] == trimmed:
            return
        self._history.insert(0, trimmed)
        del self._history[100:]

    addToHistory = add_to_history

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


class SettingsList(Component):
    """Settings list component ported from Travis."""

    def __init__(
        self,
        items: list[dict[str, Any]],
        max_visible: int,
        theme: Any,
        on_change: Callable[[str, str], None],
        on_cancel: Callable[[], None],
        options: dict[str, Any] | None = None,
    ) -> None:
        self.items = items
        self.filtered_items = list(items)
        self.max_visible = max(1, int(max_visible))
        self.theme = theme
        self.selected_index = 0
        self.on_change = on_change
        self.on_cancel = on_cancel
        self.search_enabled = bool((options or {}).get("enableSearch", (options or {}).get("enable_search", False)))
        self.search_input = Input() if self.search_enabled else None
        self.submenu_component: Component | None = None
        self.submenu_item_index: int | None = None

    def update_value(self, item_id: str, new_value: str) -> None:
        item = next((candidate for candidate in self.items if candidate.get("id") == item_id), None)
        if item is not None:
            item["currentValue"] = new_value

    updateValue = update_value

    def invalidate(self) -> None:
        if self.submenu_component is not None:
            self.submenu_component.invalidate()

    def render(self, width: int) -> list[str]:
        if self.submenu_component is not None:
            return self.submenu_component.render(width)
        return self._render_main_list(width)

    def handle_input(self, data: str) -> None:
        if self.submenu_component is not None:
            handle_input = getattr(self.submenu_component, "handle_input", None) or getattr(
                self.submenu_component, "handleInput", None
            )
            if callable(handle_input):
                handle_input(data)
            return

        keybindings = get_keybindings()
        display_items = self.filtered_items if self.search_enabled else self.items
        if keybindings.matches(data, "tui.select.up"):
            if display_items:
                self.selected_index = len(display_items) - 1 if self.selected_index == 0 else self.selected_index - 1
        elif keybindings.matches(data, "tui.select.down"):
            if display_items:
                self.selected_index = 0 if self.selected_index == len(display_items) - 1 else self.selected_index + 1
        elif keybindings.matches(data, "tui.select.confirm") or data == " ":
            self._activate_item()
        elif keybindings.matches(data, "tui.select.cancel"):
            self.on_cancel()
        elif self.search_enabled and self.search_input is not None:
            sanitized = data.replace(" ", "")
            if not sanitized:
                return
            self.search_input.handle_input(sanitized)
            self._apply_filter(self.search_input.get_value())

    handleInput = handle_input

    def _render_main_list(self, width: int) -> list[str]:
        lines: list[str] = []

        if self.search_enabled and self.search_input is not None:
            lines.extend(self.search_input.render(width))
            lines.append("")

        if not self.items:
            lines.append(self._theme_call("hint", "  No settings available"))
            if self.search_enabled:
                self._add_hint_line(lines, width)
            return lines

        display_items = self.filtered_items if self.search_enabled else self.items
        if not display_items:
            lines.append(truncate_to_width(self._theme_call("hint", "  No matching settings"), width))
            self._add_hint_line(lines, width)
            return lines

        self.selected_index = max(0, min(self.selected_index, len(display_items) - 1))
        start_index = max(
            0,
            min(self.selected_index - self.max_visible // 2, len(display_items) - self.max_visible),
        )
        end_index = min(start_index + self.max_visible, len(display_items))
        max_label_width = min(30, max(visible_width(str(item.get("label", ""))) for item in self.items))

        for index in range(start_index, end_index):
            item = display_items[index]
            is_selected = index == self.selected_index
            prefix = self._theme_value("cursor", "->") if is_selected else "  "
            prefix_width = visible_width(prefix)

            label = str(item.get("label", ""))
            label_padded = label + (" " * max(0, max_label_width - visible_width(label)))
            label_text = self._theme_call("label", label_padded, is_selected)
            separator = "  "
            used_width = prefix_width + max_label_width + visible_width(separator)
            value_max_width = width - used_width - 2
            value = truncate_to_width(str(item.get("currentValue", "")), value_max_width)
            value_text = self._theme_call("value", value, is_selected)

            lines.append(truncate_to_width(prefix + label_text + separator + value_text, width))

        if start_index > 0 or end_index < len(display_items):
            scroll_text = f"  ({self.selected_index + 1}/{len(display_items)})"
            lines.append(self._theme_call("hint", truncate_to_width(scroll_text, width - 2)))

        selected_item = display_items[self.selected_index] if display_items else None
        if selected_item and selected_item.get("description"):
            lines.append("")
            for line in wrap_text(str(selected_item["description"]), width - 4):
                lines.append(self._theme_call("description", f"  {line}"))

        self._add_hint_line(lines, width)
        return lines

    def _activate_item(self) -> None:
        display_items = self.filtered_items if self.search_enabled else self.items
        if not (0 <= self.selected_index < len(display_items)):
            return

        item = display_items[self.selected_index]
        submenu = item.get("submenu")
        if callable(submenu):
            self.submenu_item_index = self.selected_index

            def done(selected_value: str | None = None) -> None:
                if selected_value is not None:
                    item["currentValue"] = selected_value
                    self.on_change(str(item.get("id", "")), selected_value)
                self._close_submenu()

            self.submenu_component = submenu(str(item.get("currentValue", "")), done)
            return

        values = item.get("values")
        if isinstance(values, list) and values:
            current_value = str(item.get("currentValue", ""))
            try:
                current_index = values.index(current_value)
            except ValueError:
                current_index = -1
            new_value = str(values[(current_index + 1) % len(values)])
            item["currentValue"] = new_value
            self.on_change(str(item.get("id", "")), new_value)

    def _close_submenu(self) -> None:
        self.submenu_component = None
        if self.submenu_item_index is not None:
            self.selected_index = self.submenu_item_index
            self.submenu_item_index = None

    def _apply_filter(self, query: str) -> None:
        self.filtered_items = fuzzy_filter(self.items, query, lambda item: str(item.get("label", "")))
        self.selected_index = 0

    def _add_hint_line(self, lines: list[str], width: int) -> None:
        lines.append("")
        hint = (
            "  Type to search · Enter/Space to change · Esc to cancel"
            if self.search_enabled
            else "  Enter/Space to change · Esc to cancel"
        )
        lines.append(truncate_to_width(self._theme_call("hint", hint), width))

    def _theme_value(self, name: str, default: str) -> str:
        if isinstance(self.theme, dict):
            return str(self.theme.get(name, default))
        if self.theme is not None and hasattr(self.theme, name):
            return str(getattr(self.theme, name))
        return default

    def _theme_call(self, name: str, text: str, selected: bool | None = None) -> str:
        callback: object = None
        if isinstance(self.theme, dict):
            callback = self.theme.get(name)
        elif self.theme is not None:
            callback = getattr(self.theme, name, None)
        if callable(callback):
            if selected is None:
                return str(callback(text))
            return str(callback(text, selected))
        return text


def _call_autocomplete_method(provider: object, snake_name: str, camel_name: str, *args: object) -> object:
    method = getattr(provider, snake_name, None) or getattr(provider, camel_name, None)
    if not callable(method):
        raise AttributeError(f"Autocomplete provider is missing {camel_name}")
    return method(*args)


def _matched_sequence_length(data: str, index: int, sequences: tuple[str, ...]) -> int:
    for sequence in sequences:
        if data.startswith(sequence, index):
            return len(sequence)
    return 1


def _settle_autocomplete_result(result: object) -> object:
    if not inspect.isawaitable(result):
        return result
    import asyncio

    return asyncio.run(result)


def _autocomplete_item_value(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("value") or item.get("label") or item.get("name") or "")
    return str(getattr(item, "value", getattr(item, "label", item)))


def _autocomplete_command_name(command: dict[str, Any]) -> str:
    return str(command.get("name") or command.get("value") or "")


def _autocomplete_command_description(command: dict[str, Any]) -> str:
    hint = command.get("argumentHint") or command.get("argument_hint")
    description = str(command.get("description") or "")
    if hint:
        hint_text = str(hint)
        return f"{hint_text} — {description}" if description else hint_text
    return description


_PATH_DELIMITERS = {" ", "\t", '"', "'", "="}


def _to_display_path(value: str) -> str:
    return value.replace("\\", "/")


def _find_last_delimiter(text: str) -> int:
    for index in range(len(text) - 1, -1, -1):
        if text[index] in _PATH_DELIMITERS:
            return index
    return -1


def _find_unclosed_quote_start(text: str) -> int | None:
    in_quotes = False
    quote_start = -1
    for index, char in enumerate(text):
        if char == '"':
            in_quotes = not in_quotes
            if in_quotes:
                quote_start = index
    return quote_start if in_quotes else None


def _is_token_start(text: str, index: int) -> bool:
    return index == 0 or text[index - 1] in _PATH_DELIMITERS


def _extract_quoted_prefix(text: str) -> str | None:
    quote_start = _find_unclosed_quote_start(text)
    if quote_start is None:
        return None
    if quote_start > 0 and text[quote_start - 1] == "@":
        if not _is_token_start(text, quote_start - 1):
            return None
        return text[quote_start - 1 :]
    if not _is_token_start(text, quote_start):
        return None
    return text[quote_start:]


def _extract_at_prefix(text: str) -> str | None:
    quoted_prefix = _extract_quoted_prefix(text)
    if quoted_prefix and quoted_prefix.startswith('@"'):
        return quoted_prefix
    last_delimiter_index = _find_last_delimiter(text)
    token_start = 0 if last_delimiter_index == -1 else last_delimiter_index + 1
    if token_start < len(text) and text[token_start] == "@":
        return text[token_start:]
    return None


def _extract_path_prefix(text: str, force_extract: bool = False) -> str | None:
    quoted_prefix = _extract_quoted_prefix(text)
    if quoted_prefix:
        return quoted_prefix
    last_delimiter_index = _find_last_delimiter(text)
    path_prefix = text if last_delimiter_index == -1 else text[last_delimiter_index + 1 :]
    if force_extract:
        return path_prefix
    if "/" in path_prefix or path_prefix.startswith(".") or path_prefix.startswith("~/"):
        return path_prefix
    if path_prefix == "" and text.endswith(" "):
        return path_prefix
    return None


def _parse_path_prefix(prefix: str) -> tuple[str, bool, bool]:
    if prefix.startswith('@"'):
        return prefix[2:], True, True
    if prefix.startswith('"'):
        return prefix[1:], False, True
    if prefix.startswith("@"):
        return prefix[1:], True, False
    return prefix, False, False


def _expand_home_path(path: str) -> str:
    if path.startswith("~/"):
        expanded = os.path.join(os.path.expanduser("~"), path[2:])
        return f"{expanded}/" if path.endswith("/") and not expanded.endswith("/") else expanded
    if path == "~":
        return os.path.expanduser("~")
    return path


def _build_completion_value(
    path: str,
    *,
    is_directory: bool,
    is_at_prefix: bool,
    is_quoted_prefix: bool,
) -> str:
    del is_directory
    needs_quotes = is_quoted_prefix or " " in path
    prefix = "@" if is_at_prefix else ""
    if not needs_quotes:
        return f"{prefix}{path}"
    return f'{prefix}"{path}"'


@dataclass
class SelectItem:
    value: str
    label: str
    description: str | None = None


DEFAULT_PRIMARY_COLUMN_WIDTH = 32
PRIMARY_COLUMN_GAP = 2
MIN_DESCRIPTION_WIDTH = 10


class SelectList(Component):
    """Keyboard-navigable list with simple prefix filtering."""

    def __init__(
        self,
        items: list[SelectItem],
        max_visible: int = 5,
        theme: object | None = None,
        layout: dict[str, Any] | None = None,
    ) -> None:
        self.items = list(items)
        self.filtered_items = list(items)
        self.max_visible = max(1, max_visible)
        self.theme = theme
        self.layout = dict(layout or {})
        self.selected_index = 0
        self.on_select: Callable[[SelectItem], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self.on_selection_change: Callable[[SelectItem], None] | None = None
        self.onSelect: Callable[[SelectItem], None] | None = None
        self.onCancel: Callable[[], None] | None = None
        self.onSelectionChange: Callable[[SelectItem], None] | None = None

    def set_filter(self, value: str) -> None:
        needle = value.lower()
        self.filtered_items = [item for item in self.items if item.value.lower().startswith(needle)]
        self.selected_index = 0

    setFilter = set_filter

    def set_selected_index(self, index: int) -> None:
        if not self.filtered_items:
            self.selected_index = 0
            return
        self.selected_index = max(0, min(int(index), len(self.filtered_items) - 1))

    setSelectedIndex = set_selected_index

    def get_selected_item(self) -> SelectItem | None:
        if 0 <= self.selected_index < len(self.filtered_items):
            return self.filtered_items[self.selected_index]
        return None

    getSelectedItem = get_selected_item

    def handle_input(self, data: str) -> None:
        if data in ("\x1b", "\x03"):
            self._notify_cancel()
            return
        if not self.filtered_items:
            return
        if data in ("\x1b[A", "k"):
            self.selected_index = (self.selected_index - 1) % len(self.filtered_items)
            self._notify_selection_change()
        elif data in ("\x1b[B", "j"):
            self.selected_index = (self.selected_index + 1) % len(self.filtered_items)
            self._notify_selection_change()
        elif data in ("\r", "\n"):
            self._notify_select()

    def render(self, width: int) -> list[str]:
        if not self.filtered_items:
            return [self._theme_text("noMatch", "  No matching commands")]
        primary_column_width = self._get_primary_column_width()
        start = max(0, min(self.selected_index - self.max_visible // 2, len(self.filtered_items) - self.max_visible))
        end = min(start + self.max_visible, len(self.filtered_items))
        lines: list[str] = []
        for index in range(start, end):
            item = self.filtered_items[index]
            description = _single_line(item.description) if item.description else None
            lines.append(self._render_item(item, index == self.selected_index, width, description, primary_column_width))
        if start > 0 or end < len(self.filtered_items):
            scroll_text = f"  ({self.selected_index + 1}/{len(self.filtered_items)})"
            lines.append(self._theme_text("scrollInfo", truncate_to_width(scroll_text, width - 2)))
        return lines

    def _render_item(
        self,
        item: SelectItem,
        is_selected: bool,
        width: int,
        description_single_line: str | None,
        primary_column_width: int,
    ) -> str:
        prefix = "→ " if is_selected else "  "
        prefix_width = visible_width(prefix)

        if description_single_line and width > 40:
            effective_primary_width = max(1, min(primary_column_width, width - prefix_width - 4))
            max_primary_width = max(1, effective_primary_width - PRIMARY_COLUMN_GAP)
            truncated_value = self._truncate_primary(item, is_selected, max_primary_width, effective_primary_width)
            truncated_value_width = visible_width(truncated_value)
            spacing = " " * max(1, effective_primary_width - truncated_value_width)
            description_start = prefix_width + truncated_value_width + len(spacing)
            remaining_width = width - description_start - 2

            if remaining_width > MIN_DESCRIPTION_WIDTH:
                truncated_description = truncate_to_width(description_single_line, remaining_width)
                if is_selected:
                    return self._theme_text("selectedText", f"{prefix}{truncated_value}{spacing}{truncated_description}")
                return prefix + truncated_value + self._theme_text("description", spacing + truncated_description)

        max_width = max(0, width - prefix_width - 2)
        truncated_value = self._truncate_primary(item, is_selected, max_width, max_width)
        if is_selected:
            return self._theme_text("selectedText", f"{prefix}{truncated_value}")
        return prefix + truncated_value

    def _get_primary_column_width(self) -> int:
        minimum, maximum = self._get_primary_column_bounds()
        widest_primary = 0
        for item in self.filtered_items:
            widest_primary = max(widest_primary, visible_width(self._display_value(item)) + PRIMARY_COLUMN_GAP)
        return max(minimum, min(widest_primary, maximum))

    def _get_primary_column_bounds(self) -> tuple[int, int]:
        raw_min = (
            self._layout_value("minPrimaryColumnWidth", "min_primary_column_width")
            or self._layout_value("maxPrimaryColumnWidth", "max_primary_column_width")
            or DEFAULT_PRIMARY_COLUMN_WIDTH
        )
        raw_max = (
            self._layout_value("maxPrimaryColumnWidth", "max_primary_column_width")
            or self._layout_value("minPrimaryColumnWidth", "min_primary_column_width")
            or DEFAULT_PRIMARY_COLUMN_WIDTH
        )
        min_width = max(1, min(int(raw_min), int(raw_max)))
        max_width = max(1, max(int(raw_min), int(raw_max)))
        return min_width, max_width

    def _truncate_primary(self, item: SelectItem, is_selected: bool, max_width: int, column_width: int) -> str:
        display_value = self._display_value(item)
        truncate_primary = self._layout_value("truncatePrimary", "truncate_primary")
        if callable(truncate_primary):
            value = truncate_primary(
                {
                    "text": display_value,
                    "maxWidth": max_width,
                    "columnWidth": column_width,
                    "item": item,
                    "isSelected": is_selected,
                }
            )
        else:
            value = display_value
        return truncate_to_width(str(value), max_width)

    def _display_value(self, item: SelectItem) -> str:
        return item.label or item.value

    def _layout_value(self, camel_name: str, snake_name: str) -> object:
        if camel_name in self.layout:
            return self.layout[camel_name]
        return self.layout.get(snake_name)

    def _theme_text(self, name: str, text: str) -> str:
        callback: object = None
        if isinstance(self.theme, dict):
            callback = self.theme.get(name)
        elif self.theme is not None:
            callback = getattr(self.theme, name, None)
        if callable(callback):
            return str(callback(text))
        return text

    def _notify_selection_change(self) -> None:
        selected_item = self.get_selected_item()
        if selected_item is None:
            return
        for callback in self._callbacks("on_selection_change", "onSelectionChange"):
            callback(selected_item)

    def _notify_select(self) -> None:
        selected_item = self.get_selected_item()
        if selected_item is None:
            return
        for callback in self._callbacks("on_select", "onSelect"):
            callback(selected_item)

    def _notify_cancel(self) -> None:
        for callback in self._callbacks("on_cancel", "onCancel"):
            callback()

    def _callbacks(self, *names: str) -> list[Callable[..., None]]:
        callbacks: list[Callable[..., None]] = []
        seen: set[int] = set()
        for name in names:
            callback = getattr(self, name, None)
            if not callable(callback):
                continue
            marker = id(callback)
            if marker in seen:
                continue
            seen.add(marker)
            callbacks.append(callback)
        return callbacks


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
        color = _SIGNAL_GLASS_STATUS_COLORS.get(self.kind, _SIGNAL_GLASS_STATUS_COLORS["status"])
        return [_ansi_color(line, color) for line in super().render(width)]

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
        provider: str | None = None,
        thinking_level: str = "off",
        pending: int = 0,
        context_tokens: int | None = None,
        context_threshold: int | None = None,
        context_window: int | None = None,
        context_percent: float | None = None,
        context_percent_unknown: bool = False,
        context_estimate_rough: bool = False,
        total_input: int = 0,
        total_output: int = 0,
        total_cache_read: int = 0,
        total_cache_write: int = 0,
        latest_cache_hit_rate: float | None = None,
        total_cost: float = 0.0,
        using_subscription: bool = False,
        compression_count: int = 0,
        extension_statuses: dict[str, str] | None = None,
        git_branch: str | None = None,
        session_name: str | None = None,
        available_provider_count: int = 0,
        auto_compact_enabled: bool = True,
        model_reasoning: bool = False,
        history_hint: str | None = None,
        home: str | None = None,
    ) -> None:
        self.cwd = cwd
        self.model = model
        self.provider = provider
        self.thinking_level = thinking_level
        self.pending = pending
        self.context_tokens = context_tokens
        self.context_threshold = context_threshold
        self.context_window = context_window
        self.context_percent = context_percent
        self.context_percent_unknown = context_percent_unknown
        self.context_estimate_rough = context_estimate_rough
        self.total_input = total_input
        self.total_output = total_output
        self.total_cache_read = total_cache_read
        self.total_cache_write = total_cache_write
        self.latest_cache_hit_rate = latest_cache_hit_rate
        self.total_cost = total_cost
        self.using_subscription = using_subscription
        self.compression_count = compression_count
        self.extension_statuses = dict(extension_statuses or {})
        self.git_branch = git_branch
        self.session_name = session_name
        self.available_provider_count = available_provider_count
        self.auto_compact_enabled = auto_compact_enabled
        self.model_reasoning = model_reasoning
        self.history_hint = history_hint
        self.home = home

    def render(self, width: int) -> list[str]:
        width = max(1, int(width))
        formatted_cwd = format_cwd_for_footer(self.cwd, self.home or os.environ.get("HOME") or os.environ.get("USERPROFILE"))
        cwd = f"{formatted_cwd} ({self.git_branch})" if self.git_branch else formatted_cwd
        if self.session_name:
            cwd = f"{cwd} • {self.session_name}"
        context_window = self.context_window or self.context_threshold or 0
        if self.context_percent_unknown:
            if self.context_estimate_rough and self.context_tokens is not None and context_window > 0:
                context_percent = (self.context_tokens / context_window) * 100
                context_percent_display = f"~{context_percent:.1f}"
            else:
                context_percent_display = "?"
        elif self.context_percent is not None:
            context_percent = self.context_percent
            prefix = "~" if self.context_estimate_rough else ""
            context_percent_display = f"{prefix}{context_percent:.1f}"
        elif self.context_tokens is not None and context_window > 0:
            context_percent = (self.context_tokens / context_window) * 100
            prefix = "~" if self.context_estimate_rough else ""
            context_percent_display = f"{prefix}{context_percent:.1f}"
        else:
            context_percent = 0.0
            context_percent_display = f"{context_percent:.1f}"
        auto_indicator = " (auto)" if self.auto_compact_enabled else ""
        stats_parts = []
        if self.total_input:
            stats_parts.append(f"↑{_format_footer_tokens(self.total_input)}")
        if self.total_output:
            stats_parts.append(f"↓{_format_footer_tokens(self.total_output)}")
        if self.total_cache_read:
            stats_parts.append(f"R{_format_footer_tokens(self.total_cache_read)}")
        if self.total_cache_write:
            stats_parts.append(f"W{_format_footer_tokens(self.total_cache_write)}")
        if (self.total_cache_read > 0 or self.total_cache_write > 0) and self.latest_cache_hit_rate is not None:
            stats_parts.append(f"CH{self.latest_cache_hit_rate:.1f}%")
        if self.total_cost or self.using_subscription:
            subscription_suffix = " (sub)" if self.using_subscription else ""
            stats_parts.append(f"${self.total_cost:.3f}{subscription_suffix}")
        percent_suffix = "" if context_percent_display == "?" else "%"
        stats_parts.append(f"{context_percent_display}{percent_suffix}/{_format_footer_tokens(context_window)}{auto_indicator}")
        stats_left = " ".join(stats_parts)
        if visible_width(stats_left) > width:
            stats_left = truncate_to_width(stats_left, width, "...")

        right_side_without_provider = self.model
        if self.model_reasoning:
            right_side_without_provider = (
                f"{self.model} • thinking off" if self.thinking_level == "off" else f"{self.model} • {self.thinking_level}"
            )
        right_side = right_side_without_provider
        if self.available_provider_count > 1 and self.provider:
            candidate = f"({self.provider}) {right_side_without_provider}"
            if visible_width(stats_left) + 2 + visible_width(candidate) <= width:
                right_side = candidate

        stats_left_width = visible_width(stats_left)
        right_side_width = visible_width(right_side)
        if stats_left_width + 2 + right_side_width <= width:
            stats_line = stats_left + (" " * (width - stats_left_width - right_side_width)) + right_side
        else:
            available_for_right = width - stats_left_width - 2
            if available_for_right > 0:
                truncated_right = truncate_to_width(right_side, available_for_right, "")
                stats_line = stats_left + (" " * max(0, width - stats_left_width - visible_width(truncated_right))) + truncated_right
            else:
                stats_line = stats_left

        lines = [truncate_to_width(cwd, width, "..."), truncate_to_width(stats_line, width, "")]
        status_line = " ".join(
            _single_line(value)
            for _key, value in sorted(self.extension_statuses.items())
            if value and _single_line(value)
        )
        if status_line:
            lines.append(truncate_to_width(status_line, width, "..."))
        if self.history_hint:
            lines.append(truncate_to_width(_single_line(self.history_hint), width, "..."))
        return [_ansi_color(line, _SIGNAL_GLASS_FOOTER_COLOR) for line in lines]


def _format_footer_tokens(count: int) -> str:
    if count < 1000:
        return str(count)
    if count < 10000:
        return f"{count / 1000:.1f}k"
    if count < 1000000:
        return f"{round(count / 1000)}k"
    if count < 10000000:
        return f"{count / 1000000:.1f}M"
    return f"{round(count / 1000000)}M"


def format_cwd_for_footer(cwd: str, home: str | None) -> str:
    if not home:
        return cwd
    resolved_cwd = os.path.abspath(os.path.expanduser(cwd))
    resolved_home = os.path.abspath(os.path.expanduser(home))
    try:
        relative_to_home = os.path.relpath(resolved_cwd, resolved_home)
    except ValueError:
        return cwd
    is_inside_home = (
        relative_to_home == "."
        or (relative_to_home != ".." and not relative_to_home.startswith(f"..{os.sep}") and not os.path.isabs(relative_to_home))
    )
    if not is_inside_home:
        return cwd
    return "~" if relative_to_home == "." else f"~{os.sep}{relative_to_home}"


formatCwdForFooter = format_cwd_for_footer


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


def _single_line(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").replace("\t", " ").split())
