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

def _single_line(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").replace("\t", " ").split())
