"""Terminal abstraction."""

from __future__ import annotations

import codecs
from collections.abc import Callable
import os
import select
import sys
import termios
import threading
import time
import tty
from typing import Protocol

from travis.tui.stdin_buffer import StdinBuffer

_BRACKETED_PASTE_ENABLE = "\x1b[?2004h"
_BRACKETED_PASTE_DISABLE = "\x1b[?2004l"
_MOUSE_TRACKING_ENABLE = "\x1b[?1000h\x1b[?1006h"
_MOUSE_TRACKING_DISABLE = "\x1b[?1006l\x1b[?1000l"
_PROGRESS_ACTIVE = "\x1b]9;4;3\x07"
_PROGRESS_CLEAR = "\x1b]9;4;0;\x07"


def _move_terminal(write: Callable[[str], None], lines: int) -> None:
    if lines > 0:
        write(f"\x1b[{lines}B")
    elif lines < 0:
        write(f"\x1b[{-lines}A")


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_flag_disabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"0", "false", "no", "off"}


def _mouse_tracking_enabled() -> bool:
    if "TRAVIS234_TUI_MOUSE" in os.environ:
        return _env_flag_enabled("TRAVIS234_TUI_MOUSE") and not _env_flag_disabled("TRAVIS234_TUI_MOUSE")
    return _env_flag_enabled("TRAVIS234_SANDBOX")


class Terminal(Protocol):
    columns: int
    rows: int

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None: ...

    def stop(self) -> None: ...

    def drain_input(self, max_ms: int = 1000, idle_ms: int = 50) -> None: ...

    def write(self, data: str) -> None: ...

    def move_by(self, lines: int) -> None: ...

    def hide_cursor(self) -> None: ...

    def show_cursor(self) -> None: ...

    def clear_line(self) -> None: ...

    def clear_from_cursor(self) -> None: ...

    def clear_screen(self) -> None: ...

    def set_title(self, title: str) -> None: ...

    def set_progress(self, active: bool) -> None: ...


class FakeTerminal:
    """Records writes for tests."""

    def __init__(self, columns: int = 80, rows: int = 24) -> None:
        self.columns = columns
        self.rows = rows
        self.writes: list[str] = []
        self._progress_active = False
        self.input_handler: Callable[[str], None] | None = None
        self.resize_handler: Callable[[], None] | None = None

    def write(self, data: str) -> None:
        self.writes.append(data)

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:
        self.input_handler = on_input
        self.resize_handler = on_resize
        self.write(_BRACKETED_PASTE_ENABLE)

    def stop(self) -> None:
        if self._progress_active:
            self._progress_active = False
            self.write(_PROGRESS_CLEAR)
        self.write(_BRACKETED_PASTE_DISABLE)

    def drain_input(self, max_ms: int = 1000, idle_ms: int = 50) -> None:
        return None


    def move_by(self, lines: int) -> None:
        _move_terminal(self.write, lines)


    def set_title(self, title: str) -> None:
        self.write(f"\x1b]0;{title}\x07")


    def set_progress(self, active: bool) -> None:
        self._progress_active = active
        self.write(_PROGRESS_ACTIVE if active else _PROGRESS_CLEAR)


    def hide_cursor(self) -> None:
        self.write("\x1b[?25l")


    def show_cursor(self) -> None:
        self.write("\x1b[?25h")


    def clear_line(self) -> None:
        self.write("\x1b[K")


    def clear_from_cursor(self) -> None:
        self.write("\x1b[J")


    def clear_screen(self) -> None:
        self.write("\x1b[2J\x1b[H")


    @property
    def output(self) -> str:
        return "".join(self.writes)


class ProcessTerminal:
    """Real stdout-backed terminal."""

    def __init__(self, progress_keepalive_seconds: float = 1.0) -> None:
        size = _terminal_size()
        self.columns = size[0]
        self.rows = size[1]
        self._progress_keepalive_seconds = progress_keepalive_seconds
        self._progress_active = False
        self._progress_timer: threading.Timer | None = None
        self._progress_lock = threading.RLock()
        self.input_handler: Callable[[str], None] | None = None
        self.resize_handler: Callable[[], None] | None = None
        self._stdin_buffer: StdinBuffer | None = None
        self._stdin_thread: threading.Thread | None = None
        self._stdin_stop = threading.Event()
        self._stdin_fd: int | None = None
        self._saved_termios: list | None = None
        self._stdin_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._draining_input = False
        self._mouse_tracking_enabled = False

    def write(self, data: str) -> None:  # pragma: no cover - real IO
        sys.stdout.write(data)
        sys.stdout.flush()

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:  # pragma: no cover - real IO
        self.input_handler = on_input
        self.resize_handler = on_resize
        self._start_raw_stdin()
        self.write(_BRACKETED_PASTE_ENABLE)
        self._mouse_tracking_enabled = _mouse_tracking_enabled()
        if self._mouse_tracking_enabled:
            self.write(_MOUSE_TRACKING_ENABLE)

    def stop(self) -> None:  # pragma: no cover - real IO
        self._stop_raw_stdin()
        with self._progress_lock:
            progress_was_active = self._progress_active or self._progress_timer is not None
            self._progress_active = False
            self._clear_progress_timer_locked()
        if progress_was_active:
            self.write(_PROGRESS_CLEAR)
        if self._mouse_tracking_enabled:
            self.write(_MOUSE_TRACKING_DISABLE)
            self._mouse_tracking_enabled = False
        self.write(_BRACKETED_PASTE_DISABLE)

    def _start_raw_stdin(self) -> None:  # pragma: no cover - real IO
        if self._stdin_thread is not None:
            return
        if not sys.stdin.isatty():
            return
        self._stdin_fd = sys.stdin.fileno()
        try:
            self._saved_termios = termios.tcgetattr(self._stdin_fd)
            tty.setraw(self._stdin_fd)
            self._enable_interrupt_signal()
        except Exception:
            self._saved_termios = None
            self._stdin_fd = None
            return

        self._stdin_buffer = StdinBuffer({"timeout": 10})
        self._stdin_buffer.on("data", self._forward_input_sequence)
        self._stdin_buffer.on("paste", self._forward_paste)
        self._reset_stdin_decoder()
        self._stdin_stop.clear()
        self._stdin_thread = threading.Thread(target=self._read_stdin_loop, daemon=True)
        self._stdin_thread.start()

    def _stop_raw_stdin(self) -> None:  # pragma: no cover - real IO
        self._stdin_stop.set()
        if self._stdin_thread is not None:
            self._stdin_thread.join(timeout=0.2)
            self._stdin_thread = None
        if self._stdin_buffer is not None:
            self._stdin_buffer.destroy()
            self._stdin_buffer = None
        if self._stdin_fd is not None and self._saved_termios is not None:
            try:
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._saved_termios)
            except Exception:
                pass
        self._stdin_fd = None
        self._saved_termios = None
        self._reset_stdin_decoder()

    def _enable_interrupt_signal(self) -> None:  # pragma: no cover - real IO
        if self._stdin_fd is None:
            return
        try:
            attrs = termios.tcgetattr(self._stdin_fd)
            attrs[3] |= termios.ISIG
            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, attrs)
        except Exception:
            pass

    def _read_stdin_loop(self) -> None:  # pragma: no cover - real IO
        fd = self._stdin_fd
        if fd is None:
            return
        while not self._stdin_stop.is_set():
            try:
                readable, _writable, _errors = select.select([fd], [], [], 0.05)
            except Exception:
                return
            if not readable:
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                return
            if not data:
                continue
            if self._draining_input:
                continue
            self._process_stdin_bytes(data)

    def drain_input(self, max_ms: int = 1000, idle_ms: int = 50) -> None:  # pragma: no cover - timing exercised in tests
        previous_handler = self.input_handler
        self.input_handler = None
        self._draining_input = True
        fd = self._stdin_fd
        if fd is None:
            self.input_handler = previous_handler
            self._draining_input = False
            return

        end_time = time.monotonic() + max(0, max_ms) / 1000.0
        idle_seconds = max(0, idle_ms) / 1000.0
        last_data_time = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                if now >= end_time:
                    break
                if now - last_data_time >= idle_seconds:
                    break
                timeout = min(idle_seconds - (now - last_data_time), end_time - now)
                if timeout <= 0:
                    break
                try:
                    readable, _writable, _errors = select.select([fd], [], [], timeout)
                except Exception:
                    break
                if not readable:
                    continue
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                last_data_time = time.monotonic()
        finally:
            self._draining_input = False
            self.input_handler = previous_handler


    def _process_stdin_bytes(self, data: bytes) -> None:
        text = self._stdin_decoder.decode(data, final=False)
        if not text:
            return
        buffer = self._stdin_buffer
        if buffer is not None:
            buffer.process(text)

    def _reset_stdin_decoder(self) -> None:
        self._stdin_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def _forward_input_sequence(self, sequence: str) -> None:  # pragma: no cover - real IO
        if self.input_handler is not None:
            self.input_handler(sequence)

    def _forward_paste(self, content: str) -> None:  # pragma: no cover - real IO
        if self.input_handler is not None:
            self.input_handler(f"\x1b[200~{content}\x1b[201~")

    def move_by(self, lines: int) -> None:  # pragma: no cover - real IO
        _move_terminal(self.write, lines)


    def set_title(self, title: str) -> None:  # pragma: no cover - real IO
        self.write(f"\x1b]0;{title}\x07")


    def set_progress(self, active: bool) -> None:  # pragma: no cover - real IO
        if active:
            self.write(_PROGRESS_ACTIVE)
            with self._progress_lock:
                self._progress_active = True
                if self._progress_timer is None:
                    self._schedule_progress_keepalive_locked()
            return

        with self._progress_lock:
            self._progress_active = False
            self._clear_progress_timer_locked()
        self.write(_PROGRESS_CLEAR)


    def _schedule_progress_keepalive_locked(self) -> None:
        if not self._progress_active or self._progress_keepalive_seconds <= 0:
            return
        timer = threading.Timer(self._progress_keepalive_seconds, self._emit_progress_keepalive)
        timer.daemon = True
        self._progress_timer = timer
        timer.start()

    def _emit_progress_keepalive(self) -> None:
        with self._progress_lock:
            self._progress_timer = None
            if not self._progress_active:
                return
            self.write(_PROGRESS_ACTIVE)
            self._schedule_progress_keepalive_locked()

    def _clear_progress_timer_locked(self) -> None:
        if self._progress_timer is None:
            return
        self._progress_timer.cancel()
        self._progress_timer = None

    def hide_cursor(self) -> None:  # pragma: no cover - real IO
        self.write("\x1b[?25l")


    def show_cursor(self) -> None:  # pragma: no cover - real IO
        self.write("\x1b[?25h")


    def clear_line(self) -> None:  # pragma: no cover - real IO
        self.write("\x1b[K")


    def clear_from_cursor(self) -> None:  # pragma: no cover - real IO
        self.write("\x1b[J")


    def clear_screen(self) -> None:  # pragma: no cover - real IO
        self.write("\x1b[2J\x1b[H")



def _terminal_size() -> tuple[int, int]:
    try:
        import shutil

        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines
    except Exception:  # pragma: no cover
        return 80, 24
