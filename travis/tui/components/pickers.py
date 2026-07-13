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

from travis.tui.components.base import Component, _single_line
from travis.tui.components.editor import Input

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
        self.on_select: Callable[[SelectItem], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self.on_selection_change: Callable[[SelectItem], None] | None = None

    def set_filter(self, value: str) -> None:
        needle = value.lower()
        self.filtered_items = [item for item in self.items if item.value.lower().startswith(needle)]
        self.selected_index = 0


    def set_selected_index(self, index: int) -> None:
        if not self.filtered_items:
            self.selected_index = 0
            return
        self.selected_index = max(0, min(int(index), len(self.filtered_items) - 1))


    def get_selected_item(self) -> SelectItem | None:
        if 0 <= self.selected_index < len(self.filtered_items):
            return self.filtered_items[self.selected_index]
        return None


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
