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
        self.on_abort: Callable[[], object] | None = None
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
            if callable(self.on_abort):
                self.on_abort()


    def dispose(self) -> None:
        self.stop()
