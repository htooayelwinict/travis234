"""Thread-safe lifecycle authority for coding-agent process sessions."""

from __future__ import annotations

import queue
import secrets
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from appv231.coding_agent.processes.output import SanitizedOutputSpool
from appv231.coding_agent.processes.transport import ProcessTransport
from appv231.coding_agent.processes.types import (
    ProcessClosedError,
    ProcessEvent,
    ProcessInputLimitError,
    ProcessLaunchRequest,
    ProcessLimitError,
    ProcessNotFoundError,
    ProcessOwner,
    ProcessSnapshot,
    ProcessState,
    ProcessStateError,
    StopCause,
)

ProcessTransportFactory = Callable[[ProcessLaunchRequest], ProcessTransport]
ProcessListener = Callable[[ProcessEvent], None]
ProcessOutputListener = Callable[[ProcessSnapshot], None]


@dataclass(frozen=True)
class _InputItem:
    data: bytes
    eof: bool


class _ManagedProcess:
    def __init__(
        self,
        session_id: str,
        owner: ProcessOwner,
        request: ProcessLaunchRequest,
        transport: ProcessTransport,
        output: SanitizedOutputSpool,
        started_at: float,
    ) -> None:
        self.session_id = session_id
        self.owner = owner
        self.request = request
        self.transport = transport
        self.output = output
        self.started_at = started_at
        self.state = ProcessState.RUNNING
        self.stop_cause: StopCause | None = None
        self.exit_code: int | None = None
        self.detached = False
        self.terminal_at: float | None = None
        self.reader_count = 0
        self.reader_threads: list[threading.Thread] = []
        self.input_thread: threading.Thread | None = None
        self.monitor_thread: threading.Thread | None = None
        self.input_queue: queue.Queue[_InputItem | None] = queue.Queue()
        self.pending_input_bytes = 0
        self.input_closed = False
        self.input_error: str | None = None
        self.output_error: str | None = None
        self.terminate_sent = False
        self.kill_sent = False
        self.event_emitted = False
        self.active_calls = 0
        self.foreground_update: ProcessOutputListener | None = None
        self.force_finalize = threading.Event()
        self.wakeup = threading.Event()
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)


class ProcessSessionService:
    def __init__(
        self,
        *,
        directory: str | Path | None = None,
        max_active: int = 4,
        max_terminal: int = 64,
        terminal_ttl_seconds: float = 900,
        max_output_bytes: int = 51_200,
        max_input_bytes: int = 16_384,
        max_pending_input_bytes: int = 65_536,
        termination_grace_seconds: float = 2,
        drain_timeout_seconds: float = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_active < 1:
            raise ValueError("max_active must be positive")
        self._directory = Path(directory) if directory is not None else Path(
            tempfile.mkdtemp(prefix="appv231-processes-")
        )
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._directory.chmod(0o700)
        self._max_active = max_active
        self._max_terminal = max(0, max_terminal)
        self._terminal_ttl_seconds = max(0, terminal_ttl_seconds)
        self._max_output_bytes = max_output_bytes
        self._max_input_bytes = max_input_bytes
        self._max_pending_input_bytes = max_pending_input_bytes
        self._termination_grace_seconds = max(0, termination_grace_seconds)
        self._drain_timeout_seconds = max(0, drain_timeout_seconds)
        self._clock = clock
        self._records: dict[str, _ManagedProcess] = {}
        self._listeners: list[ProcessListener] = []
        self._starting = 0
        self._closed = False
        self._lock = threading.RLock()

    def start(
        self,
        owner: ProcessOwner,
        request: ProcessLaunchRequest,
        transport_factory: ProcessTransportFactory,
        *,
        yield_time_ms: int = 10_000,
        signal=None,
        on_update: ProcessOutputListener | None = None,
    ) -> ProcessSnapshot:
        if not 0 <= yield_time_ms <= 30_000:
            raise ValueError("yield_time_ms must be between 0 and 30000")
        if request.timeout_seconds is not None and request.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._reserve_start()
        output = SanitizedOutputSpool(self._directory)
        try:
            transport = transport_factory(request)
        except BaseException:
            output.close(remove=True)
            self._release_start()
            raise

        session_id = f"proc_{secrets.token_hex(16)}"
        record = _ManagedProcess(session_id, owner, request, transport, output, self._clock())
        record.foreground_update = on_update
        retained = False
        try:
            with self._lock:
                self._starting -= 1
                if self._closed:
                    raise ProcessClosedError("Process service is closed")
                record.active_calls = 1
                self._records[session_id] = record
                retained = True
            self._start_workers(record)
            return self._wait_for_initial_handoff(record, yield_time_ms, signal)
        except BaseException:
            with self._lock:
                self._records.pop(session_id, None)
            transport.close()
            output.close(remove=True)
            raise
        finally:
            if retained:
                self._release_record_call(record)

    def poll(
        self,
        owner: ProcessOwner,
        session_id: str,
        cursor: int,
        *,
        wait_ms: int = 1_000,
        max_bytes: int = 51_200,
    ) -> ProcessSnapshot:
        self._validate_wait(wait_ms)
        if not 1 <= max_bytes <= self._max_output_bytes:
            raise ValueError(f"max_bytes must be between 1 and {self._max_output_bytes}")
        with self._record_call(owner, session_id) as record:
            deadline = self._clock() + wait_ms / 1000
            with record.condition:
                while record.output.size <= cursor and not record.state.terminal:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        break
                    record.condition.wait(min(remaining, 0.05))
            return self._snapshot(record, cursor, max_bytes)

    def write(
        self,
        owner: ProcessOwner,
        session_id: str,
        data: str,
        *,
        eof: bool = False,
        wait_ms: int = 1_000,
    ) -> ProcessSnapshot:
        self._validate_wait(wait_ms)
        encoded = data.encode("utf-8")
        if len(encoded) > self._max_input_bytes:
            raise ProcessInputLimitError(f"Process input accepts at most {self._max_input_bytes} bytes per call")
        with self._record_call(owner, session_id) as record:
            cursor = record.output.size
            with record.condition:
                self._require_running(record, "write")
                if record.input_closed:
                    raise ProcessStateError("Process stdin is closed")
                if record.pending_input_bytes + len(encoded) > self._max_pending_input_bytes:
                    raise ProcessInputLimitError(
                        f"Process pending input accepts at most {self._max_pending_input_bytes} bytes"
                    )
                record.pending_input_bytes += len(encoded)
                if eof:
                    record.input_closed = True
                record.input_queue.put_nowait(_InputItem(encoded, eof))
                record.condition.notify_all()
            return self._wait_after_control(record, cursor, wait_ms)

    def resize(self, owner: ProcessOwner, session_id: str, *, rows: int, cols: int) -> ProcessSnapshot:
        if not 2 <= rows <= 200 or not 20 <= cols <= 500:
            raise ValueError("PTY dimensions are outside supported bounds")
        with self._record_call(owner, session_id) as record:
            with record.condition:
                self._require_running(record, "resize")
                if not record.request.tty:
                    raise ProcessStateError("resize requires tty=true")
                record.transport.resize(rows, cols)
            return self._snapshot(record, record.output.size, self._max_output_bytes)

    def interrupt(
        self,
        owner: ProcessOwner,
        session_id: str,
        *,
        wait_ms: int = 1_000,
    ) -> ProcessSnapshot:
        self._validate_wait(wait_ms)
        with self._record_call(owner, session_id) as record:
            cursor = record.output.size
            with record.condition:
                self._require_running(record, "interrupt")
                record.transport.signal_group("interrupt")
                record.wakeup.set()
            return self._wait_after_control(record, cursor, wait_ms)

    def terminate(
        self,
        owner: ProcessOwner,
        session_id: str,
        *,
        wait_ms: int = 2_000,
    ) -> ProcessSnapshot:
        self._validate_wait(wait_ms)
        with self._record_call(owner, session_id) as record:
            cursor = record.output.size
            self._begin_stop(record, StopCause.TERMINATE)
            return self._wait_after_control(record, cursor, wait_ms)

    def kill(self, owner: ProcessOwner, session_id: str) -> ProcessSnapshot:
        with self._record_call(owner, session_id) as record:
            cursor = record.output.size
            self._begin_stop(record, StopCause.KILL)
            return self._snapshot(record, cursor, self._max_output_bytes)

    def list(self, owner: ProcessOwner) -> tuple[ProcessSnapshot, ...]:
        self._prune()
        with self._lock:
            if self._closed:
                return ()
            records = [record for record in self._records.values() if record.owner == owner]
            for record in records:
                with record.condition:
                    record.active_calls += 1
        try:
            records.sort(
                key=lambda record: (
                    record.state.terminal,
                    -(record.terminal_at if record.terminal_at is not None else record.started_at),
                )
            )
            return tuple(self._snapshot(record, record.output.size, self._max_output_bytes) for record in records)
        finally:
            for record in records:
                self._release_record_call(record)

    def subscribe(self, listener: ProcessListener) -> Callable[[], None]:
        with self._lock:
            if self._closed:
                raise ProcessClosedError("Process service is closed")
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            records = list(self._records.values())
            self._listeners.clear()

        active = [record for record in records if not record.state.terminal]
        for record in active:
            if self._claim_stop(record, StopCause.SHUTDOWN):
                self._safe_signal(record, "terminate")
        self._wait_transports(active, self._termination_grace_seconds)
        for record in active:
            if record.transport.poll() is None:
                self._safe_signal(record, "kill")
        self._wait_transports(active, self._termination_grace_seconds)
        for record in active:
            if record.transport.poll() is None:
                record.force_finalize.set()
                record.wakeup.set()
                record.transport.close()
        for record in records:
            if record.monitor_thread is not None:
                record.monitor_thread.join(
                    timeout=self._termination_grace_seconds + self._drain_timeout_seconds + 0.5
                )
            record.input_queue.put_nowait(None)
            if record.input_thread is not None:
                record.input_thread.join(timeout=0.2)
            self._wait_for_record_calls(record)
            record.transport.close()
            record.output.close(remove=True)
        with self._lock:
            self._records.clear()
        shutil.rmtree(self._directory, ignore_errors=True)

    def export_output(self, owner: ProcessOwner, session_id: str, directory: str | Path) -> Path:
        with self._record_call(owner, session_id) as record:
            with record.condition:
                if not record.state.terminal:
                    raise ProcessStateError("Cannot export output while process is active")
            return record.output.export_copy(directory)

    def tail_snapshot(self, owner: ProcessOwner, session_id: str):
        with self._record_call(owner, session_id) as record:
            return record.output.tail_snapshot()

    def _reserve_start(self) -> None:
        self._prune()
        with self._lock:
            if self._closed:
                raise ProcessClosedError("Process service is closed")
            active = sum(not record.state.terminal for record in self._records.values()) + self._starting
            if active >= self._max_active:
                raise ProcessLimitError(f"Reached active process limit of {self._max_active}")
            self._starting += 1

    def _release_start(self) -> None:
        with self._lock:
            self._starting = max(0, self._starting - 1)

    def _start_workers(self, record: _ManagedProcess) -> None:
        sources = record.transport.read_sources()
        with record.condition:
            record.reader_count = len(sources)
        for index, source in enumerate(sources):
            thread = threading.Thread(
                target=self._read_output,
                args=(record, source),
                name=f"appv231-{record.session_id}-reader-{index}",
                daemon=True,
            )
            record.reader_threads.append(thread)
            thread.start()
        record.input_thread = threading.Thread(
            target=self._pump_input,
            args=(record,),
            name=f"appv231-{record.session_id}-input",
            daemon=True,
        )
        record.monitor_thread = threading.Thread(
            target=self._monitor,
            args=(record,),
            name=f"appv231-{record.session_id}-monitor",
            daemon=True,
        )
        record.input_thread.start()
        record.monitor_thread.start()

    def _read_output(self, record: _ManagedProcess, source) -> None:
        try:
            while True:
                read = getattr(source, "read1", source.read)
                data = read(4096)
                if not data:
                    return
                record.output.append(data)
                with record.condition:
                    listener = record.foreground_update
                    record.condition.notify_all()
                if listener is not None:
                    try:
                        listener(self._snapshot(record, 0, self._max_output_bytes))
                    except BaseException:
                        pass
        except BaseException as error:  # noqa: BLE001 - reader failure is retained as process metadata.
            with record.condition:
                record.output_error = str(error)
        finally:
            with record.condition:
                record.reader_count = max(0, record.reader_count - 1)
                record.condition.notify_all()

    def _pump_input(self, record: _ManagedProcess) -> None:
        while True:
            item = record.input_queue.get()
            if item is None:
                return
            try:
                remaining = memoryview(item.data)
                while remaining:
                    written = record.transport.write(bytes(remaining))
                    if written <= 0:
                        raise BrokenPipeError("Process stdin accepted zero bytes")
                    remaining = remaining[written:]
                if item.eof:
                    record.transport.close_stdin()
                    with record.condition:
                        record.input_closed = True
            except BaseException as error:  # noqa: BLE001 - preserve process while closing unusable stdin.
                with record.condition:
                    record.input_error = str(error)
                    record.input_closed = True
            finally:
                with record.condition:
                    record.pending_input_bytes = max(0, record.pending_input_bytes - len(item.data))
                    record.condition.notify_all()

    def _monitor(self, record: _ManagedProcess) -> None:
        try:
            timeout_at = (
                record.started_at + record.request.timeout_seconds
                if record.request.timeout_seconds is not None
                else None
            )
            while True:
                exit_code = record.transport.poll()
                if exit_code is not None or record.force_finalize.is_set():
                    break
                if timeout_at is not None and self._clock() >= timeout_at:
                    self._begin_stop(record, StopCause.TIMEOUT)
                record.wakeup.wait(0.01)
                record.wakeup.clear()

            with record.condition:
                record.exit_code = exit_code
                record.state = ProcessState.DRAINING
                record.input_closed = True
                record.condition.notify_all()
            record.input_queue.put_nowait(None)
            self._drain_readers(record)
            record.output.finish()
            with record.condition:
                if record.stop_cause is StopCause.TIMEOUT:
                    record.state = ProcessState.TIMED_OUT
                elif record.stop_cause is not None:
                    record.state = ProcessState.TERMINATED
                else:
                    record.state = ProcessState.EXITED
                record.terminal_at = self._clock()
                record.condition.notify_all()
            self._emit_terminal(record)
        except BaseException as error:  # noqa: BLE001 - publish deterministic failure instead of losing monitor.
            with record.condition:
                record.output_error = str(error)
                record.state = ProcessState.FAILED
                record.terminal_at = self._clock()
                record.condition.notify_all()
            try:
                record.output.finish()
            finally:
                self._emit_terminal(record)

    def _drain_readers(self, record: _ManagedProcess) -> None:
        deadline = self._clock() + self._drain_timeout_seconds
        with record.condition:
            while record.reader_count and self._clock() < deadline:
                record.condition.wait(min(0.02, max(0, deadline - self._clock())))
        if record.reader_count:
            try:
                record.transport.signal_group("kill")
            except BaseException as error:  # noqa: BLE001 - descriptor cleanup still runs.
                with record.condition:
                    record.output_error = str(error)
            with record.condition:
                if record.reader_count:
                    record.condition.wait_for(lambda: record.reader_count == 0, timeout=0.2)
        if record.reader_count:
            record.transport.close()
        for thread in record.reader_threads:
            thread.join(timeout=0.1)

    def _wait_for_initial_handoff(self, record: _ManagedProcess, yield_time_ms: int, signal) -> ProcessSnapshot:
        deadline = self._clock() + yield_time_ms / 1000
        while True:
            should_abort = False
            with record.condition:
                if record.state.terminal:
                    record.foreground_update = None
                    return self._snapshot(record, 0, self._max_output_bytes)
                if signal is not None and getattr(signal, "aborted", False) and record.stop_cause is None:
                    should_abort = True
                elif record.stop_cause is not None:
                    record.condition.wait(0.01)
                    continue
                else:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        record.detached = True
                        record.foreground_update = None
                        return self._snapshot(record, 0, self._max_output_bytes)
                    record.condition.wait(min(remaining, 0.01))
            if should_abort:
                self._begin_stop(record, StopCause.ABORT_BEFORE_YIELD)

    def _wait_after_control(self, record: _ManagedProcess, cursor: int, wait_ms: int) -> ProcessSnapshot:
        deadline = self._clock() + wait_ms / 1000
        with record.condition:
            while record.output.size <= cursor and not record.state.terminal:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                record.condition.wait(min(remaining, 0.05))
        return self._snapshot(record, cursor, self._max_output_bytes)

    def _claim_stop(self, record: _ManagedProcess, cause: StopCause) -> bool:
        with record.condition:
            if record.state.terminal or record.stop_cause is not None:
                return False
            if record.transport.poll() is not None:
                return False
            record.stop_cause = cause
            record.state = ProcessState.STOPPING
            record.condition.notify_all()
            record.wakeup.set()
            return True

    def _begin_stop(self, record: _ManagedProcess, cause: StopCause) -> None:
        claimed = self._claim_stop(record, cause)
        if cause is StopCause.KILL:
            self._safe_signal(record, "kill")
            return
        if not claimed:
            return
        if cause is StopCause.ABORT_BEFORE_YIELD:
            self._safe_signal(record, "kill")
            return
        self._safe_signal(record, "terminate")
        self._wait_transports([record], self._termination_grace_seconds)
        if record.transport.poll() is None:
            self._safe_signal(record, "kill")

    @staticmethod
    def _safe_signal(record: _ManagedProcess, signal_name: str) -> None:
        with record.condition:
            if record.transport.poll() is not None:
                return
            if signal_name == "terminate":
                if record.terminate_sent or record.kill_sent:
                    return
                record.terminate_sent = True
            elif signal_name == "kill":
                if record.kill_sent:
                    return
                record.kill_sent = True
        try:
            record.transport.signal_group(signal_name)  # type: ignore[arg-type]
        except BaseException as error:  # noqa: BLE001 - monitor still observes process state.
            with record.condition:
                record.input_error = str(error)
        finally:
            record.wakeup.set()

    def _wait_transports(self, records: list[_ManagedProcess], timeout: float) -> None:
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if all(record.transport.poll() is not None for record in records):
                return
            time.sleep(min(0.005, max(0, deadline - self._clock())))

    def _snapshot(
        self,
        record: _ManagedProcess,
        cursor: int,
        max_bytes: int,
    ) -> ProcessSnapshot:
        output = record.output.read(cursor, max_bytes)
        with record.condition:
            elapsed_at = record.terminal_at if record.terminal_at is not None else self._clock()
            return ProcessSnapshot(
                session_id=record.session_id,
                state=record.state,
                output=output.text,
                cursor=output.cursor,
                next_cursor=output.next_cursor,
                output_size=record.output.size,
                exit_code=record.exit_code,
                tty=record.request.tty,
                elapsed_ms=max(0, int((elapsed_at - record.started_at) * 1000)),
                command=record.request.command,
                cwd=record.request.cwd,
            )

    @contextmanager
    def _record_call(self, owner: ProcessOwner, session_id: str):
        record = self._acquire_record_call(owner, session_id)
        try:
            yield record
        finally:
            self._release_record_call(record)

    def _acquire_record_call(self, owner: ProcessOwner, session_id: str) -> _ManagedProcess:
        self._prune()
        with self._lock:
            if self._closed:
                raise ProcessClosedError("Process service is closed")
            record = self._records.get(session_id)
            if record is None or record.owner != owner:
                raise ProcessNotFoundError(session_id)
            with record.condition:
                record.active_calls += 1
            return record

    @staticmethod
    def _release_record_call(record: _ManagedProcess) -> None:
        with record.condition:
            record.active_calls = max(0, record.active_calls - 1)
            record.condition.notify_all()

    @staticmethod
    def _wait_for_record_calls(record: _ManagedProcess) -> None:
        with record.condition:
            while record.active_calls:
                record.condition.wait(0.05)

    @staticmethod
    def _require_running(record: _ManagedProcess, action: str) -> None:
        if record.state is not ProcessState.RUNNING:
            raise ProcessStateError(f"Cannot {action} process while it is {record.state.value}")

    @staticmethod
    def _validate_wait(wait_ms: int) -> None:
        if not 0 <= wait_ms <= 30_000:
            raise ValueError("wait_ms must be between 0 and 30000")

    def _emit_terminal(self, record: _ManagedProcess) -> None:
        with record.condition:
            if record.event_emitted:
                return
            record.event_emitted = True
            event = ProcessEvent(record.session_id, record.state, record.exit_code, record.owner)
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except BaseException:
                continue

    def _prune(self) -> None:
        now = self._clock()
        with self._lock:
            terminal = [
                record
                for record in self._records.values()
                if record.state.terminal and record.active_calls == 0
            ]
            terminal.sort(key=lambda record: record.terminal_at or record.started_at)
            expired = {
                record.session_id
                for record in terminal
                if record.terminal_at is not None and now - record.terminal_at >= self._terminal_ttl_seconds
            }
            excess = max(0, len(terminal) - self._max_terminal)
            expired.update(record.session_id for record in terminal[:excess])
            removed = [self._records.pop(session_id) for session_id in expired]
        for record in removed:
            record.transport.close()
            record.output.close(remove=True)


__all__ = ["ProcessSessionService", "ProcessTransportFactory"]
