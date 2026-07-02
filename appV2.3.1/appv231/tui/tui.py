"""Differential rendering engine. Port of pi/packages/tui/src/tui.ts (TUI core)."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
import os
import re
import threading

from appv231.tui.component import CURSOR_MARKER, Container
from appv231.tui.keys import is_key_release
from appv231.tui.terminal import Terminal
from appv231.tui.terminal_colors import is_osc11_background_color_response, parse_osc11_background_color
from appv231.tui.terminal_image import (
    delete_kitty_image,
    get_capabilities,
    is_image_line,
    set_cell_dimensions,
)
from appv231.tui.utils import (
    extract_segments,
    normalize_terminal_output,
    slice_by_column,
    slice_with_width,
    truncate_to_width,
    visible_width,
)

_CLEAR_SCREEN = "\x1b[2J\x1b[H\x1b[3J"
_SEGMENT_RESET = "\x1b[0m\x1b]8;;\x07"
_SYNC_BEGIN = "\x1b[?2026h"
_SYNC_END = "\x1b[?2026l"
_KITTY_SEQUENCE_PREFIX = "\x1b_G"
_CELL_SIZE_RESPONSE_RE = re.compile(r"^\x1b\[6;(\d+);(\d+)t$")
_SGR_MOUSE_RE = re.compile(r"^\x1b\[<(\d+);(\d+);(\d+)([Mm])$")
_RXVT_MOUSE_RE = re.compile(r"^\x1b\[(\d+);(\d+);(\d+)([Mm])$")
_X10_MOUSE_RE = re.compile(r"^\x1b\[M(.)(.)(.)$")
_PAGE_UP = "\x1b[5~"
_PAGE_DOWN = "\x1b[6~"
_END_KEYS = {"\x1b[F", "\x1b[4~"}
_MOUSE_WHEEL_STEP_LINES = 3


def _move_to_line(index: int) -> str:
    return f"\x1b[{index + 1};1H"


def _clear_line() -> str:
    return "\x1b[2K"


def _parse_kitty_image_header(line: str) -> dict[str, object] | None:
    sequence_start = line.find(_KITTY_SEQUENCE_PREFIX)
    if sequence_start == -1:
        return None

    params_start = sequence_start + len(_KITTY_SEQUENCE_PREFIX)
    params_end = line.find(";", params_start)
    if params_end == -1:
        return None

    ids: list[int] = []
    rows = 1
    params = line[params_start:params_end]
    for param in params.split(","):
        key, separator, value = param.partition("=")
        if not separator:
            continue
        try:
            number_value = int(value)
        except ValueError:
            continue
        if number_value <= 0 or number_value > 0xFFFFFFFF:
            continue
        if key == "i":
            ids.append(number_value)
        elif key == "r":
            rows = number_value
    return {"ids": ids, "rows": rows}


def _extract_kitty_image_ids(line: str) -> list[int]:
    header = _parse_kitty_image_header(line)
    return list(header["ids"]) if header is not None else []


def _extract_kitty_image_rows(line: str) -> int:
    header = _parse_kitty_image_header(line)
    return int(header["rows"]) if header is not None else 1


def is_focusable(component: object | None) -> bool:
    return component is not None and hasattr(component, "focused")


isFocusable = is_focusable


def _parse_size_value(value: object, reference_size: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)%", value)
        if match:
            return int(reference_size * float(match.group(1)) / 100)
    return None


@dataclass
class RenderInfo:
    full: bool
    first_changed: int
    last_changed: int
    lines: list[str]
    cursor_position: tuple[int, int] | None = None


class TUI(Container):
    """Container that diff-renders its frame to a Terminal, emitting minimal ANSI."""

    def __init__(
        self,
        terminal: Terminal,
        show_hardware_cursor: bool | None = None,
        *,
        showHardwareCursor: bool | None = None,
    ) -> None:
        super().__init__()
        self.terminal = terminal
        self.previous_lines: list[str] = []
        self._last_width: int | None = None
        self._max_lines_rendered = 0
        self._hardware_cursor_row = 0
        self._clear_on_shrink = os.environ.get("PI_CLEAR_ON_SHRINK") == "1"
        self._full_redraw_count = 0
        explicit_hardware_cursor = show_hardware_cursor if show_hardware_cursor is not None else showHardwareCursor
        self._show_hardware_cursor = (
            os.environ.get("PI_HARDWARE_CURSOR") == "1"
            if explicit_hardware_cursor is None
            else bool(explicit_hardware_cursor)
        )
        self.last_render: RenderInfo | None = None
        self._started = False
        self._input_listeners: list[Callable[[str], object]] = []
        self._focused_component: Container | None = None
        self._pending_osc11_background_queries: list[Future[dict[str, int] | None]] = []
        self._pending_osc11_background_replies = 0
        self._previous_kitty_image_ids: set[int] = set()
        self._focus_order_counter = 0
        self._overlay_stack: list[dict[str, object]] = []
        self._scroll_offset_from_bottom = 0
        self._logical_line_count = 0
        self._scroll_listeners: list[Callable[[], None]] = []

    @property
    def full_redraws(self) -> int:
        return self._full_redraw_count

    fullRedraws = full_redraws

    def get_show_hardware_cursor(self) -> bool:
        return self._show_hardware_cursor

    getShowHardwareCursor = get_show_hardware_cursor

    def set_show_hardware_cursor(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._show_hardware_cursor == enabled:
            return
        self._show_hardware_cursor = enabled
        if not enabled:
            self.terminal.hide_cursor()
        self.request_render()

    setShowHardwareCursor = set_show_hardware_cursor

    def get_clear_on_shrink(self) -> bool:
        return self._clear_on_shrink

    getClearOnShrink = get_clear_on_shrink

    def set_clear_on_shrink(self, enabled: bool) -> None:
        self._clear_on_shrink = bool(enabled)

    setClearOnShrink = set_clear_on_shrink

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.terminal.start(self._handle_terminal_input, lambda: self.request_render(force=True))
        self.terminal.hide_cursor()
        self._query_cell_size()
        self.request_render()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        drain_input = getattr(self.terminal, "drain_input", None) or getattr(self.terminal, "drainInput", None)
        if callable(drain_input):
            drain_input(1000)
        self.terminal.show_cursor()
        self.terminal.stop()

    def add_input_listener(self, listener: Callable[[str], object]) -> Callable[[], None]:
        self._input_listeners.append(listener)

        def unsubscribe() -> None:
            self.remove_input_listener(listener)

        return unsubscribe

    addInputListener = add_input_listener

    def remove_input_listener(self, listener: Callable[[str], object]) -> None:
        if listener in self._input_listeners:
            self._input_listeners.remove(listener)

    removeInputListener = remove_input_listener

    def add_scroll_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._scroll_listeners.append(listener)

        def unsubscribe() -> None:
            self.remove_scroll_listener(listener)

        return unsubscribe

    addScrollListener = add_scroll_listener

    def remove_scroll_listener(self, listener: Callable[[], None]) -> None:
        if listener in self._scroll_listeners:
            self._scroll_listeners.remove(listener)

    removeScrollListener = remove_scroll_listener

    def set_focus(self, component: object | None) -> None:
        if self._focused_component is component:
            return
        if is_focusable(self._focused_component):
            self._focused_component.focused = False
        self._focused_component = component
        if is_focusable(component):
            component.focused = True

    setFocus = set_focus

    @property
    def focused_component(self):
        return self._focused_component

    def show_overlay(self, component: Container, options: dict[str, object] | None = None):
        opts = dict(options or {})
        self._focus_order_counter += 1
        entry: dict[str, object] = {
            "component": component,
            "options": opts,
            "pre_focus": self._focused_component,
            "hidden": False,
            "focus_order": self._focus_order_counter,
        }
        self._overlay_stack.append(entry)
        if not opts.get("nonCapturing") and self._is_overlay_visible(entry):
            self.set_focus(component)
        self.terminal.hide_cursor()
        self.request_render()
        return _OverlayHandle(self, entry)

    showOverlay = show_overlay

    def hide_overlay(self) -> None:
        if not self._overlay_stack:
            return
        entry = self._overlay_stack.pop()
        component = entry["component"]
        if self._focused_component is component:
            top_visible = self._get_topmost_visible_overlay()
            self.set_focus(top_visible["component"] if top_visible is not None else entry.get("pre_focus"))
        if not self._overlay_stack:
            self.terminal.hide_cursor()
        self.request_render()

    hideOverlay = hide_overlay

    def has_overlay(self) -> bool:
        return any(self._is_overlay_visible(entry) for entry in self._overlay_stack)

    hasOverlay = has_overlay

    def invalidate(self) -> None:
        super().invalidate()
        for entry in self._overlay_stack:
            component = entry.get("component")
            invalidate = getattr(component, "invalidate", None)
            if callable(invalidate):
                invalidate()

    def _handle_terminal_input(self, data: str) -> None:
        if self._consume_osc11_background_response(data):
            return

        current = data
        for listener in list(self._input_listeners):
            result = listener(current)
            if isinstance(result, dict):
                if result.get("consume"):
                    return
                if result.get("data") is not None:
                    current = str(result["data"])
        if not current:
            return
        if self._consume_cell_size_response(current):
            return
        focused_overlay = next(
            (entry for entry in self._overlay_stack if entry.get("component") is self._focused_component),
            None,
        )
        if focused_overlay is not None and not self._is_overlay_visible(focused_overlay):
            top_visible = self._get_topmost_visible_overlay()
            self.set_focus(top_visible["component"] if top_visible is not None else focused_overlay.get("pre_focus"))
        focus_is_overlay = any(
            entry.get("component") is self._focused_component and self._is_overlay_visible(entry)
            for entry in self._overlay_stack
        )
        if not focus_is_overlay and self._handle_scroll_input(current):
            return
        if self._focused_component is not None and hasattr(self._focused_component, "handle_input"):
            if is_key_release(current) and not _wants_key_release(self._focused_component):
                return
            self._focused_component.handle_input(current)
            self.request_render()

    def _handle_scroll_input(self, data: str) -> bool:
        page_size = max(1, self.terminal.rows - 1)
        if data == _PAGE_UP:
            self.scroll_by(-page_size)
            return True
        if data == _PAGE_DOWN:
            self.scroll_by(page_size)
            return True
        if data in _END_KEYS:
            self.scroll_to_bottom()
            return True
        mouse_match = _SGR_MOUSE_RE.match(data)
        if mouse_match is not None:
            button_code = int(mouse_match.group(1))
            self._handle_mouse_button_code(button_code)
            return True
        rxvt_mouse_match = _RXVT_MOUSE_RE.match(data)
        if rxvt_mouse_match is not None:
            button_code = int(rxvt_mouse_match.group(1))
            self._handle_mouse_button_code(button_code)
            return True
        legacy_mouse_match = _X10_MOUSE_RE.match(data)
        if legacy_mouse_match is None:
            return False
        button_code = ord(legacy_mouse_match.group(1)) - 32
        if button_code < 0:
            return True
        self._handle_mouse_button_code(button_code)
        return True

    def _handle_mouse_button_code(self, button_code: int) -> None:
        if button_code & 64:
            wheel_button = button_code & 3
            if wheel_button == 0:
                self.scroll_by(-_MOUSE_WHEEL_STEP_LINES)
            elif wheel_button == 1:
                self.scroll_by(_MOUSE_WHEEL_STEP_LINES)

    def _query_cell_size(self) -> None:
        if not get_capabilities()["images"]:
            return
        self.terminal.write("\x1b[16t")

    def _consume_cell_size_response(self, data: str) -> bool:
        match = _CELL_SIZE_RESPONSE_RE.match(data)
        if match is None:
            return False

        height_px = int(match.group(1))
        width_px = int(match.group(2))
        if height_px <= 0 or width_px <= 0:
            return True

        set_cell_dimensions({"widthPx": width_px, "heightPx": height_px})
        self.invalidate()
        self.request_render()
        return True

    def _consume_osc11_background_response(self, data: str) -> bool:
        if self._pending_osc11_background_replies <= 0:
            return False
        if not is_osc11_background_color_response(data):
            return False

        rgb = parse_osc11_background_color(data)
        self._pending_osc11_background_replies -= 1
        query = self._pending_osc11_background_queries.pop(0) if self._pending_osc11_background_queries else None
        if query is not None and not query.done():
            query.set_result(rgb)
        return True

    def query_terminal_background_color(self, options: dict[str, object] | None = None) -> Future[dict[str, int] | None]:
        opts = options or {}
        timeout_value = opts.get("timeout_ms", opts.get("timeoutMs", 0))
        try:
            timeout_ms = float(timeout_value or 0)
        except (TypeError, ValueError):
            timeout_ms = 0.0

        query: Future[dict[str, int] | None] = Future()
        if timeout_ms > 0:
            timer = threading.Timer(timeout_ms / 1000.0, self._timeout_osc11_background_query, args=(query,))
            timer.daemon = True
            timer.start()
            query.add_done_callback(lambda _query: timer.cancel())

        self._pending_osc11_background_queries.append(query)
        self._pending_osc11_background_replies += 1
        self.terminal.write("\x1b]11;?\x07")
        return query

    queryTerminalBackgroundColor = query_terminal_background_color

    @staticmethod
    def _timeout_osc11_background_query(query: Future[dict[str, int] | None]) -> None:
        if not query.done():
            query.set_result(None)

    def request_render(self, force: bool = False) -> RenderInfo:
        return self._do_render(force)

    def _do_render(self, force: bool) -> RenderInfo:
        width = self.terminal.columns
        rendered_lines = super().render(width)
        if self._overlay_stack:
            rendered_lines = self._composite_overlays(rendered_lines, width, self.terminal.rows)
        logical_lines = [
            line if is_image_line(line) else truncate_to_width(line, width)
            for line in rendered_lines
        ]
        self._logical_line_count = len(logical_lines)
        self._clamp_scroll_offset()
        viewport_top = self._viewport_top(len(logical_lines), force_bottom=self.has_overlay())
        cursor_position = self._extract_cursor_position(logical_lines, viewport_top)
        new_lines = self._viewport_lines(logical_lines, viewport_top)

        size_changed = self._last_width is not None and self._last_width != width
        first_render = not self.previous_lines
        clear_on_shrink = self._clear_on_shrink and bool(self.previous_lines) and len(new_lines) < self._max_lines_rendered

        if force or first_render or size_changed or clear_on_shrink:
            should_clear = force or size_changed or clear_on_shrink
            info = self._full_render(new_lines, cursor_position, clear=should_clear)
        else:
            info = self._diff_render(new_lines, cursor_position)

        self.previous_lines = new_lines
        self._previous_kitty_image_ids = self._collect_kitty_image_ids(new_lines)
        self._last_width = width
        if info.full and (force or size_changed or clear_on_shrink):
            self._max_lines_rendered = len(new_lines)
        else:
            self._max_lines_rendered = max(self._max_lines_rendered, len(new_lines))
        self.last_render = info
        return info

    def scroll_by(self, delta: int) -> int:
        old_offset = self._scroll_offset_from_bottom
        max_offset = self._max_scroll_offset()
        if delta > 0:
            new_offset = max(0, old_offset - int(delta))
        elif delta < 0:
            new_offset = min(max_offset, old_offset + abs(int(delta)))
        else:
            return 0
        if new_offset == old_offset:
            return 0

        self._scroll_offset_from_bottom = new_offset
        self._notify_scroll_listeners()
        self.request_render()
        return old_offset - new_offset

    scrollBy = scroll_by

    def scroll_to_bottom(self) -> None:
        if self._scroll_offset_from_bottom == 0:
            return
        self._scroll_offset_from_bottom = 0
        self._notify_scroll_listeners()
        self.request_render()

    scrollToBottom = scroll_to_bottom

    def is_scrolled(self) -> bool:
        return self._scroll_offset_from_bottom > 0

    isScrolled = is_scrolled

    def _notify_scroll_listeners(self) -> None:
        for listener in list(self._scroll_listeners):
            listener()

    def _max_scroll_offset(self, line_count: int | None = None) -> int:
        rows = max(1, self.terminal.rows)
        total = self._logical_line_count if line_count is None else line_count
        return max(0, total - rows)

    def _clamp_scroll_offset(self) -> None:
        self._scroll_offset_from_bottom = min(max(0, self._scroll_offset_from_bottom), self._max_scroll_offset())

    def _viewport_top(self, line_count: int, *, force_bottom: bool = False) -> int:
        rows = max(1, self.terminal.rows)
        bottom_top = max(0, line_count - rows)
        offset = 0 if force_bottom else min(self._scroll_offset_from_bottom, bottom_top)
        return max(0, bottom_top - offset)

    def _viewport_lines(self, lines: list[str], viewport_top: int) -> list[str]:
        rows = max(1, self.terminal.rows)
        return lines[viewport_top : viewport_top + rows]

    def _extract_cursor_position(self, lines: list[str], viewport_top: int) -> tuple[int, int] | None:
        viewport_bottom = min(len(lines), viewport_top + max(1, self.terminal.rows))
        for row in range(viewport_bottom - 1, viewport_top - 1, -1):
            marker_index = lines[row].find(CURSOR_MARKER)
            if marker_index == -1:
                continue
            before_marker = lines[row][:marker_index]
            lines[row] = lines[row][:marker_index] + lines[row][marker_index + len(CURSOR_MARKER) :]
            return row - viewport_top, visible_width(before_marker)
        return None

    def _full_render(self, new_lines: list[str], cursor_position: tuple[int, int] | None, *, clear: bool) -> RenderInfo:
        self._full_redraw_count += 1
        clear_sequence = self._delete_kitty_images(self._previous_kitty_image_ids) + _CLEAR_SCREEN if clear else ""
        self.terminal.write(
            _SYNC_BEGIN + clear_sequence + "\r\n".join(_terminal_line(line) for line in new_lines) + _SYNC_END
        )
        self._hardware_cursor_row = max(0, len(new_lines) - 1)
        self._position_hardware_cursor(cursor_position, len(new_lines))
        return RenderInfo(
            full=True,
            first_changed=0,
            last_changed=max(0, len(new_lines) - 1),
            lines=new_lines,
            cursor_position=cursor_position,
        )

    def _diff_render(self, new_lines: list[str], cursor_position: tuple[int, int] | None) -> RenderInfo:
        old_lines = self.previous_lines
        max_len = max(len(old_lines), len(new_lines))

        first_changed = -1
        for index in range(max_len):
            old = old_lines[index] if index < len(old_lines) else None
            new = new_lines[index] if index < len(new_lines) else None
            if old != new:
                first_changed = index
                break
        if first_changed == -1:
            self._position_hardware_cursor(cursor_position, len(new_lines))
            return RenderInfo(
                full=False,
                first_changed=-1,
                last_changed=-1,
                lines=new_lines,
                cursor_position=cursor_position,
            )

        last_changed = first_changed
        for index in range(max_len - 1, first_changed - 1, -1):
            old = old_lines[index] if index < len(old_lines) else None
            new = new_lines[index] if index < len(new_lines) else None
            if old != new:
                last_changed = index
                break

        expanded_range = self._expand_changed_range_for_kitty_images(first_changed, last_changed, new_lines)
        first_changed = expanded_range["first_changed"]
        last_changed = expanded_range["last_changed"]

        buffer: list[str] = []
        delete_sequence = self._delete_changed_kitty_images(first_changed, last_changed)
        if delete_sequence:
            buffer.append(delete_sequence)
        for index in range(first_changed, last_changed + 1):
            line = new_lines[index] if index < len(new_lines) else ""
            buffer.append(_move_to_line(index) + _clear_line() + _terminal_line(line))
        self.terminal.write(_SYNC_BEGIN + "".join(buffer) + _SYNC_END)
        self._hardware_cursor_row = max(0, last_changed)
        self._position_hardware_cursor(cursor_position, len(new_lines))
        return RenderInfo(
            full=False,
            first_changed=first_changed,
            last_changed=last_changed,
            lines=new_lines,
            cursor_position=cursor_position,
        )

    def _position_hardware_cursor(self, cursor_position: tuple[int, int] | None, total_lines: int) -> None:
        if not self._show_hardware_cursor:
            return
        if cursor_position is None or total_lines <= 0:
            self.terminal.hide_cursor()
            return

        target_row = max(0, min(cursor_position[0], total_lines - 1))
        target_col = max(0, cursor_position[1])
        row_delta = target_row - self._hardware_cursor_row
        buffer = ""
        if row_delta > 0:
            buffer += f"\x1b[{row_delta}B"
        elif row_delta < 0:
            buffer += f"\x1b[{-row_delta}A"
        buffer += f"\x1b[{target_col + 1}G"
        if buffer:
            self.terminal.write(buffer)
        self._hardware_cursor_row = target_row
        self.terminal.show_cursor()

    def _collect_kitty_image_ids(self, lines: list[str]) -> set[int]:
        ids: set[int] = set()
        for line in lines:
            ids.update(_extract_kitty_image_ids(line))
        return ids

    @staticmethod
    def _delete_kitty_images(ids: set[int]) -> str:
        return "".join(delete_kitty_image(image_id) for image_id in ids)

    def _get_kitty_image_reserved_rows(self, lines: list[str], index: int, max_index: int | None = None) -> int:
        rows = _extract_kitty_image_rows(lines[index] if index < len(lines) else "")
        if rows <= 1:
            return 1
        max_index = len(lines) - 1 if max_index is None else max_index
        max_rows = min(rows, max_index - index + 1, len(lines) - index)
        reserved_rows = 1
        while reserved_rows < max_rows:
            line = lines[index + reserved_rows] if index + reserved_rows < len(lines) else ""
            if is_image_line(line) or visible_width(line) > 0:
                break
            reserved_rows += 1
        return reserved_rows

    def _expand_changed_range_for_kitty_images(
        self,
        first_changed: int,
        last_changed: int,
        new_lines: list[str],
    ) -> dict[str, int]:
        expanded_first_changed = first_changed
        expanded_last_changed = last_changed

        def expand_for_lines(lines: list[str]) -> None:
            nonlocal expanded_first_changed, expanded_last_changed
            for index, line in enumerate(lines):
                if not _extract_kitty_image_ids(line):
                    continue
                block_end = index + self._get_kitty_image_reserved_rows(lines, index) - 1
                if index >= first_changed or (index <= last_changed and block_end >= first_changed):
                    expanded_first_changed = min(expanded_first_changed, index)
                    expanded_last_changed = max(expanded_last_changed, block_end)

        expand_for_lines(self.previous_lines)
        expand_for_lines(new_lines)
        return {"first_changed": expanded_first_changed, "last_changed": expanded_last_changed}

    def _delete_changed_kitty_images(self, first_changed: int, last_changed: int) -> str:
        if first_changed < 0 or last_changed < first_changed:
            return ""

        ids: set[int] = set()
        max_line = min(last_changed, len(self.previous_lines) - 1)
        for index in range(first_changed, max_line + 1):
            ids.update(_extract_kitty_image_ids(self.previous_lines[index] if index < len(self.previous_lines) else ""))
        return self._delete_kitty_images(ids)

    def _is_overlay_visible(self, entry: dict[str, object]) -> bool:
        if entry.get("hidden"):
            return False
        options = entry.get("options")
        visible = options.get("visible") if isinstance(options, dict) else None
        if callable(visible):
            return bool(visible(self.terminal.columns, self.terminal.rows))
        return True

    def _get_topmost_visible_overlay(self) -> dict[str, object] | None:
        topmost: dict[str, object] | None = None
        for entry in self._overlay_stack:
            options = entry.get("options")
            if isinstance(options, dict) and options.get("nonCapturing"):
                continue
            if not self._is_overlay_visible(entry):
                continue
            if topmost is None or int(entry.get("focus_order", 0)) > int(topmost.get("focus_order", 0)):
                topmost = entry
        return topmost

    def _resolve_overlay_layout(
        self,
        options: dict[str, object] | None,
        overlay_height: int,
        term_width: int,
        term_height: int,
    ) -> dict[str, int | None]:
        opt = options or {}
        margin_value = opt.get("margin")
        if isinstance(margin_value, (int, float)):
            margin = {"top": int(margin_value), "right": int(margin_value), "bottom": int(margin_value), "left": int(margin_value)}
        elif isinstance(margin_value, dict):
            margin = margin_value
        else:
            margin = {}

        margin_top = max(0, int(margin.get("top", 0)))
        margin_right = max(0, int(margin.get("right", 0)))
        margin_bottom = max(0, int(margin.get("bottom", 0)))
        margin_left = max(0, int(margin.get("left", 0)))
        available_width = max(1, term_width - margin_left - margin_right)
        available_height = max(1, term_height - margin_top - margin_bottom)

        resolved_width = _parse_size_value(opt.get("width"), term_width)
        width = resolved_width if resolved_width is not None else min(80, available_width)
        if opt.get("minWidth") is not None:
            width = max(width, int(opt["minWidth"]))
        width = max(1, min(width, available_width))

        max_height = _parse_size_value(opt.get("maxHeight"), term_height)
        if max_height is not None:
            max_height = max(1, min(max_height, available_height))
        effective_height = min(overlay_height, max_height) if max_height is not None else overlay_height

        row_value = opt.get("row")
        if row_value is not None:
            parsed_row = _parse_size_value(row_value, available_height)
            if isinstance(row_value, str) and row_value.endswith("%") and parsed_row is not None:
                max_row = max(0, available_height - effective_height)
                row = margin_top + int(max_row * float(row_value[:-1]) / 100)
            else:
                row = int(parsed_row if parsed_row is not None else 0)
        else:
            row = self._resolve_anchor_row(str(opt.get("anchor", "center")), effective_height, available_height, margin_top)

        col_value = opt.get("col")
        if col_value is not None:
            parsed_col = _parse_size_value(col_value, available_width)
            if isinstance(col_value, str) and col_value.endswith("%") and parsed_col is not None:
                max_col = max(0, available_width - width)
                col = margin_left + int(max_col * float(col_value[:-1]) / 100)
            else:
                col = int(parsed_col if parsed_col is not None else 0)
        else:
            col = self._resolve_anchor_col(str(opt.get("anchor", "center")), width, available_width, margin_left)

        if opt.get("offsetY") is not None:
            row += int(opt["offsetY"])
        if opt.get("offsetX") is not None:
            col += int(opt["offsetX"])

        row = max(margin_top, min(row, term_height - margin_bottom - effective_height))
        col = max(margin_left, min(col, term_width - margin_right - width))
        return {"width": width, "row": row, "col": col, "max_height": max_height}

    @staticmethod
    def _resolve_anchor_row(anchor: str, height: int, available_height: int, margin_top: int) -> int:
        if anchor in {"top-left", "top-center", "top-right"}:
            return margin_top
        if anchor in {"bottom-left", "bottom-center", "bottom-right"}:
            return margin_top + available_height - height
        return margin_top + (available_height - height) // 2

    @staticmethod
    def _resolve_anchor_col(anchor: str, width: int, available_width: int, margin_left: int) -> int:
        if anchor in {"top-left", "left-center", "bottom-left"}:
            return margin_left
        if anchor in {"top-right", "right-center", "bottom-right"}:
            return margin_left + available_width - width
        return margin_left + (available_width - width) // 2

    def _composite_overlays(self, lines: list[str], term_width: int, term_height: int) -> list[str]:
        if not self._overlay_stack:
            return lines

        result = list(lines)
        rendered: list[dict[str, object]] = []
        min_lines_needed = len(result)
        visible_entries = [entry for entry in self._overlay_stack if self._is_overlay_visible(entry)]
        visible_entries.sort(key=lambda entry: int(entry.get("focus_order", 0)))

        for entry in visible_entries:
            component = entry["component"]
            options = entry.get("options") if isinstance(entry.get("options"), dict) else {}
            initial_layout = self._resolve_overlay_layout(options, 0, term_width, term_height)
            overlay_lines = component.render(int(initial_layout["width"]))
            max_height = initial_layout["max_height"]
            if max_height is not None and len(overlay_lines) > max_height:
                overlay_lines = overlay_lines[: int(max_height)]
            layout = self._resolve_overlay_layout(options, len(overlay_lines), term_width, term_height)
            rendered.append({
                "lines": overlay_lines,
                "row": int(layout["row"]),
                "col": int(layout["col"]),
                "width": int(layout["width"]),
            })
            min_lines_needed = max(min_lines_needed, int(layout["row"]) + len(overlay_lines))

        working_height = max(len(result), term_height, min_lines_needed)
        while len(result) < working_height:
            result.append("")
        viewport_start = max(0, working_height - term_height)

        for item in rendered:
            overlay_lines = item["lines"]
            for offset, overlay_line in enumerate(overlay_lines):
                index = viewport_start + int(item["row"]) + offset
                if 0 <= index < len(result):
                    line = overlay_line
                    if visible_width(line) > int(item["width"]):
                        line = slice_by_column(line, 0, int(item["width"]), strict=True)
                    result[index] = self._composite_line_at(
                        result[index],
                        line,
                        int(item["col"]),
                        int(item["width"]),
                        term_width,
                    )

        return result

    @staticmethod
    def _composite_line_at(
        base_line: str,
        overlay_line: str,
        start_col: int,
        overlay_width: int,
        total_width: int,
    ) -> str:
        if is_image_line(base_line):
            return base_line

        after_start = start_col + overlay_width
        base = extract_segments(base_line, start_col, after_start, max(0, total_width - after_start), True)
        overlay = slice_with_width(overlay_line, 0, overlay_width, strict=True)
        before_width = int(base["beforeWidth"])
        overlay_actual_width = int(overlay["width"])
        after_width = int(base["afterWidth"])
        before_pad = max(0, start_col - before_width)
        overlay_pad = max(0, overlay_width - overlay_actual_width)
        actual_before_width = max(start_col, before_width)
        actual_overlay_width = max(overlay_width, overlay_actual_width)
        after_target = max(0, total_width - actual_before_width - actual_overlay_width)
        after_pad = max(0, after_target - after_width)
        result = (
            str(base["before"])
            + (" " * before_pad)
            + _SEGMENT_RESET
            + str(overlay["text"])
            + (" " * overlay_pad)
            + _SEGMENT_RESET
            + str(base["after"])
            + (" " * after_pad)
        )
        if visible_width(result) <= total_width:
            return result
        return slice_by_column(result, 0, total_width, strict=True)


def _terminal_line(line: str) -> str:
    if is_image_line(line):
        return line
    return normalize_terminal_output(line) + _SEGMENT_RESET


def _wants_key_release(component: object) -> bool:
    return bool(getattr(component, "wants_key_release", False) or getattr(component, "wantsKeyRelease", False))


class _OverlayHandle:
    def __init__(self, tui: TUI, entry: dict[str, object]) -> None:
        self._tui = tui
        self._entry = entry

    def hide(self) -> None:
        if self._entry not in self._tui._overlay_stack:
            return
        self._tui._overlay_stack.remove(self._entry)
        component = self._entry["component"]
        if self._tui.focused_component is component:
            top_visible = self._tui._get_topmost_visible_overlay()
            self._tui.set_focus(top_visible["component"] if top_visible is not None else self._entry.get("pre_focus"))
        if not self._tui._overlay_stack:
            self._tui.terminal.hide_cursor()
        self._tui.request_render()

    def set_hidden(self, hidden: bool) -> None:
        hidden = bool(hidden)
        if self._entry.get("hidden") == hidden:
            return
        self._entry["hidden"] = hidden
        component = self._entry["component"]
        options = self._entry.get("options") if isinstance(self._entry.get("options"), dict) else {}
        if hidden:
            if self._tui.focused_component is component:
                top_visible = self._tui._get_topmost_visible_overlay()
                self._tui.set_focus(top_visible["component"] if top_visible is not None else self._entry.get("pre_focus"))
        elif not options.get("nonCapturing") and self._tui._is_overlay_visible(self._entry):
            self._tui._focus_order_counter += 1
            self._entry["focus_order"] = self._tui._focus_order_counter
            self._tui.set_focus(component)
        self._tui.request_render()

    setHidden = set_hidden

    def is_hidden(self) -> bool:
        return bool(self._entry.get("hidden"))

    isHidden = is_hidden

    def focus(self) -> None:
        if self._entry not in self._tui._overlay_stack or not self._tui._is_overlay_visible(self._entry):
            return
        self._tui._focus_order_counter += 1
        self._entry["focus_order"] = self._tui._focus_order_counter
        self._tui.set_focus(self._entry["component"])
        self._tui.request_render()

    def unfocus(self, options: dict[str, object] | None = None) -> None:
        component = self._entry["component"]
        if self._tui.focused_component is not component and options is None:
            return
        target = options.get("target") if isinstance(options, dict) and "target" in options else self._entry.get("pre_focus")
        self._tui.set_focus(target)
        self._tui.request_render()

    def is_focused(self) -> bool:
        return self._tui.focused_component is self._entry["component"]

    isFocused = is_focused
