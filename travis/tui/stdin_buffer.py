"""Buffered stdin sequence parser ported from Travis TUI."""

from __future__ import annotations

from collections import defaultdict
import re
import threading
from typing import Callable


ESC = "\x1b"
BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"


def _is_complete_sequence(data: str) -> str:
    if not data.startswith(ESC):
        return "not-escape"
    if len(data) == 1:
        return "incomplete"

    after_esc = data[1:]
    if after_esc.startswith("["):
        if after_esc.startswith("[M"):
            return "complete" if len(data) >= 6 else "incomplete"
        return _is_complete_csi_sequence(data)
    if after_esc.startswith("]"):
        return _is_complete_osc_sequence(data)
    if after_esc.startswith("P"):
        return _is_complete_dcs_sequence(data)
    if after_esc.startswith("_"):
        return _is_complete_apc_sequence(data)
    if after_esc.startswith("O"):
        return "complete" if len(after_esc) >= 2 else "incomplete"
    if len(after_esc) == 1:
        return "complete"
    return "complete"


def _is_complete_csi_sequence(data: str) -> str:
    if not data.startswith(f"{ESC}["):
        return "complete"
    if len(data) < 3:
        return "incomplete"

    payload = data[2:]
    last_char = payload[-1]
    last_code = ord(last_char)
    if 0x40 <= last_code <= 0x7E:
        if payload.startswith("<"):
            if re.match(r"^<\d+;\d+;\d+[Mm]$", payload):
                return "complete"
            if last_char in {"M", "m"}:
                parts = payload[1:-1].split(";")
                if len(parts) == 3 and all(re.match(r"^\d+$", part) for part in parts):
                    return "complete"
            return "incomplete"
        return "complete"
    return "incomplete"


def _is_complete_osc_sequence(data: str) -> str:
    if not data.startswith(f"{ESC}]"):
        return "complete"
    return "complete" if data.endswith(f"{ESC}\\") or data.endswith("\x07") else "incomplete"


def _is_complete_dcs_sequence(data: str) -> str:
    if not data.startswith(f"{ESC}P"):
        return "complete"
    return "complete" if data.endswith(f"{ESC}\\") else "incomplete"


def _is_complete_apc_sequence(data: str) -> str:
    if not data.startswith(f"{ESC}_"):
        return "complete"
    return "complete" if data.endswith(f"{ESC}\\") else "incomplete"


def _parse_unmodified_kitty_printable_codepoint(sequence: str) -> int | None:
    match = re.match(r"^\x1b\[(\d+)(?::\d*)?(?::\d+)?u$", sequence)
    if not match:
        return None
    codepoint = int(match.group(1))
    return codepoint if codepoint >= 32 else None


def _extract_complete_sequences(buffer: str) -> tuple[list[str], str]:
    sequences: list[str] = []
    pos = 0

    while pos < len(buffer):
        remaining = buffer[pos:]
        if remaining.startswith(ESC):
            seq_end = 1
            while seq_end <= len(remaining):
                candidate = remaining[:seq_end]
                status = _is_complete_sequence(candidate)
                if status == "complete":
                    if candidate == "\x1b\x1b":
                        next_char = remaining[seq_end] if seq_end < len(remaining) else ""
                        if next_char in {"[", "]", "O", "P", "_"}:
                            sequences.append(ESC)
                            pos += 1
                            break
                    sequences.append(candidate)
                    pos += seq_end
                    break
                if status == "incomplete":
                    seq_end += 1
                    continue
                sequences.append(candidate)
                pos += seq_end
                break

            if seq_end > len(remaining):
                return sequences, remaining
        else:
            sequences.append(remaining[0])
            pos += 1

    return sequences, ""


class StdinBuffer:
    def __init__(self, options: dict[str, object] | None = None, *, timeout: int | float | None = None) -> None:
        option_timeout = options.get("timeout") if options else None
        timeout_ms = timeout if timeout is not None else option_timeout
        self.timeout_ms = float(timeout_ms if timeout_ms is not None else 10)
        self._buffer = ""
        self._timer: threading.Timer | None = None
        self._paste_mode = False
        self._paste_buffer = ""
        self._pending_kitty_printable_codepoint: int | None = None
        self._listeners: dict[str, list[Callable[[str], object]]] = defaultdict(list)
        self._lock = threading.RLock()

    def on(self, event: str, callback: Callable[[str], object]) -> Callable[[], None]:
        self._listeners[event].append(callback)

        def unsubscribe() -> None:
            try:
                self._listeners[event].remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def process(self, data: str | bytes | bytearray) -> None:
        with self._lock:
            self._clear_timer_locked()
            incoming = self._normalize_input(data)
            if len(incoming) == 0 and len(self._buffer) == 0:
                self._emit_data_sequence("")
                return

            self._buffer += incoming
            if self._paste_mode:
                self._paste_buffer += self._buffer
                self._buffer = ""
                self._emit_paste_if_complete_locked()
                return

            start_index = self._buffer.find(BRACKETED_PASTE_START)
            if start_index != -1:
                if start_index > 0:
                    before_paste = self._buffer[:start_index]
                    sequences, _remainder = _extract_complete_sequences(before_paste)
                    for sequence in sequences:
                        self._emit_data_sequence(sequence)

                self._pending_kitty_printable_codepoint = None
                self._buffer = self._buffer[start_index + len(BRACKETED_PASTE_START):]
                self._paste_mode = True
                self._paste_buffer = self._buffer
                self._buffer = ""
                self._emit_paste_if_complete_locked()
                return

            sequences, remainder = _extract_complete_sequences(self._buffer)
            self._buffer = remainder
            for sequence in sequences:
                self._emit_data_sequence(sequence)

            if self._buffer:
                self._timer = threading.Timer(self.timeout_ms / 1000.0, self._flush_timeout)
                self._timer.daemon = True
                self._timer.start()

    def flush(self) -> list[str]:
        with self._lock:
            self._clear_timer_locked()
            if not self._buffer:
                return []
            sequences = [self._buffer]
            self._buffer = ""
            self._pending_kitty_printable_codepoint = None
            return sequences

    def clear(self) -> None:
        with self._lock:
            self._clear_timer_locked()
            self._buffer = ""
            self._paste_mode = False
            self._paste_buffer = ""
            self._pending_kitty_printable_codepoint = None

    def destroy(self) -> None:
        self.clear()

    def get_buffer(self) -> str:
        return self._buffer

    getBuffer = get_buffer

    def _normalize_input(self, data: str | bytes | bytearray) -> str:
        if isinstance(data, str):
            return data
        raw = bytes(data)
        if len(raw) == 1 and raw[0] > 127:
            return f"{ESC}{chr(raw[0] - 128)}"
        return raw.decode()

    def _emit_data_sequence(self, sequence: str) -> None:
        raw_codepoint = ord(sequence) if len(sequence) == 1 else None
        if raw_codepoint is not None and raw_codepoint == self._pending_kitty_printable_codepoint:
            self._pending_kitty_printable_codepoint = None
            return
        self._pending_kitty_printable_codepoint = _parse_unmodified_kitty_printable_codepoint(sequence)
        self._emit("data", sequence)

    def _emit(self, event: str, value: str) -> None:
        for callback in list(self._listeners.get(event, [])):
            callback(value)

    def _emit_paste_if_complete_locked(self) -> None:
        end_index = self._paste_buffer.find(BRACKETED_PASTE_END)
        if end_index == -1:
            return
        pasted_content = self._paste_buffer[:end_index]
        remaining = self._paste_buffer[end_index + len(BRACKETED_PASTE_END):]
        self._paste_mode = False
        self._paste_buffer = ""
        self._pending_kitty_printable_codepoint = None
        self._emit("paste", pasted_content)
        if remaining:
            self.process(remaining)

    def _flush_timeout(self) -> None:
        flushed = self.flush()
        for sequence in flushed:
            self._emit_data_sequence(sequence)

    def _clear_timer_locked(self) -> None:
        if self._timer is None:
            return
        self._timer.cancel()
        self._timer = None
