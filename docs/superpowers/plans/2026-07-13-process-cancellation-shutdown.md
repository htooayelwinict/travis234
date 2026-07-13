# Process, Cancellation, and Shutdown Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every owned process is terminated on lifecycle failure, every stdin write reports its actual outcome, repeated Ctrl-C escalates, and TUI shutdown always returns within a defined deadline.

**Architecture:** Keep `ProcessSessionService` as the single OS-process lifecycle authority. Add per-write acknowledgements and one failure-finalization path, expose an explicit user-command interrupt state machine, and make every future/thread wait deadline-aware and observable.

**Tech Stack:** Python 3.13, threads, `concurrent.futures`, pytest fault injection, POSIX process transports.

## Global Constraints

- Use final paths/imports after the rebrand plan: `travis/...` and `tests/...`.
- All concurrency regressions must fail deterministically without arbitrary sleeps longer than 100 ms.
- A terminal process record may not own a transport that is known or presumed alive.
- `write()` succeeds only after all bytes and optional EOF are accepted by the transport.
- First/second/third Ctrl-C actions are interrupt/terminate/kill and must be visible to the TUI.
- Shutdown defaults: active-turn grace 1.0 seconds, executor join 1.0 seconds, user-command cancel grace 0.25 seconds per stage.
- No change in ordered tool results, iteration budgeting, bounded tool parallelism, or compaction behavior.

---

### Task 1: Reproduce monitor-exception process orphaning

**Files:**
- Modify: `tests/test_process_service.py`
- Modify later: `travis/coding_agent/processes/service.py`

**Interfaces:**
- Consumes: existing `FakeProcessTransport`, `Factory`, `eventually`, and `ProcessSessionService` fixtures.
- Produces: regression `test_monitor_failure_terminates_transport_before_failed_state`.

- [ ] **Step 1: Add a faulting transport and failing regression**

```python
class RefreshFailureTransport(FakeProcessTransport):
    def __init__(self) -> None:
        super().__init__(exit_on_signals={"kill"})
        self.refresh_attempted = threading.Event()

    def refresh_tree(self) -> None:
        self.refresh_attempted.set()
        raise RuntimeError("tree refresh failed")


def test_monitor_failure_terminates_transport_before_failed_state(service, owner) -> None:
    transport = RefreshFailureTransport()
    started = service.start(owner, request("monitor-failure"), Factory(transport), yield_time_ms=0)

    assert transport.refresh_attempted.wait(timeout=1)
    terminal = service.wait_terminal(owner, started.session_id, 0, wait_ms=2_000)

    assert terminal.state is ProcessState.FAILED
    assert terminal.failure_code == "monitor_failure"
    assert transport.poll() is not None
    assert transport.signals == ["terminate", "kill"]
```

- [ ] **Step 2: Run the regression to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_service.py::test_monitor_failure_terminates_transport_before_failed_state -q`

Expected: FAIL because the snapshot becomes `FAILED` while `transport.poll()` remains `None` and no signal was sent.

- [ ] **Step 3: Add transport-liveness and monitor-failure helpers**

Implement these private contracts in `ProcessSessionService`:

```python
@staticmethod
def _transport_is_alive(record: _ManagedProcess) -> bool:
    try:
        return record.transport.poll() is None
    except BaseException:
        return True


def _terminate_owned_transport(self, record: _ManagedProcess) -> None:
    self._safe_signal(record, "terminate", check_liveness=False)
    self._wait_transports([record], self._termination_grace_seconds)
    if self._transport_is_alive(record):
        self._safe_signal(record, "kill", check_liveness=False)
    self._wait_transports([record], self._termination_grace_seconds)


def _fail_monitor(self, record: _ManagedProcess, error: BaseException) -> None:
    with record.condition:
        record.output_error = type(error).__name__[:80]
        record.failure_code = "monitor_failure"
        record.state = ProcessState.STOPPING
        record.input_closed = True
        record.condition.notify_all()
    self._terminate_owned_transport(record)
    self._finalize_record(record, ProcessState.FAILED)
```

Refactor normal and exceptional monitor exits through `_finalize_record()`. The
helper finishes output, persists completion, sets `terminal_at`, publishes the
terminal state, and emits the terminal event exactly once. `_safe_signal()` must
catch a failing `poll()` and still attempt the requested signal when
`check_liveness=False`.

- [ ] **Step 4: Defensively clean terminal records in `close()`**

Replace the old active-only selection with:

```python
owned = [record for record in records if self._transport_is_alive(record)]
for record in owned:
    self._terminate_owned_transport(record)
```

No `record.state.terminal` filter may precede the liveness check.

- [ ] **Step 5: Run focused process tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_service.py tests/test_process_local.py tests/test_process_completions.py -q`

Expected: PASS.

- [ ] **Step 6: Commit monitor ownership repair**

```bash
git add travis/coding_agent/processes/service.py tests/test_process_service.py
git commit -m "fix: terminate owned process after monitor failure"
```

### Task 2: Acknowledge stdin writes and propagate pump failures

**Files:**
- Modify: `travis/coding_agent/processes/service.py`
- Modify: `tests/test_process_service.py`
- Modify: `tests/test_process_tools.py`

**Interfaces:**
- Produces `_InputItem(data: bytes, eof: bool, completion: Future[None])`.
- Produces service constructor option `input_write_timeout_seconds: float = 5.0`.
- `ProcessSessionService.write(...) -> ProcessSnapshot` raises `ProcessStateError` on transport failure/timeout.

- [ ] **Step 1: Write the failing broken-pipe regression**

```python
class BrokenInputTransport(FakeProcessTransport):
    def write(self, data: bytes) -> int:
        self.write_started.set()
        raise BrokenPipeError("stdin closed")


def test_write_waits_for_pump_and_raises_broken_pipe(service, owner) -> None:
    transport = BrokenInputTransport()
    started = service.start(owner, request("closed-stdin"), Factory(transport), yield_time_ms=0)

    with pytest.raises(ProcessStateError, match="stdin write failed.*stdin closed"):
        service.write(owner, started.session_id, "hello", wait_ms=0)

    inspected = service.poll(owner, started.session_id, 0, wait_ms=0)
    assert inspected.state is ProcessState.RUNNING
    with pytest.raises(ProcessStateError, match="stdin is closed"):
        service.write(owner, started.session_id, "again", wait_ms=0)
```

- [ ] **Step 2: Write the failing partial-write acknowledgement test**

```python
def test_write_returns_only_after_all_partial_writes_are_accepted(service, owner) -> None:
    transport = FakeProcessTransport()
    accepted: list[bytes] = []

    def partial(data: bytes) -> int:
        accepted.append(bytes(data))
        return min(2, len(data))

    transport.write = partial  # type: ignore[method-assign]
    started = service.start(owner, request("partial"), Factory(transport), yield_time_ms=0)
    service.write(owner, started.session_id, "abcde", wait_ms=0)
    assert accepted == [b"abcde", b"cde", b"e"]
```

- [ ] **Step 3: Run both tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_service.py -k 'write_waits_for_pump or partial_writes' -q`

Expected: broken-pipe test FAIL because the old method returns a running snapshot before the pump writes; partial-write test may pass only after the pump eventually runs and therefore does not prove acknowledgement.

- [ ] **Step 4: Add completion-backed input items**

```python
from concurrent.futures import Future, TimeoutError as FutureTimeoutError


@dataclass(frozen=True)
class _InputItem:
    data: bytes
    eof: bool
    completion: Future[None]
```

`write()` enqueues the item, releases the record lock, and then waits for
`completion.result(timeout=self._input_write_timeout_seconds)`. On timeout it
closes stdin state and raises `ProcessStateError("Process stdin write timed out")`.
On another exception it raises
`ProcessStateError(f"Process stdin write failed: {error}")` from that exception.
Only after the acknowledgement does it call `_wait_after_control()`.

- [ ] **Step 5: Complete or fail every request in the pump**

```python
try:
    # write every byte and optional EOF
except BaseException as error:
    with record.condition:
        record.input_error = str(error)
        record.input_closed = True
    item.completion.set_exception(error)
    self._fail_queued_input(record, error)
    return
else:
    item.completion.set_result(None)
finally:
    # decrement pending bytes and notify
```

`_fail_queued_input()` drains queued `_InputItem` objects, decrements their
pending byte counts, and completes each future exceptionally. Monitor exit and
service close call it before enqueueing the input-thread sentinel.

- [ ] **Step 6: Run process service/tool tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_service.py tests/test_process_tools.py tests/test_process_regressions.py -q`

Expected: PASS.

- [ ] **Step 7: Commit stdin acknowledgement**

```bash
git add travis/coding_agent/processes/service.py tests/test_process_service.py tests/test_process_tools.py
git commit -m "fix: acknowledge managed process stdin writes"
```

### Task 3: Implement repeated Ctrl-C escalation

**Files:**
- Modify: `travis/tui/user_commands.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `tests/test_tui_user_commands.py`
- Modify: `tests/test_tui.py`

**Interfaces:**
- Produces `InterruptAction(Enum)` with `NONE`, `INTERRUPT`, `TERMINATE`, `KILL`.
- `interrupt_focused() -> InterruptAction`; `interrupt(command_id) -> InterruptAction`.
- `_UserCommandState.interrupt_count: int` replaces `interrupt_requested: bool`; inspection exposes both count and derived boolean.

- [ ] **Step 1: Replace the idempotence expectation with a failing escalation test**

```python
def test_repeated_interrupt_escalates_managed_command(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes", termination_grace_seconds=0.01)
    transport = FakeProcessTransport(exit_on_signals={"kill"})
    controller = make_controller(service, transport, tmp_path)
    try:
        handle = controller.start("stuck", binding())
        assert wait_until(lambda: controller.inspect(handle.command_id).process_id is not None)
        assert controller.interrupt_focused() is InterruptAction.INTERRUPT
        assert controller.interrupt_focused() is InterruptAction.TERMINATE
        assert controller.interrupt_focused() is InterruptAction.KILL
        assert wait_until(lambda: controller.inspect(handle.command_id).done)
        assert transport.signals[:3] == ["interrupt", "terminate", "kill"]
    finally:
        controller.close()
        service.close()
```

- [ ] **Step 2: Add a failing TUI status regression**

```python
def test_ctrl_c_status_reflects_user_command_escalation(mode, monkeypatch) -> None:
    actions = iter((InterruptAction.INTERRUPT, InterruptAction.TERMINATE, InterruptAction.KILL))
    monkeypatch.setattr(mode._user_commands, "interrupt_focused", lambda: next(actions))
    mode._handle_editor_escape()
    assert mode.status._message == "Interrupting user command"
    mode._handle_editor_escape()
    assert mode.status._message == "Terminating user command"
    mode._handle_editor_escape()
    assert mode.status._message == "Killing user command"
```

- [ ] **Step 3: Run both tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_tui_user_commands.py tests/test_tui.py -k 'escalat or status_reflects' -q`

Expected: FAIL because the second interrupt returns `False`.

- [ ] **Step 4: Implement the explicit action state machine**

```python
class InterruptAction(str, Enum):
    NONE = "none"
    INTERRUPT = "interrupt"
    TERMINATE = "terminate"
    KILL = "kill"


def interrupt(self, command_id: str) -> InterruptAction:
    with self._lock:
        state = self._states.get(command_id)
        if state is None or state.done:
            return InterruptAction.NONE
        state.interrupt_count += 1
        attempt = state.interrupt_count
        process_id = state.process_id
    state.signal.abort()
    if process_id is None:
        return InterruptAction.INTERRUPT
    if attempt == 1:
        self._service.interrupt(state.owner, process_id, wait_ms=0)
        return InterruptAction.INTERRUPT
    if attempt == 2:
        self._service.terminate(state.owner, process_id, wait_ms=0)
        return InterruptAction.TERMINATE
    self._service.kill(state.owner, process_id)
    return InterruptAction.KILL
```

Catch only process-not-found/terminal races; do not swallow arbitrary service
errors. `_handle_editor_escape()` maps actions to the exact status strings in the
test. Completion resets ownership by marking the state done; a new command starts
with count zero.

- [ ] **Step 5: Replace the uninterruptible 60-second cancellation wait**

After `ProcessWaitCancelledError`, use this bounded sequence:

```python
for action, wait_ms in (("interrupt", 250), ("terminate", 250), ("kill", 500)):
    snapshot = getattr(self._service, action)(state.owner, snapshot.session_id, wait_ms=0) if action != "kill" else self._service.kill(state.owner, snapshot.session_id)
    snapshot = self._service.wait_terminal(state.owner, snapshot.session_id, cursor, wait_ms=wait_ms)
    if snapshot.state.terminal:
        break
if not snapshot.state.terminal:
    raise RuntimeError("User command did not terminate after forced cancellation")
```

Repeated user interrupts can advance the same process faster; repeated signals
remain idempotent in `ProcessSessionService`.

- [ ] **Step 6: Run TUI command suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_tui_user_commands.py tests/test_tui.py -q`

Expected: PASS.

- [ ] **Step 7: Commit Ctrl-C escalation**

```bash
git add travis/tui/user_commands.py travis/tui/interactive_mode.py tests/test_tui_user_commands.py tests/test_tui.py
git commit -m "fix: escalate repeated user-command interrupts"
```

### Task 4: Bound session executor and TUI exit waits

**Files:**
- Modify: `travis/coding_agent/session_commands.py`
- Create: `travis/tui/shutdown.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `tests/test_session_commands.py`
- Create: `tests/test_tui_shutdown.py`

**Interfaces:**
- `SessionCommandExecutor.close(*, wait: bool = True, timeout: float = 1.0) -> bool` returns whether the worker exited.
- `ShutdownResult(completed: bool, timed_out_operation: str | None, elapsed_seconds: float)`.
- `TurnShutdownController.stop(future, thread, abort, executor) -> ShutdownResult`.

- [ ] **Step 1: Write a failing bounded-executor test**

```python
def test_close_returns_false_when_active_command_is_stuck() -> None:
    executor = SessionCommandExecutor(daemon=True)
    started = threading.Event()
    release = threading.Event()
    executor.submit("stuck-provider", lambda: (started.set(), release.wait()))
    assert started.wait(timeout=1)
    before = time.monotonic()
    try:
        assert executor.close(timeout=0.05) is False
        assert time.monotonic() - before < 0.25
    finally:
        release.set()
```

- [ ] **Step 2: Write a failing `/exit` stuck-turn test**

```python
def test_exit_aborts_stuck_turn_and_returns_by_deadline(tmp_path: Path) -> None:
    release = threading.Event()
    started = threading.Event()
    app = make_app_with_stuck_turn(tmp_path, started=started, release=release)
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit", shutdown_timeout_seconds=0.05)
    mode.init()
    mode._start_turn_thread("stuck", 0, 0)
    assert started.wait(timeout=1)
    before = time.monotonic()
    try:
        assert mode.run() == 0
        assert time.monotonic() - before < 0.25
        assert app.session.agent.signal.aborted is True
    finally:
        release.set()
```

- [ ] **Step 3: Run shutdown tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_session_commands.py::test_close_returns_false_when_active_command_is_stuck tests/test_tui_shutdown.py::test_exit_aborts_stuck_turn_and_returns_by_deadline -q`

Expected: FAIL/hang under an outer 2-second test timeout because current `join()`/`Future.result()` are unbounded.

- [ ] **Step 4: Make executor close deadline-aware**

```python
def close(self, *, wait: bool = True, timeout: float = 1.0) -> bool:
    if timeout < 0:
        raise ValueError("timeout must be nonnegative")
    with self._lock:
        if not self._closed:
            self._closed = True
            self._cancel_queued_commands()
            self._queue.put(_STOP)
    if not wait or self._thread is threading.current_thread():
        return not self._thread.is_alive()
    self._thread.join(timeout=timeout)
    return not self._thread.is_alive()
```

The worker defaults to `daemon=True`. `_cancel_queued_commands()` drains queued
commands and calls `future.cancel()` while preserving the sentinel if observed.

- [ ] **Step 5: Centralize bounded turn shutdown**

`TurnShutdownController.stop()`:

1. calls `abort()` immediately;
2. computes one monotonic deadline;
3. waits on `future.result(timeout=remaining)` and catches
   `concurrent.futures.TimeoutError`;
4. joins a non-current thread with `timeout=remaining`;
5. calls `executor.close(timeout=remaining)`;
6. returns a `ShutdownResult` instead of blocking or re-raising provider errors.

`InteractiveMode._wait_for_active_turn()` delegates to the controller, drains
already-posted UI work, displays `Shutdown timed out: <operation>` when needed,
and returns the result. `/exit`, `/quit`, EOF, and final cleanup all call the same
path. `_run_session_command()` uses a finite command timeout and reports timeout
through the normal status/error channel.

- [ ] **Step 6: Run focused shutdown/TUI suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_session_commands.py tests/test_tui_shutdown.py tests/test_tui.py -q`

Expected: PASS, including the old successful-wait behavior and new timeout behavior.

- [ ] **Step 7: Run process/TUI cross-check**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_service.py tests/test_process_tools.py tests/test_tui_user_commands.py tests/test_session_commands.py tests/test_tui_shutdown.py -q`

Expected: PASS with no non-daemon `travis-*` worker left alive after each test.

- [ ] **Step 8: Commit bounded shutdown**

```bash
git add travis/coding_agent/session_commands.py travis/tui/shutdown.py travis/tui/interactive_mode.py tests/test_session_commands.py tests/test_tui_shutdown.py tests/test_tui.py
git commit -m "fix: bound TUI and session-executor shutdown"
```

### Task 5: Remove adjacent unbounded close paths

**Files:**
- Modify: `travis/tui/model_loader.py`
- Modify: `travis/coding_agent/processes/service.py`
- Create: `tests/test_model_loader.py`
- Modify: `tests/test_process_service.py`
- Modify: `tests/test_tui_shutdown.py`

**Interfaces:**
- `ModelCatalogLoader.close(timeout_seconds: float = 1.0) -> bool`.
- `ProcessSessionService` constructor option `record_call_close_timeout_seconds: float = 1.0`.
- Service close fails/abandons active record calls at the deadline without an unbounded wait.

- [ ] **Step 1: Add failing model-loader deadline test**

```python
def test_model_loader_close_returns_when_fetch_is_stuck() -> None:
    started = threading.Event()
    release = threading.Event()
    loader = ModelCatalogLoader(lambda: (started.set(), release.wait()), thread_name="stuck-loader")
    assert started.wait(timeout=1)
    before = time.monotonic()
    try:
        assert loader.close(timeout_seconds=0.05) is False
        assert time.monotonic() - before < 0.25
    finally:
        release.set()
```

- [ ] **Step 2: Add failing active-record-call deadline test**

```python
def test_process_service_close_does_not_wait_forever_for_active_call(tmp_path, owner) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes", record_call_close_timeout_seconds=0.05)
    transport = FakeProcessTransport()
    started = service.start(owner, request("active-call"), Factory(transport), yield_time_ms=0)
    entered = threading.Event()
    release = threading.Event()

    def hold_call() -> None:
        with service._record_call(owner, started.session_id):
            entered.set()
            release.wait()

    thread = threading.Thread(target=hold_call, daemon=True)
    thread.start()
    assert entered.wait(timeout=1)
    before = time.monotonic()
    try:
        service.close()
        assert time.monotonic() - before < 0.25
    finally:
        release.set()
```

- [ ] **Step 3: Run both tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_model_loader.py::test_model_loader_close_returns_when_fetch_is_stuck tests/test_process_service.py::test_process_service_close_does_not_wait_forever_for_active_call -q`

Expected: FAIL because `ThreadPoolExecutor.shutdown(wait=True)` and `_wait_for_record_calls()` are unbounded.

- [ ] **Step 4: Use an owned daemon worker for model loading**

Replace `ThreadPoolExecutor` with one daemon thread plus `Future`. `close()` marks
the loader closed, cancels a not-yet-running future, joins only to the supplied
deadline, suppresses late callbacks after close, and returns whether the thread
stopped.

- [ ] **Step 5: Bound active-record-call cleanup**

`_wait_for_record_calls()` computes one monotonic deadline. At expiry it records
`persistence_error="record call still active during shutdown"`, fails pending
stdin requests, detaches listener/output cleanup from the caller, and returns.
All transport termination occurs before this wait, so an abandoned API call
cannot leave its OS process alive.

- [ ] **Step 6: Run end-to-end shutdown suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_model_loader.py tests/test_process_service.py tests/test_session_commands.py tests/test_tui_shutdown.py tests/test_tui.py -k 'close or shutdown or exit or active_call or model_loader' -q`

Expected: PASS; no tested close path waits beyond its configured deadline.

- [ ] **Step 7: Commit end-to-end shutdown bounds**

```bash
git add travis/tui/model_loader.py travis/coding_agent/processes/service.py tests/test_model_loader.py tests/test_process_service.py tests/test_tui_shutdown.py
git commit -m "fix: bound model-loader and process-service close"
```
