"""Local pipe and POSIX PTY transports through the declared backend."""

from __future__ import annotations

import errno
import os
import signal
import struct
import subprocess
import threading
from pathlib import Path

from travis.coding_agent.execution_backend import ExecutionBackend
from travis.coding_agent.processes.containment import ProcessTreeController
from travis.coding_agent.processes.transport import ProcessTransport, SignalName
from travis.coding_agent.processes.types import ProcessLaunchRequest, ProcessStateError


class _PipeTransport(ProcessTransport):
    tty = False

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self._stdin_closed = False
        self._tree = ProcessTreeController(process.pid)

    def read_sources(self):
        return tuple(source for source in (self._process.stdout, self._process.stderr) if source is not None)

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def write(self, data: bytes) -> int:
        with self._write_lock:
            if self._stdin_closed or self._process.stdin is None:
                raise BrokenPipeError("Process stdin is closed")
            written = self._process.stdin.write(data)
            self._process.stdin.flush()
            return int(written or 0)

    def close_stdin(self) -> None:
        with self._write_lock:
            if self._stdin_closed:
                return
            self._stdin_closed = True
            if self._process.stdin is not None:
                self._process.stdin.close()

    def resize(self, rows: int, cols: int) -> None:
        raise ProcessStateError("resize requires tty=true")

    def signal_group(self, signal_name: SignalName) -> None:
        _signal_process_group(self._process, signal_name)

    def refresh_tree(self) -> None:
        self._tree.refresh()

    def signal_tree(self, signal_name: SignalName) -> None:
        _signal_process_group(self._process, signal_name)
        self._tree.signal(signal_name)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self.close_stdin()
        for stream in (self._process.stdout, self._process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


class _PTYEndpoint:
    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._closed = False
        self._lock = threading.RLock()

    def read(self, size: int = 4096) -> bytes:
        with self._lock:
            if self._closed:
                return b""
            fd = self._fd
        try:
            return os.read(fd, size)
        except OSError as error:
            if error.errno in {errno.EIO, errno.EBADF}:
                return b""
            raise

    def write(self, data: bytes) -> int:
        with self._lock:
            if self._closed:
                raise BrokenPipeError("PTY is closed")
            return os.write(self._fd, data)

    def resize(self, rows: int, cols: int) -> None:
        import fcntl
        import termios

        with self._lock:
            if self._closed:
                raise ProcessStateError("PTY is closed")
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fd = self._fd
        try:
            os.close(fd)
        except OSError:
            pass


class _PTYTransport(ProcessTransport):
    tty = True

    def __init__(self, process: subprocess.Popen, endpoint: _PTYEndpoint) -> None:
        self._process = process
        self._endpoint = endpoint
        self._stdin_closed = False
        self._close_lock = threading.Lock()
        self._closed = False
        self._tree = ProcessTreeController(process.pid)

    def read_sources(self):
        return (self._endpoint,)

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def write(self, data: bytes) -> int:
        if self._stdin_closed:
            raise BrokenPipeError("PTY stdin is closed")
        return self._endpoint.write(data)

    def close_stdin(self) -> None:
        if self._stdin_closed:
            return
        self._stdin_closed = True
        try:
            self._endpoint.write(b"\x04")
        except (BrokenPipeError, OSError):
            pass

    def resize(self, rows: int, cols: int) -> None:
        self._endpoint.resize(rows, cols)

    def signal_group(self, signal_name: SignalName) -> None:
        _signal_process_group(self._process, signal_name)

    def refresh_tree(self) -> None:
        self._tree.refresh()

    def signal_tree(self, signal_name: SignalName) -> None:
        _signal_process_group(self._process, signal_name)
        self._tree.signal(signal_name)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._endpoint.close()


def create_local_process_transport(
    request: ProcessLaunchRequest,
    backend: ExecutionBackend,
) -> ProcessTransport:
    if request.tty and os.name != "posix":
        raise RuntimeError("PTY mode requires POSIX")
    cwd = Path(request.cwd)
    if not cwd.exists():
        raise RuntimeError(f"Working directory does not exist: {request.cwd}")
    if not Path(request.shell_path).exists():
        raise RuntimeError(f"Shell path not found: {request.shell_path}")
    if request.tty:
        return _create_pty_transport(request, backend)
    process = backend.spawn(
        request.command,
        request.cwd,
        request.env,
        {
            "shell_path": request.shell_path,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "start_new_session": os.name == "posix",
        },
    )
    return _PipeTransport(process)


def _create_pty_transport(request: ProcessLaunchRequest, backend: ExecutionBackend) -> ProcessTransport:
    import fcntl
    import pty
    import termios

    master_fd, slave_fd = pty.openpty()
    try:
        winsize = struct.pack("HHHH", request.rows, request.cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        process = backend.spawn(
            request.command,
            request.cwd,
            request.env,
            {
                "shell_path": request.shell_path,
                "stdin": slave_fd,
                "stdout": slave_fd,
                "stderr": slave_fd,
                "start_new_session": True,
            },
        )
    except BaseException:
        os.close(master_fd)
        os.close(slave_fd)
        raise
    os.close(slave_fd)
    return _PTYTransport(process, _PTYEndpoint(master_fd))


def _signal_process_group(process: subprocess.Popen, signal_name: SignalName) -> None:
    if os.name == "posix":
        selected = {
            "interrupt": signal.SIGINT,
            "terminate": signal.SIGTERM,
            "kill": signal.SIGKILL,
        }[signal_name]
        try:
            os.killpg(process.pid, selected)
        except ProcessLookupError:
            return
        return
    if process.poll() is not None:
        return
    if signal_name == "interrupt" and hasattr(signal, "CTRL_BREAK_EVENT"):
        process.send_signal(signal.CTRL_BREAK_EVENT)
    elif signal_name == "terminate":
        process.terminate()
    else:
        process.kill()


__all__ = [
    "create_local_process_transport",
]
