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

class SimpleAutocompleteProvider:
    """Small Python equivalent of travis-tui's CombinedAutocompleteProvider."""

    def __init__(self, commands: list[dict[str, Any]] | None = None) -> None:
        self.commands = list(commands or [])
        self._trigger_characters: list[str] = []

    @property
    def trigger_characters(self) -> list[str]:
        return self._trigger_characters

    @trigger_characters.setter
    def trigger_characters(self, value: list[str]) -> None:
        self._trigger_characters = list(value)

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


    def should_trigger_file_completion(self, lines: list[str], cursor_line: int, cursor_col: int) -> bool:
        return True



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


    def should_trigger_file_completion(self, lines: list[str], cursor_line: int, cursor_col: int) -> bool:
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        text_before_cursor = current_line[:cursor_col]
        stripped = text_before_cursor.strip()
        if stripped.startswith("/") and " " not in stripped:
            return False
        return True


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
