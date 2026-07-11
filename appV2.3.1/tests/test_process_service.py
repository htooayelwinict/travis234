from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

import pytest

from appv231.coding_agent.processes.service import ProcessSessionService
from appv231.coding_agent.processes.types import (
    ProcessInputLimitError,
    ProcessLaunchRequest,
    ProcessLimitError,
    ProcessNotFoundError,
    ProcessOwner,
    ProcessState,
    ProcessStateError,
)


class Signal:
    def __init__(self, aborted: bool = False) -> None:
        self.aborted = aborted

    def abort(self) -> None:
        self.aborted = True


class BlockingReader:
    def __init__(self) -> None:
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = bytearray()
        self._closed = False

    def feed(self, data: bytes) -> None:
        if not self._closed:
            self._queue.put(data)

    def finish(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put(None)

    def read(self, size: int = -1) -> bytes:
        while not self._buffer:
            item = self._queue.get(timeout=2)
            if item is None:
                return b""
            self._buffer.extend(item)
        if size < 0 or size >= len(self._buffer):
            data = bytes(self._buffer)
            self._buffer.clear()
            return data
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def close(self) -> None:
        self.finish()


class FakeProcessTransport:
    def __init__(
        self,
        *,
        tty: bool = False,
        initial_output: bytes = b"",
        initial_exit_code: int | None = None,
        close_output_on_exit: bool = True,
        exit_on_signals: set[str] | None = None,
        write_gate: threading.Event | None = None,
    ) -> None:
        self.tty = tty
        self.reader = BlockingReader()
        self.writes: list[bytes] = []
        self.signals: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self.stdin_closed = False
        self.closed = False
        self._return_code: int | None = None
        self._exit_event = threading.Event()
        self._close_output_on_exit = close_output_on_exit
        self._exit_on_signals = {"kill"} if exit_on_signals is None else exit_on_signals
        self._write_gate = write_gate
        self.write_started = threading.Event()
        self._lock = threading.Lock()
        if initial_output:
            self.reader.feed(initial_output)
        if initial_exit_code is not None:
            self.exit(initial_exit_code)

    def read_sources(self):
        return (self.reader,)

    def poll(self) -> int | None:
        with self._lock:
            return self._return_code

    def wait(self, timeout: float | None = None) -> int:
        if not self._exit_event.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        assert self._return_code is not None
        return self._return_code

    def write(self, data: bytes) -> int:
        if self.stdin_closed:
            raise BrokenPipeError("stdin closed")
        self.write_started.set()
        if self._write_gate is not None and not self._write_gate.wait(2):
            raise TimeoutError("fake write gate timed out")
        self.writes.append(bytes(data))
        return len(data)

    def close_stdin(self) -> None:
        self.stdin_closed = True

    def resize(self, rows: int, cols: int) -> None:
        if not self.tty:
            raise ProcessStateError("resize requires tty=true")
        self.resizes.append((rows, cols))

    def signal_group(self, signal_name: str) -> None:
        self.signals.append(signal_name)
        if signal_name in self._exit_on_signals:
            self.exit(-9 if signal_name == "kill" else -15)

    def close(self) -> None:
        self.closed = True
        self.reader.close()

    def emit(self, data: bytes) -> None:
        self.reader.feed(data)

    def finish_output(self) -> None:
        self.reader.finish()

    def exit(self, code: int, *, close_output: bool | None = None) -> None:
        with self._lock:
            if self._return_code is not None:
                return
            self._return_code = code
            self._exit_event.set()
        if self._close_output_on_exit if close_output is None else close_output:
            self.reader.finish()


class Factory:
    def __init__(self, *transports: FakeProcessTransport) -> None:
        self._transports = queue.Queue()
        for transport in transports:
            self._transports.put(transport)
        self.created: list[FakeProcessTransport] = []

    def __call__(self, _request: ProcessLaunchRequest) -> FakeProcessTransport:
        transport = self._transports.get_nowait()
        self.created.append(transport)
        return transport


def request(command: str = "long", *, timeout: float | None = None, tty: bool = False) -> ProcessLaunchRequest:
    return ProcessLaunchRequest(
        command=command,
        cwd="/workspace",
        env={},
        shell_path="/bin/bash",
        tty=tty,
        timeout_seconds=timeout,
    )


def eventually(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def owner() -> ProcessOwner:
    return ProcessOwner("app-1", "/workspace", "agent")


@pytest.fixture
def service(tmp_path: Path):
    value = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.02,
        drain_timeout_seconds=0.1,
    )
    yield value
    value.close()


def test_start_returns_completed_output_when_process_exits_before_yield(service, owner) -> None:
    transport = FakeProcessTransport(initial_output=b"done\n", initial_exit_code=0)

    snapshot = service.start(owner, request("short"), Factory(transport), yield_time_ms=1_000)

    assert snapshot.state is ProcessState.EXITED
    assert snapshot.output == "done\n"
    assert snapshot.exit_code == 0
    assert snapshot.cursor == 0
    assert snapshot.next_cursor == 5


def test_start_yields_running_handle_without_stopping_process(service, owner) -> None:
    transport = FakeProcessTransport(initial_output=b"ready\n")

    snapshot = service.start(owner, request(), Factory(transport), yield_time_ms=0)

    assert snapshot.state is ProcessState.RUNNING
    assert snapshot.session_id.startswith("proc_")
    assert len(snapshot.session_id) == len("proc_") + 32
    assert snapshot.output == "ready\n"
    assert transport.signals == []


def test_abort_before_handoff_terminates_and_returns_terminal_snapshot(service, owner) -> None:
    signal = Signal()
    transport = FakeProcessTransport(exit_on_signals={"kill"})

    def abort_soon() -> None:
        time.sleep(0.01)
        signal.abort()

    threading.Thread(target=abort_soon).start()
    snapshot = service.start(owner, request(), Factory(transport), yield_time_ms=1_000, signal=signal)

    assert snapshot.state is ProcessState.TERMINATED
    assert transport.signals == ["kill"]


def test_abort_after_atomic_handoff_does_not_kill_job(service, owner) -> None:
    signal = Signal()
    transport = FakeProcessTransport()
    snapshot = service.start(owner, request(), Factory(transport), yield_time_ms=0, signal=signal)

    signal.abort()

    assert snapshot.state is ProcessState.RUNNING
    assert transport.signals == []
    assert service.poll(owner, snapshot.session_id, 0, wait_ms=0).state is ProcessState.RUNNING


def test_timeout_before_handoff_returns_timed_out_instead_of_detaching(service, owner) -> None:
    transport = FakeProcessTransport(exit_on_signals={"kill"})

    snapshot = service.start(owner, request(timeout=0.01), Factory(transport), yield_time_ms=1_000)

    assert snapshot.state is ProcessState.TIMED_OUT
    assert transport.signals == ["terminate", "kill"]


def test_natural_exit_wins_when_it_happens_before_timeout(service, owner) -> None:
    transport = FakeProcessTransport()
    snapshot = service.start(owner, request(timeout=1), Factory(transport), yield_time_ms=0)

    transport.exit(7)
    terminal = service.poll(owner, snapshot.session_id, 0, wait_ms=1_000)

    assert terminal.state is ProcessState.EXITED
    assert terminal.exit_code == 7
    assert transport.signals == []


def test_terminal_state_is_published_only_after_final_output_drains(service, owner) -> None:
    transport = FakeProcessTransport(close_output_on_exit=False)
    snapshot = service.start(owner, request(), Factory(transport), yield_time_ms=0)

    transport.exit(0, close_output=False)
    eventually(lambda: service.list(owner)[0].state is ProcessState.DRAINING)
    transport.emit(b"last byte\n")
    transport.finish_output()
    drained = service.poll(owner, snapshot.session_id, 0, wait_ms=1_000)
    terminal = service.poll(owner, snapshot.session_id, drained.next_cursor, wait_ms=1_000)

    assert drained.output == "last byte\n"
    assert terminal.state is ProcessState.EXITED
    assert terminal.output == ""


def test_poll_is_cursor_deterministic_and_waits_for_new_output(service, owner) -> None:
    transport = FakeProcessTransport()
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)

    def emit_soon() -> None:
        time.sleep(0.02)
        transport.emit(b"next\n")

    threading.Thread(target=emit_soon).start()
    update = service.poll(owner, started.session_id, 0, wait_ms=500)

    assert update.output == "next\n"
    assert service.poll(owner, started.session_id, 0, wait_ms=0).output == "next\n"


def test_write_is_ordered_and_eof_closes_after_payload(service, owner) -> None:
    transport = FakeProcessTransport()
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)

    service.write(owner, started.session_id, "first", wait_ms=0)
    service.write(owner, started.session_id, "second", eof=True, wait_ms=0)

    eventually(lambda: transport.stdin_closed)
    assert transport.writes == [b"first", b"second"]


def test_write_enforces_per_call_and_pending_limits(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        max_input_bytes=4,
        max_pending_input_bytes=4,
        termination_grace_seconds=0.01,
    )
    transport = FakeProcessTransport()
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)
    try:
        with pytest.raises(ProcessInputLimitError, match="at most 4 bytes"):
            service.write(owner, started.session_id, "12345", wait_ms=0)
    finally:
        service.close()


def test_write_enforces_total_pending_limit_while_child_blocks(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        max_input_bytes=4,
        max_pending_input_bytes=4,
        termination_grace_seconds=0.01,
    )
    gate = threading.Event()
    transport = FakeProcessTransport(write_gate=gate)
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)
    try:
        service.write(owner, started.session_id, "1234", wait_ms=0)
        assert transport.write_started.wait(1)
        with pytest.raises(ProcessInputLimitError, match="pending input accepts at most 4 bytes"):
            service.write(owner, started.session_id, "x", wait_ms=0)
    finally:
        gate.set()
        service.close()


def test_resize_requires_tty_and_records_dimensions(service, owner) -> None:
    pipe = FakeProcessTransport()
    pipe_job = service.start(owner, request(), Factory(pipe), yield_time_ms=0)
    with pytest.raises(ProcessStateError, match="tty=true"):
        service.resize(owner, pipe_job.session_id, rows=40, cols=120)

    tty = FakeProcessTransport(tty=True)
    tty_job = service.start(owner, request(tty=True), Factory(tty), yield_time_ms=0)
    service.resize(owner, tty_job.session_id, rows=40, cols=120)
    assert tty.resizes == [(40, 120)]


def test_owner_mismatch_is_indistinguishable_from_unknown_handle(service, owner) -> None:
    transport = FakeProcessTransport()
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)

    for wrong_owner in (
        ProcessOwner("app-1", "/other", "agent"),
        ProcessOwner("app-1", "/workspace", "user"),
    ):
        with pytest.raises(ProcessNotFoundError, match=f"Process not found: {started.session_id}"):
            service.poll(wrong_owner, started.session_id, 0, wait_ms=0)


def test_active_process_limit_does_not_evict_running_job(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        max_active=1,
        termination_grace_seconds=0.01,
    )
    first = FakeProcessTransport()
    first_job = service.start(owner, request("first"), Factory(first), yield_time_ms=0)
    try:
        with pytest.raises(ProcessLimitError, match="active process limit of 1"):
            service.start(owner, request("second"), Factory(FakeProcessTransport()), yield_time_ms=0)
        assert service.poll(owner, first_job.session_id, 0, wait_ms=0).state is ProcessState.RUNNING
    finally:
        service.close()


def test_listener_failure_does_not_kill_monitor_or_other_listeners(service, owner) -> None:
    events = []
    service.subscribe(lambda _event: (_ for _ in ()).throw(RuntimeError("listener failed")))
    service.subscribe(events.append)
    transport = FakeProcessTransport()
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)

    transport.exit(0)
    terminal = service.poll(owner, started.session_id, 0, wait_ms=1_000)

    assert terminal.state is ProcessState.EXITED
    eventually(lambda: len(events) == 1)
    assert events[0].session_id == started.session_id


def test_close_terminates_all_processes_then_is_idempotent(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.01,
    )
    first = FakeProcessTransport(exit_on_signals={"terminate"})
    second = FakeProcessTransport(exit_on_signals={"kill"})
    service.start(owner, request("first"), Factory(first), yield_time_ms=0)
    service.start(owner, request("second"), Factory(second), yield_time_ms=0)

    service.close()
    service.close()

    assert first.signals == ["terminate"]
    assert second.signals == ["terminate", "kill"]
    assert first.closed is True
    assert second.closed is True
    assert not (tmp_path / "processes").exists()


def test_close_does_not_signal_process_that_already_exited(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes")
    transport = FakeProcessTransport(close_output_on_exit=False)
    service.start(owner, request(), Factory(transport), yield_time_ms=0)
    transport.exit(0, close_output=False)

    service.close()

    assert transport.signals == []


@pytest.mark.parametrize("timeout", [0, -1])
def test_start_rejects_nonpositive_timeout_before_spawning(tmp_path: Path, owner, timeout: float) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes")
    factory = Factory(FakeProcessTransport())
    try:
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            service.start(owner, request(timeout=timeout), factory, yield_time_ms=0)
        assert factory.created == []
    finally:
        service.close()


def test_terminal_ttl_prunes_completed_record(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes", terminal_ttl_seconds=0)
    completed = service.start(
        owner,
        request("short"),
        Factory(FakeProcessTransport(initial_exit_code=0)),
        yield_time_ms=1_000,
    )
    try:
        assert service.list(owner) == ()
        with pytest.raises(ProcessNotFoundError):
            service.poll(owner, completed.session_id, 0, wait_ms=0)
    finally:
        service.close()
