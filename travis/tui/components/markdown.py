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

from travis.tui.components.base import Text

_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.*?)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]*)`")


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
