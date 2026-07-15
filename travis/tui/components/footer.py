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

from travis.tui.components.base import Component, Text, _single_line

_STATUS_ROLES = {
    "compact": "accent",
    "info": "accent",
    "note": "muted",
    "warning": "warning",
    "error": "error",
    "help": "accent",
    "success": "success",
    "select": "accent",
    "auth": "accent",
    "model": "accent",
    "input": "accent",
    "editor": "accent",
    "theme": "accent",
}

class StatusLine(Text):
    def __init__(
        self,
        message: str = "",
        kind: str = "status",
        *,
        theme_context: object | None = None,
    ) -> None:
        self.kind = kind
        self.visible = True
        self._message = ""
        self._indicator: str | None = None
        super().__init__("", theme_context=theme_context)
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
        lines = super().render(width)
        theme = getattr(self.theme_context, "theme", None)
        if theme is None:
            return lines
        role = _STATUS_ROLES.get(self.kind, "text")
        return [theme.fg(role, line) for line in lines]

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
        theme_context: object | None = None,
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
        self.theme_context = theme_context

    def set_theme_context(self, theme_context: object | None) -> None:
        self.theme_context = theme_context

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
        detail_segments: list[tuple[int, int, str]] = []
        if self.total_input:
            detail_segments.append((3, 0, f"↑{_format_footer_tokens(self.total_input)}"))
        if self.total_output:
            detail_segments.append((3, 1, f"↓{_format_footer_tokens(self.total_output)}"))
        if self.pending:
            detail_segments.append((2, 2, f"P{self.pending}"))
        if self.total_cache_read:
            detail_segments.append((6, 3, f"R{_format_footer_tokens(self.total_cache_read)}"))
        if self.total_cache_write:
            detail_segments.append((6, 4, f"W{_format_footer_tokens(self.total_cache_write)}"))
        if (self.total_cache_read > 0 or self.total_cache_write > 0) and self.latest_cache_hit_rate is not None:
            detail_segments.append((6, 5, f"CH{self.latest_cache_hit_rate:.1f}%"))
        if self.total_cost or self.using_subscription:
            subscription_suffix = " (sub)" if self.using_subscription else ""
            detail_segments.append((5, 6, f"${self.total_cost:.3f}{subscription_suffix}"))
        if self.compression_count:
            detail_segments.append((4, 7, f"C{self.compression_count}"))
        percent_suffix = "" if context_percent_display == "?" else "%"
        context_segment = f"{context_percent_display}{percent_suffix}/{_format_footer_tokens(context_window)}{auto_indicator}"
        ordered_details = [text for _priority, _order, text in sorted(detail_segments, key=lambda item: item[1])]
        stats_left = " ".join([*ordered_details, context_segment])
        if visible_width(stats_left) > width:
            selected: list[tuple[int, str]] = []
            for priority, order, text in sorted(detail_segments, key=lambda item: (item[0], item[1])):
                candidate_details = [value for _index, value in sorted([*selected, (order, text)])]
                candidate = " ".join([*candidate_details, context_segment])
                if visible_width(candidate) <= width:
                    selected.append((order, text))
            selected_details = [text for _order, text in sorted(selected)]
            stats_left = " ".join([*selected_details, context_segment])
            if visible_width(stats_left) > width:
                stats_left = truncate_to_width(context_segment, width, "")

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
        theme = getattr(self.theme_context, "theme", None)
        if theme is None:
            return lines
        context_role = "warning" if self.context_percent_unknown or context_percent >= 75 else "muted"
        if not self.context_percent_unknown and context_percent >= 90:
            context_role = "error"
        roles = ["accent", context_role, *(["muted"] if status_line else []), *(["dim"] if self.history_hint else [])]
        return [theme.fg(roles[index], line) for index, line in enumerate(lines)]


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
