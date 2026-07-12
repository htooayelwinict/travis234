from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

import pytest

from appv231.coding_agent.processes.completions import ProcessCompletionStore
from appv231.coding_agent.processes.output import SanitizedOutputSpool
from appv231.coding_agent.processes.service import ProcessSessionService
from appv231.coding_agent.processes.types import (
    ProcessInputLimitError,
    ProcessLaunchRequest,
    ProcessLimitError,
    ProcessNotFoundError,
    ProcessOwner,
    ProcessState,
    ProcessStateError,
    ProcessWaitCancelledError,
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

    def refresh_tree(self) -> None:
        pass

    def signal_tree(self, signal_name: str) -> None:
        self.signal_group(signal_name)

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


def test_write_rejects_input_queued_after_eof(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.01,
    )
    gate = threading.Event()
    transport = FakeProcessTransport(write_gate=gate)
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)
    try:
        service.write(owner, started.session_id, "last", eof=True, wait_ms=0)
        assert transport.write_started.wait(1)

        with pytest.raises(ProcessStateError, match="stdin is closed"):
            service.write(owner, started.session_id, "too late", wait_ms=0)
    finally:
        gate.set()
        service.close()


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


def test_kill_immediately_escalates_existing_terminate_without_relabeling(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.5,
    )
    transport = FakeProcessTransport(exit_on_signals={"kill"})
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)
    worker = threading.Thread(
        target=lambda: service.terminate(owner, started.session_id, wait_ms=0),
    )
    worker.start()
    eventually(lambda: transport.signals == ["terminate"])
    try:
        service.kill(owner, started.session_id)

        assert transport.signals == ["terminate", "kill"]
        terminal = service.poll(owner, started.session_id, 0, wait_ms=1_000)
        assert terminal.state is ProcessState.TERMINATED
    finally:
        worker.join(timeout=1)
        service.close()


def test_close_escalates_and_waits_for_in_flight_control(tmp_path: Path, owner, monkeypatch) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.05,
    )
    transport = FakeProcessTransport(exit_on_signals={"kill"})
    started = service.start(owner, request(), Factory(transport), yield_time_ms=0)
    snapshot_entered = threading.Event()
    release_snapshot = threading.Event()
    close_finished = threading.Event()
    errors = []
    original_wait = service._wait_after_control

    def gated_wait(*args, **kwargs):
        snapshot_entered.set()
        assert release_snapshot.wait(1)
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(service, "_wait_after_control", gated_wait)

    def terminate() -> None:
        try:
            service.terminate(owner, started.session_id, wait_ms=0)
        except BaseException as error:  # noqa: BLE001 - assertion captures cross-thread lifecycle errors.
            errors.append(error)

    def close() -> None:
        try:
            service.close()
        finally:
            close_finished.set()

    worker = threading.Thread(target=terminate)
    worker.start()
    eventually(lambda: transport.signals == ["terminate"])
    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert snapshot_entered.wait(1)
        close_finished.wait(0.2)
        release_snapshot.set()
        worker.join(timeout=1)
        closer.join(timeout=1)

        assert transport.signals == ["terminate", "kill"]
        assert errors == []
        assert not worker.is_alive()
        assert not closer.is_alive()
    finally:
        release_snapshot.set()
        worker.join(timeout=1)
        closer.join(timeout=1)
        service.close()


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
    transport = FakeProcessTransport()
    service.start(owner, request(), Factory(transport), yield_time_ms=0)
    transport.exit(0)

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


def test_terminal_elapsed_time_stops_at_completion(tmp_path: Path, owner) -> None:
    now = [10.0]
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        clock=lambda: now[0],
    )
    try:
        completed = service.start(
            owner,
            request("short"),
            Factory(FakeProcessTransport(initial_exit_code=0)),
            yield_time_ms=1_000,
        )
        now[0] += 100

        retained = service.list(owner)[0]

        assert retained.elapsed_ms == completed.elapsed_ms
    finally:
        service.close()


def test_list_orders_active_then_newest_terminal_completion(tmp_path: Path, owner) -> None:
    now = [1.0]
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        clock=lambda: now[0],
    )
    first_transport = FakeProcessTransport()
    first = service.start(owner, request("first"), Factory(first_transport), yield_time_ms=0)
    now[0] = 2.0
    second_transport = FakeProcessTransport()
    second = service.start(owner, request("second"), Factory(second_transport), yield_time_ms=0)
    try:
        now[0] = 3.0
        second_transport.exit(0)
        eventually(lambda: service.poll(owner, second.session_id, 0, wait_ms=0).state.terminal)
        now[0] = 4.0
        first_transport.exit(0)
        eventually(lambda: service.poll(owner, first.session_id, 0, wait_ms=0).state.terminal)

        retained = service.list(owner)

        assert [snapshot.session_id for snapshot in retained] == [first.session_id, second.session_id]
    finally:
        service.close()


def test_wait_terminal_ignores_output_updates_until_terminal(service, owner) -> None:
    transport = FakeProcessTransport()
    started = service.start(owner, request("chatty"), Factory(transport), yield_time_ms=0)
    result = []
    waiter = threading.Thread(
        target=lambda: result.append(
            service.wait_terminal(owner, started.session_id, 0, wait_ms=5_000)
        )
    )
    waiter.start()
    try:
        transport.emit(b"one\n")
        transport.emit(b"two\n")
        time.sleep(0.05)
        assert waiter.is_alive()

        transport.exit(0)
        waiter.join(timeout=1)

        assert not waiter.is_alive()
        assert result[0].state is ProcessState.EXITED
        assert result[0].output == "one\ntwo\n"
    finally:
        transport.exit(0)
        waiter.join(timeout=1)


def test_wait_cancellation_does_not_kill_detached_job(service, owner) -> None:
    signal = Signal(aborted=True)
    transport = FakeProcessTransport()
    started = service.start(owner, request("long"), Factory(transport), yield_time_ms=0)

    with pytest.raises(ProcessWaitCancelledError):
        service.wait_terminal(
            owner,
            started.session_id,
            0,
            wait_ms=60_000,
            signal=signal,
        )

    assert transport.signals == []
    assert service.poll(owner, started.session_id, 0, wait_ms=0).state is ProcessState.RUNNING


def test_terminal_poll_falls_back_to_durable_completion_after_memory_eviction(
    tmp_path: Path,
    owner,
) -> None:
    store = ProcessCompletionStore(tmp_path / "completions")
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        completion_store=store,
        terminal_ttl_seconds=0,
    )
    try:
        completed = service.start(
            owner,
            request("short"),
            Factory(FakeProcessTransport(initial_output=b"durable\n", initial_exit_code=0)),
            yield_time_ms=1_000,
        )

        recovered = service.poll(owner, completed.session_id, 0, wait_ms=0)

        assert recovered.state is ProcessState.EXITED
        assert recovered.output == "durable\n"
        assert recovered.durable_output is True
        assert recovered.full_output_path is not None
    finally:
        service.close()
        store.close()


def test_durable_completion_supports_wait_tail_and_export_after_eviction(
    tmp_path: Path,
    owner,
) -> None:
    store = ProcessCompletionStore(tmp_path / "completions")
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        completion_store=store,
        terminal_ttl_seconds=0,
    )
    try:
        completed = service.start(
            owner,
            request("short"),
            Factory(FakeProcessTransport(initial_output=b"final\n", initial_exit_code=0)),
            yield_time_ms=1_000,
        )
        service.list(owner)

        waited = service.wait_terminal(owner, completed.session_id, 0, wait_ms=1_000)
        tail = service.tail_snapshot(owner, completed.session_id)
        exported = service.export_output(owner, completed.session_id, tmp_path / "exports")

        assert waited.output == "final\n"
        assert tail.content == "final\n"
        assert exported.read_text(encoding="utf-8") == "final\n"
        assert exported.stat().st_mode & 0o777 == 0o600
    finally:
        service.close()
        store.close()


def test_spool_failure_stops_process_and_publishes_failed(
    tmp_path: Path,
    owner,
    monkeypatch,
) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=0.01,
    )
    transport = FakeProcessTransport(exit_on_signals={"kill"})

    def fail_append(self, data: bytes) -> None:
        raise OSError("simulated full spool")

    monkeypatch.setattr(SanitizedOutputSpool, "append", fail_append)
    try:
        started = service.start(owner, request("writer"), Factory(transport), yield_time_ms=0)
        transport.emit(b"data")
        terminal = service.wait_terminal(owner, started.session_id, 0, wait_ms=2_000)

        assert terminal.state is ProcessState.FAILED
        assert terminal.failure_code == "output_failure"
        assert set(transport.signals) & {"terminate", "kill"}
    finally:
        service.close()


def test_active_limit_is_per_owner_scope_with_global_ceiling(tmp_path: Path) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        max_active_per_owner=1,
        max_active_total=3,
        termination_grace_seconds=0.01,
    )
    left = ProcessOwner("app", "/left", "agent")
    right = ProcessOwner("app", "/right", "agent")
    left_transport = FakeProcessTransport()
    right_transport = FakeProcessTransport()
    try:
        service.start(left, request("left"), Factory(left_transport), yield_time_ms=0)

        with pytest.raises(ProcessLimitError, match="owner scope"):
            service.start(left, request("left-two"), Factory(FakeProcessTransport()), yield_time_ms=0)

        assert service.start(
            right,
            request("right"),
            Factory(right_transport),
            yield_time_ms=0,
        ).state is ProcessState.RUNNING
    finally:
        service.close()


def test_process_output_limit_fails_only_producer_and_preserves_prefix(
    tmp_path: Path,
    owner,
) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        max_spool_bytes_per_process=8,
        max_live_spool_bytes=32,
        termination_grace_seconds=0.01,
    )
    transport = FakeProcessTransport(exit_on_signals={"kill"})
    try:
        started = service.start(owner, request("chatty"), Factory(transport), yield_time_ms=0)
        transport.emit(b"123456789")
        terminal = service.wait_terminal(owner, started.session_id, 0, wait_ms=2_000)

        assert terminal.state is ProcessState.FAILED
        assert terminal.failure_code == "output_limit"
        assert terminal.output == "12345678"
        assert terminal.output_size == 8
    finally:
        service.close()


def test_spawn_failure_releases_owner_reservation(tmp_path: Path, owner) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        max_active_per_owner=1,
        termination_grace_seconds=0.01,
    )
    try:
        with pytest.raises(queue.Empty):
            service.start(owner, request("failed"), Factory(), yield_time_ms=0)

        started = service.start(
            owner,
            request("replacement"),
            Factory(FakeProcessTransport()),
            yield_time_ms=0,
        )
        assert started.state is ProcessState.RUNNING
    finally:
        service.close()


def test_live_budget_evicts_durable_terminal_before_limiting_active_producer(
    tmp_path: Path,
    owner,
) -> None:
    store = ProcessCompletionStore(tmp_path / "completions")
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        completion_store=store,
        max_spool_bytes_per_process=8,
        max_live_spool_bytes=8,
        termination_grace_seconds=0.01,
    )
    try:
        first = service.start(
            owner,
            request("first"),
            Factory(FakeProcessTransport(initial_output=b"12345678", initial_exit_code=0)),
            yield_time_ms=1_000,
        )
        second_transport = FakeProcessTransport()
        second = service.start(
            owner,
            request("second"),
            Factory(second_transport),
            yield_time_ms=0,
        )
        second_transport.emit(b"abcdefgh")
        second_transport.exit(0)

        terminal = service.wait_terminal(owner, second.session_id, 0, wait_ms=2_000)
        recovered_first = service.poll(owner, first.session_id, 0, wait_ms=0)

        assert terminal.state is ProcessState.EXITED
        assert terminal.output == "abcdefgh"
        assert recovered_first.output == "12345678"
        assert recovered_first.durable_output is True
    finally:
        service.close()
        store.close()
