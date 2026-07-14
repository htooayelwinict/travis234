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

from travis.tui.components.base import Component

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
