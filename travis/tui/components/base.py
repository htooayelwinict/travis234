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

class Component:
    """Base component: render(width) -> list of lines (one string per visual line)."""

    def render(self, width: int) -> list[str]:
        raise NotImplementedError

    def handle_input(self, data: str) -> None:  # pragma: no cover - optional
        pass

    def invalidate(self) -> None:
        pass

    def set_theme_context(self, theme_context: object | None) -> None:
        self.theme_context = theme_context
        self.invalidate()


class Container(Component):
    def __init__(
        self,
        children: Optional[list[Component]] = None,
        *,
        theme_context: object | None = None,
    ) -> None:
        self.children: list[Component] = []
        self.theme_context = theme_context
        for child in children or []:
            self.add(child)

    def add(self, component: Component) -> Component:
        if self.theme_context is not None:
            setter = getattr(component, "set_theme_context", None)
            if callable(setter):
                setter(self.theme_context)
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

    def set_theme_context(self, theme_context: object | None) -> None:
        self.theme_context = theme_context
        for child in self.children:
            setter = getattr(child, "set_theme_context", None)
            if callable(setter):
                setter(theme_context)
        self.invalidate()


class Text(Component):
    def __init__(
        self,
        text: str = "",
        *,
        theme_context: object | None = None,
        role: str | None = None,
        background_role: str | None = None,
    ) -> None:
        self._text = text
        self.theme_context = theme_context
        self.role = role
        self.background_role = background_role
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
            return self._style_lines(self._cache)
        lines: list[str] = []
        for raw in self._text.split("\n"):
            lines.extend(wrap_text(raw, width))
        self._cache = lines
        self._cache_key = key
        return self._style_lines(lines)

    def _style_lines(self, lines: list[str]) -> list[str]:
        context = self.theme_context
        theme = getattr(context, "theme", None) if context is not None else None
        if theme is None:
            return lines
        styled: list[str] = []
        for line in lines:
            value = theme.fg(self.role, line) if self.role else line
            if self.background_role:
                value = theme.bg(self.background_role, value)
            styled.append(value)
        return styled


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

class Spacer(Component):
    def __init__(self, height: int = 1) -> None:
        self.height = height

    def render(self, width: int) -> list[str]:
        return ["" for _ in range(self.height)]


class Box(Component):
    """A child wrapped in a simple single-line border (optional title)."""

    def __init__(
        self,
        child: Component,
        title: str = "",
        *,
        theme_context: object | None = None,
        border_role: str = "border",
        background_role: str | None = None,
        title_role: str = "accent",
        padding: int = 0,
        unicode: bool = False,
        accent_rail: bool = False,
    ) -> None:
        self.child = child
        self.title = title
        self.theme_context = theme_context
        self.border_role = border_role
        self.background_role = background_role
        self.title_role = title_role
        self.padding = max(0, int(padding))
        self.unicode = bool(unicode)
        self.accent_rail = bool(accent_rail)
        if theme_context is not None:
            self.child.set_theme_context(theme_context)

    def invalidate(self) -> None:
        self.child.invalidate()

    def set_theme_context(self, theme_context: object | None) -> None:
        self.theme_context = theme_context
        self.child.set_theme_context(theme_context)
        self.invalidate()

    def render(self, width: int) -> list[str]:
        if self.accent_rail:
            inner_width = max(1, width - 2 - self.padding * 2)
            lines = [*([""] * self.padding), *self.child.render(inner_width), *([""] * self.padding)]
            rows: list[str] = []
            for line in lines:
                content = (" " * self.padding) + truncate_to_width(line, inner_width) + (" " * self.padding)
                content += " " * max(0, width - 2 - visible_width(content))
                rows.append(self._fg(self.border_role, "▌") + " " + self._bg(content))
            return rows
        inner_width = max(1, width - 2)
        top_label = f" {self.title} " if self.title else ""
        top_left, top_right, bottom_left, bottom_right, horizontal, vertical = (
            ("╭", "╮", "╰", "╯", "─", "│") if self.unicode else ("+", "+", "+", "+", "-", "|")
        )
        rendered_label = self._fg(self.title_role, top_label) if top_label else ""
        top = self._fg(self.border_role, top_left) + rendered_label + self._fg(
            self.border_role,
            horizontal * max(0, inner_width - visible_width(top_label)) + top_right,
        )
        rows = [top]
        for line in self.child.render(inner_width):
            padded = truncate_to_width(line, inner_width)
            pad = inner_width - visible_width(padded)
            content = self._bg(padded + (" " * max(0, pad)))
            rows.append(self._fg(self.border_role, vertical) + content + self._fg(self.border_role, vertical))
        rows.append(self._fg(self.border_role, bottom_left + horizontal * inner_width + bottom_right))
        return rows

    def _fg(self, role: str, text: str) -> str:
        theme = getattr(self.theme_context, "theme", None)
        return theme.fg(role, text) if theme is not None else text

    def _bg(self, text: str) -> str:
        theme = getattr(self.theme_context, "theme", None)
        return theme.bg(self.background_role, text) if theme is not None and self.background_role else text

def _single_line(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").replace("\t", " ").split())
