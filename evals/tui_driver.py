from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import Sequence

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_TRANSCRIPT_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.IGNORECASE)


class TuiDriver:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        master_fd: int,
        trace_path: Path,
        transcript_path: Path | None = None,
    ) -> None:
        self.process = process
        self.master_fd = master_fd
        self.trace_path = trace_path
        self.transcript_path = transcript_path
        if self.transcript_path is not None:
            self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.transcript_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(descriptor)
            os.chmod(self.transcript_path, 0o600)
        self._trace_offset = 0
        self._events: list[dict[str, object]] = []
        self._tail = ""

    @classmethod
    def start(cls, command: Sequence[str], cwd: str | Path, trace_path: str | Path) -> "TuiDriver":
        master_fd, slave_fd = pty.openpty()
        environment = os.environ.copy()
        if environment.get("PYTHONPATH"):
            launch_cwd = Path.cwd()
            environment["PYTHONPATH"] = os.pathsep.join(
                str((launch_cwd / item).resolve()) if not Path(item).is_absolute() else item
                for item in environment["PYTHONPATH"].split(os.pathsep)
                if item
            )
        try:
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                env=environment,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)
        trace = Path(trace_path)
        return cls(process, master_fd, trace, trace.with_name("terminal.log"))

    @property
    def diagnostic_tail(self) -> str:
        return self._tail[-8000:]

    def send_line(self, text: str) -> None:
        os.write(self.master_fd, text.encode("utf-8") + b"\r")

    def send_key(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def select_model(self, query: str, index: int, timeout: float) -> dict[str, object]:
        self.send_line(f"/model {query}")
        ready = self.wait_for_event("model_picker_ready", timeout)
        model_count = int(ready.get("model_count") or 0)
        if index <= 0 or index > model_count:
            raise RuntimeError(f"model picker index {index} unavailable for {model_count} rows")
        self.send_line(str(index))
        return self.wait_for_event("model_selected", timeout)

    def wait_for_event(self, event_type: str, timeout: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._drain_output()
            self._read_trace()
            for index, event in enumerate(self._events):
                if event.get("event") == event_type:
                    return self._events.pop(index)
                if event.get("event") == "fatal":
                    fatal = self._events.pop(index)
                    code = str(fatal.get("error_code") or "fatal")
                    raise RuntimeError(f"TUI reported fatal event before {event_type}: {code}")
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"TUI exited with {self.process.returncode} before {event_type}; tail={self.diagnostic_tail!r}"
                )
            time.sleep(0.02)
        raise TimeoutError(f"timed out waiting for {event_type}; tail={self.diagnostic_tail!r}")

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send_line("/exit")
                self.process.wait(timeout=3)
            except Exception:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=2)
        self._drain_output()
        try:
            os.close(self.master_fd)
        except OSError:
            pass

    def _read_trace(self) -> None:
        if not self.trace_path.exists():
            return
        with self.trace_path.open("r", encoding="utf-8") as handle:
            handle.seek(self._trace_offset)
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    self._events.append(event)
            self._trace_offset = handle.tell()

    def _drain_output(self) -> None:
        while True:
            readable, _, _ = select.select([self.master_fd], [], [], 0)
            if not readable:
                return
            try:
                chunk = os.read(self.master_fd, 65536)
            except OSError:
                return
            if not chunk:
                return
            text = _ANSI.sub("", chunk.decode("utf-8", errors="replace"))
            self._tail = (self._tail + text)[-8000:]
            if self.transcript_path is not None:
                safe_text = _TRANSCRIPT_SECRET.sub("[REDACTED]", text)
                with self.transcript_path.open("a", encoding="utf-8") as handle:
                    handle.write(safe_text)


__all__ = ["TuiDriver"]
