# appv231 Process Runtime v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make managed process waiting, failure publication, quotas, descendant cleanup, terminal retention, and detached-output artifacts deterministic without modifying the generic agent loop.

**Architecture:** `ProcessSessionService` remains the in-memory lifecycle authority and gains host-side terminal waiting, per-owner reservation, output-failure handling, and a durable completion sink. `ProcessCompletionStore` owns transactional indexed metadata and atomic terminal output, while local transports terminate both the process group and tracked descendants. The model-facing `process` tool exposes this through a sequential `wait` action.

**Tech Stack:** Python 3.13, existing process state machine and output spool, `threading.Condition`, standard-library `sqlite3`, atomic output files, `psutil>=6.1`, pytest, existing execution backend.

## Global Constraints

- Do not modify any file under `appV2.3.1/appv231/agent/`.
- Do not modify any file under `appV2.3.1/appv231/compaction/`.
- A host wait deadline is never a process execution timeout.
- Cancelling `process.wait` must not kill an already detached process.
- Keep direct SDK/custom `BashOperations` and internal subagent bash synchronous.
- Preserve command prefix, shell path, spawn hook, execution backend, extension hooks, package consent, and opaque workspace ownership.
- Never expose PID, process group, environment values, raw descriptors, or unsanitized output.
- Retain the 15-minute/64-record live terminal cache; durable terminal records default to seven days, 256 MiB, and 10,000 rows.
- Default sanitized live-spool budgets are 64 MiB per process and 512 MiB app-wide; an output limit is not an elapsed-time timeout.
- Use red-green TDD and make one scoped commit per task.
- Do not push, publish, release, or modify unrelated worktree files.

---

### Task 1: Durable Completion Contracts and Store

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/processes/types.py`
- Create: `appV2.3.1/appv231/coding_agent/processes/completions.py`
- Modify: `appV2.3.1/appv231/coding_agent/processes/__init__.py`
- Create: `appV2.3.1/tests/test_process_completions.py`

**Interfaces:**
- Consumes: `ProcessOwner`, `ProcessSnapshot`, `ProcessState`, and sanitized terminal output.
- Produces: `ProcessCompletionRecord`, `ProcessCompletionStore.persist`, `resolve`, `inspect`, `inspect_many`, `tail_snapshot`, `prune`, and `close`.
- Produces: `ProcessOwner.persistence_scope` and terminal snapshot fields `durable_output`, `full_output_path`, and `failure_code`.
- Guarantees: transactional mode-0600 indexed metadata, atomic mode-0600 output, workspace/origin authorization, deterministic cursor reads, bounded retention without per-write full scans, and no command/environment persistence.

- [ ] **Step 1: Write failing durable-store tests**

Create `test_process_completions.py` with round-trip, cross-workspace denial,
app-instance restart recovery, cursor determinism, corrupt row/log handling,
corrupt-index quarantine, two-store concurrency, orphan cleanup, TTL pruning,
size-cap pruning, 10,000-row pruning, and an assertion that persist performs no
directory-wide metadata scan. Prove `inspect_many` resolves 64 IDs with one SQL
statement. Add a sparse 64 MiB output fixture whose terminal tail lookup reads
at most four times the configured 51,200-byte result budget.

```python
def test_completion_survives_new_app_instance_without_cross_workspace_access(tmp_path: Path) -> None:
    store = ProcessCompletionStore(tmp_path, retention_seconds=604_800, max_total_bytes=268_435_456)
    first = ProcessOwner("app-one", str(tmp_path / "workspace"), "agent")
    restarted = ProcessOwner("app-two", str(tmp_path / "workspace"), "agent")
    foreign = ProcessOwner("app-two", str(tmp_path / "other"), "agent")
    output = tmp_path / "terminal.log"
    output.write_text("build complete\n", encoding="utf-8")
    process_id = "proc_" + "a" * 32

    store.persist(
        first,
        ProcessCompletionRecord(
            session_id=process_id,
            state=ProcessState.EXITED,
            exit_code=0,
            output_size=15,
            elapsed_ms=125_000,
            completed_at=1_700_000_000.0,
            launch_session_id="session-a",
            failure_code=None,
        ),
        output,
    )

    recovered = store.resolve(restarted, process_id, cursor=0, max_bytes=51_200)
    assert recovered is not None
    assert recovered.state is ProcessState.EXITED
    assert recovered.output == "build complete\n"
    assert recovered.durable_output is True
    assert recovered.full_output_path is not None
    assert store.resolve(foreign, process_id, cursor=0, max_bytes=51_200) is None
```

- [ ] **Step 2: Run the completion tests to witness the missing API**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_completions.py
```

Expected: collection fails because `appv231.coding_agent.processes.completions`
does not exist.

- [ ] **Step 3: Extend stable process contracts**

Add these fields and types to `types.py` while retaining defaults for current
callers:

```python
@dataclass(frozen=True)
class ProcessOwner:
    app_instance_id: str
    workspace_key: str
    origin: Literal["agent", "user"] = "agent"

    @property
    def persistence_scope(self) -> tuple[str, str]:
        return (self.workspace_key, self.origin)


@dataclass(frozen=True)
class ProcessCompletionRecord:
    session_id: str
    state: ProcessState
    exit_code: int | None
    output_size: int
    elapsed_ms: int
    completed_at: float
    launch_session_id: str | None
    failure_code: str | None


@dataclass(frozen=True)
class ProcessLaunchRequest:
    command: str
    cwd: str
    env: Mapping[str, str]
    shell_path: str
    tty: bool = False
    rows: int = 24
    cols: int = 80
    timeout_seconds: float | None = None
    launch_session_id: str | None = None


@dataclass(frozen=True)
class ProcessSnapshot:
    session_id: str
    state: ProcessState
    output: str
    cursor: int
    next_cursor: int
    output_size: int
    exit_code: int | None
    tty: bool
    elapsed_ms: int
    command: str = ""
    cwd: str = ""
    suggested_poll_delay_ms: int = DEFAULT_PROCESS_POLL_DELAY_MS
    durable_output: bool = False
    full_output_path: str | None = None
    failure_code: str | None = None


class ProcessWaitCancelledError(ProcessSessionError):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"Process wait cancelled: {session_id}")
        self.session_id = session_id
```

Place `ProcessWaitCancelledError` beside the existing process-session errors,
after `ProcessSessionError`, while keeping the dataclasses with the other stable
contracts.

Extend `as_details()` only when terminal metadata is present:

```python
if self.durable_output:
    details["durableOutput"] = True
if self.full_output_path is not None:
    details["fullOutputPath"] = self.full_output_path
if self.failure_code is not None:
    details["failureCode"] = self.failure_code
```

- [ ] **Step 4: Implement transactional completion persistence and lookup**

Implement a versioned SQLite index with strict columns rather than deserializing
arbitrary objects. Keep output in separate atomic files so large text never
passes through the database.

```python
class ProcessCompletionStore:
    def __init__(
        self,
        root: str | Path,
        *,
        retention_seconds: float = 7 * 24 * 60 * 60,
        max_total_bytes: int = 256 * 1024 * 1024,
        max_records: int = 10_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.objects.chmod(0o700)
        self.index_path = self.root / "index.sqlite3"
        self.retention_seconds = max(0.0, retention_seconds)
        self.max_total_bytes = max(0, max_total_bytes)
        self.max_records = max(0, max_records)
        self.clock = clock
        self._lock = threading.RLock()
        self._connection = self._open_versioned_index()

    def persist(
        self,
        owner: ProcessOwner,
        record: ProcessCompletionRecord,
        sanitized_output: Path,
    ) -> Path:
        self._validate_record_fits_retention(record)
        directory = self._scope_directory(owner)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_path = directory / f"{record.session_id}-{uuid.uuid4().hex}.log"
        _atomic_copy_0600(sanitized_output, output_path)
        try:
            with self._transaction(immediate=True) as connection:
                self._insert_record(connection, owner, record, output_path)
                stale_paths = self._prune_transaction(
                    connection,
                    keep=(self._workspace_digest(owner), owner.origin, record.session_id),
                )
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        self._unlink_pruned_objects(stale_paths)
        return output_path
```

Use a primary key on `(workspace_digest, origin, session_id)`, an index on
`completed_at`, and a one-row store-state table for exact total output bytes and
record count. Configure `busy_timeout`, `synchronous=FULL`, foreign keys, and a
fixed schema version. Ensure database, journal, WAL, and output files are mode
0600 after creation. `resolve()` validates workspace digest, origin,
process-ID format, terminal state, numeric bounds, relative object path, and
actual output size before constructing a snapshot.

Reject persistence before copying when retention is disabled, `max_records <
1`, or one output cannot fit `max_total_bytes`. Use a unique object suffix so a
duplicate process key can fail without overwriting/deleting the first record.
Pruning excludes the just-inserted key for its transaction; after older
eviction the prevalidated new record necessarily fits.

`inspect()` performs the same authorization/metadata validation but returns an
empty-output snapshot without opening the log. `inspect_many()` accepts at most
64 unique valid IDs and resolves them in one parameterized query while
preserving caller order. `tail_snapshot()` applies the
same line/byte truncation contract directly to the authorized durable log so a
new app instance can produce one bounded terminal result without replaying the
whole file. Read backward in bounded binary blocks, stop after satisfying both
tail limits, and repair only the leading UTF-8 boundary; never call
`read_bytes()`/`read_text()` on the complete artifact. `close()` acquires the
store lock, marks the instance closed, and
rejects future writes while leaving durable records intact for the next app
instance. A corrupt row or missing/mismatched log is removed under transaction;
a corrupt index is atomically moved aside with `-wal`/`-shm` companions and
recreated empty. Never interpolate identifiers or values into SQL.

- [ ] **Step 5: Implement indexed TTL, size, count, and orphan pruning**

```python
def _prune_transaction(self, connection: sqlite3.Connection) -> tuple[Path, ...]:
    expired = self._delete_before(connection, self.clock() - self.retention_seconds)
    state = self._store_state(connection)
    evicted = list(expired)
    while state.total_output_bytes > self.max_total_bytes or state.record_count > self.max_records:
        oldest = self._oldest_record(connection)
        if oldest is None:
            break
        self._delete_record(connection, oldest)
        evicted.append(self.root / oldest.relative_output_path)
        state = state.with_removed(oldest.output_size)
    self._write_store_state(connection, state)
    return tuple(evicted)
```

All retention queries use indexes or the constant-size state row; `persist`
must not call `rglob`, `glob`, or scan every metadata record. Serialize writes
with SQLite plus a small interprocess maintenance lock for index quarantine and
orphan cleanup. Delete only unindexed `.log` files older than the orphan grace
period while holding that lock, so another process cannot lose a just-copied,
not-yet-committed output object.

- [ ] **Step 6: Run completion and process-contract tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_completions.py
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_output.py \
  appV2.3.1/tests/test_process_tools.py \
  -k "snapshot or output"
```

Expected: selected tests pass; existing details remain unchanged unless durable
terminal metadata is present.

- [ ] **Step 7: Commit the completion layer**

```bash
git add appV2.3.1/appv231/coding_agent/processes/types.py appV2.3.1/appv231/coding_agent/processes/completions.py appV2.3.1/appv231/coding_agent/processes/__init__.py appV2.3.1/tests/test_process_completions.py
git commit -m "feat(appv231): persist terminal process results"
```

### Task 2: Host-Side Terminal Wait and Completion Fallback

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/processes/service.py`
- Extend: `appV2.3.1/tests/test_process_service.py`
- Extend: `appV2.3.1/tests/test_process_completions.py`

**Interfaces:**
- Consumes: Task 1 `ProcessCompletionStore` and completion contracts.
- Produces: `ProcessSessionService.wait_terminal(...)` and durable fallback from `poll`/`wait_terminal`/`tail_snapshot`.
- Changes: service construction accepts `completion_store` and `wall_clock`.
- Guarantees: output-only events do not return a terminal wait; cancellation stops waiting but not the detached process.

- [ ] **Step 1: Add failing chatty-wait and cancellation tests**

```python
def test_wait_terminal_ignores_output_updates_until_terminal(service, owner, fake_factory) -> None:
    started = service.start(owner, request("chatty"), fake_factory, yield_time_ms=0)
    result: list[ProcessSnapshot] = []
    waiter = threading.Thread(
        target=lambda: result.append(service.wait_terminal(owner, started.session_id, 0, wait_ms=5_000))
    )
    waiter.start()
    fake_factory.last.emit_output(b"one\n")
    fake_factory.last.emit_output(b"two\n")
    time.sleep(0.05)
    assert waiter.is_alive()
    fake_factory.last.exit(0)
    waiter.join(timeout=1)
    assert result[0].state is ProcessState.EXITED
    assert result[0].output == "one\ntwo\n"


def test_wait_cancellation_does_not_kill_detached_job(service, owner, fake_factory) -> None:
    started = service.start(owner, request("long"), fake_factory, yield_time_ms=0)
    signal = AbortSignal()
    signal.abort()
    with pytest.raises(ProcessWaitCancelledError):
        service.wait_terminal(owner, started.session_id, 0, wait_ms=60_000, signal=signal)
    assert fake_factory.last.signals == []
    assert service.poll(owner, started.session_id, 0, wait_ms=0).state is ProcessState.RUNNING
```

Add a fake-clock test where a terminal record is pruned from memory and resolved
through `ProcessCompletionStore` with final output intact.

- [ ] **Step 2: Run the new service tests to witness missing behavior**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_service.py -k "wait_terminal or durable_fallback"
```

Expected: failures because `wait_terminal` and completion fallback do not exist.

- [ ] **Step 3: Inject the completion store and persist before terminal events**

```python
def __init__(
    self,
    *,
    directory: str | Path | None = None,
    completion_store: ProcessCompletionStore | None = None,
    wall_clock: Callable[[], float] = time.time,
    max_active_per_owner: int = 4,
    max_active_total: int = 16,
    **existing_options,
) -> None:
    self._completion_store = completion_store
    self._wall_clock = wall_clock
    self._max_active_per_owner = max(1, max_active_per_owner)
    self._max_active_total = max(1, max_active_total)
```

Before terminal event emission, finish and persist output:

```python
def _persist_completion(self, record: _ManagedProcess) -> None:
    if self._completion_store is None:
        return
    completion = ProcessCompletionRecord(
        session_id=record.session_id,
        state=record.state,
        exit_code=record.exit_code,
        output_size=record.output.size,
        elapsed_ms=self._elapsed_ms(record),
        completed_at=self._wall_clock(),
        launch_session_id=record.request.launch_session_id,
        failure_code=record.failure_code,
    )
    try:
        record.full_output_path = str(self._completion_store.persist(record.owner, completion, record.output.path))
        record.durable_output = True
    except Exception as error:
        record.persistence_error = _bounded_error_code(error)
```

- [ ] **Step 4: Implement terminal-only waiting**

```python
def wait_terminal(
    self,
    owner: ProcessOwner,
    session_id: str,
    cursor: int,
    *,
    wait_ms: int = 60_000,
    max_bytes: int = 51_200,
    signal=None,
    on_update=None,
) -> ProcessSnapshot:
    self._validate_long_wait(wait_ms)
    try:
        with self._record_call(owner, session_id) as record:
            deadline = self._clock() + wait_ms / 1000
            while True:
                with record.condition:
                    if record.state.terminal:
                        return self._snapshot(record, cursor, max_bytes)
                    if signal is not None and getattr(signal, "aborted", False):
                        raise ProcessWaitCancelledError(session_id)
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return self._snapshot(record, cursor, max_bytes)
                    record.condition.wait(min(remaining, 0.1))
                self._emit_wait_update(record, cursor, max_bytes, on_update)
    except ProcessNotFoundError:
        recovered = self._resolve_completion(owner, session_id, cursor, max_bytes)
        if recovered is not None:
            return recovered
        raise
```

Throttle updates to ten per second and invoke callbacks outside the condition.

- [ ] **Step 5: Add durable fallback to ordinary poll**

```python
def _resolve_completion(
    self, owner: ProcessOwner, session_id: str, cursor: int, max_bytes: int
) -> ProcessSnapshot | None:
    if self._completion_store is None:
        return None
    return self._completion_store.resolve(owner, session_id, cursor=cursor, max_bytes=max_bytes)
```

Catch only `ProcessNotFoundError` from `poll`/`wait_terminal`. Mutating actions
continue to reject durable terminal records. Extend `tail_snapshot` and
`export_output` with the same owner-authorized completion-store fallback;
neither method may reopen a private live spool after eviction.

- [ ] **Step 6: Run service tests repeatedly**

```bash
for run in 1 2 3 4 5; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    appV2.3.1/tests/test_process_service.py appV2.3.1/tests/test_process_completions.py \
    -k "wait_terminal or completion or ttl or poll" || exit 1
done
```

Expected: five passes with no live waiter threads.

- [ ] **Step 7: Commit terminal wait and fallback**

```bash
git add appV2.3.1/appv231/coding_agent/processes/service.py appV2.3.1/tests/test_process_service.py appV2.3.1/tests/test_process_completions.py
git commit -m "feat(appv231): await managed process completion"
```

### Task 3: Output Failure, Live-Spool Budgets, and Owner-Aware Quotas

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/processes/service.py`
- Modify: `appV2.3.1/appv231/coding_agent/processes/types.py`
- Modify: `appV2.3.1/appv231/coding_agent/processes/output.py`
- Extend: `appV2.3.1/tests/test_process_service.py`
- Extend: `appV2.3.1/tests/test_process_output.py`

**Interfaces:**
- Consumes: existing reader/monitor state machine.
- Produces: deterministic `output_failure`/`output_limit` metadata, bounded live-spool accounting, and scoped reservation counters.
- Guarantees: a command cannot grow sanitized live output without bound; a hidden workspace/user job cannot exhaust another scope's four slots; sixteen active jobs remain the app-wide safety limit.

- [ ] **Step 1: Add failing spool-error and quota-isolation regressions**

```python
def test_spool_failure_stops_process_and_publishes_failed(tmp_path, owner, fake_factory, monkeypatch) -> None:
    service = ProcessSessionService(directory=tmp_path)

    def fail_append(self, data: bytes) -> None:
        raise OSError("simulated full spool")

    monkeypatch.setattr(SanitizedOutputSpool, "append", fail_append)
    started = service.start(owner, request("writer"), fake_factory, yield_time_ms=0)
    fake_factory.last.emit_output(b"data")
    terminal = wait_for_terminal(service, owner, started.session_id)

    assert terminal.state is ProcessState.FAILED
    assert terminal.failure_code == "output_failure"
    assert set(fake_factory.last.signals) & {"terminate", "kill"}


def test_active_limit_is_per_owner_scope_with_global_ceiling(tmp_path, fake_factory) -> None:
    service = ProcessSessionService(directory=tmp_path, max_active_per_owner=1, max_active_total=3)
    left = ProcessOwner("app", "/left", "agent")
    right = ProcessOwner("app", "/right", "agent")
    service.start(left, request("left"), fake_factory, yield_time_ms=0)

    with pytest.raises(ProcessLimitError, match="owner scope"):
        service.start(left, request("left-two"), fake_factory, yield_time_ms=0)

    assert service.start(right, request("right"), fake_factory, yield_time_ms=0).state is ProcessState.RUNNING


def test_process_output_limit_fails_only_the_producer_and_preserves_prefix(tmp_path, owner, fake_factory) -> None:
    service = ProcessSessionService(
        directory=tmp_path,
        max_spool_bytes_per_process=8,
        max_live_spool_bytes=32,
    )
    started = service.start(owner, request("chatty"), fake_factory, yield_time_ms=0)
    fake_factory.last.emit_output(b"123456789")
    terminal = wait_for_terminal(service, owner, started.session_id)

    assert terminal.state is ProcessState.FAILED
    assert terminal.failure_code == "output_limit"
    assert terminal.output_size == 8
    assert terminal.state is not ProcessState.TIMED_OUT
```

- [ ] **Step 2: Run focused regressions and confirm both fail**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_service.py appV2.3.1/tests/test_process_output.py \
  -k "spool_failure or output_limit or per_owner_scope"
```

Expected: spool failure is incorrectly observed as exited or empty, output is
unbounded, and the second workspace is rejected by the global counter.

- [ ] **Step 3: Make reader failure first-wins terminal failure**

Add a bounded failure code and wake the monitor. Never expose the raw OS error
through model details.

```python
def _claim_output_failure(
    self,
    record: _ManagedProcess,
    error: Exception,
    *,
    failure_code: str = "output_failure",
) -> None:
    with record.condition:
        if record.state.terminal or record.failure_code is not None:
            return
        record.failure_code = failure_code
        record.output_error = type(error).__name__[:80]
        record.state = ProcessState.STOPPING
        record.wakeup.set()
        record.condition.notify_all()
    self._safe_signal(record, "terminate")
```

In `_monitor`, map failure before natural exit:

```python
if record.failure_code is not None:
    record.state = ProcessState.FAILED
elif record.stop_cause is StopCause.TIMEOUT:
    record.state = ProcessState.TIMED_OUT
elif record.stop_cause is not None:
    record.state = ProcessState.TERMINATED
else:
    record.state = ProcessState.EXITED
```

- [ ] **Step 4: Enforce bounded sanitized live output**

Give `SanitizedOutputSpool` a configurable hard byte cap and an injected
app-wide reservation budget. Reservation occurs after decoding/sanitizing but
before the file write. If the next sanitized text crosses either limit, write
only the largest valid UTF-8 prefix that was reserved, then raise
`ProcessOutputLimitError`. This makes both limits hard bounds rather than
after-write observations.

```python
DEFAULT_MAX_PROCESS_SPOOL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_LIVE_SPOOL_BYTES = 512 * 1024 * 1024


class LiveSpoolBudget:
    def __init__(self, limit: int) -> None:
        self._limit = max(0, limit)
        self._used = 0
        self._lock = threading.Lock()

    def reserve_up_to(self, requested: int) -> int:
        with self._lock:
            granted = min(max(0, requested), self._limit - self._used)
            self._used += granted
            return granted

    def release(self, count: int) -> None:
        with self._lock:
            if count < 0 or count > self._used:
                raise RuntimeError("live-spool accounting invariant violated")
            self._used -= count
```

Defaults are 64 MiB per process and 512 MiB app-wide. Account/deaccount bytes
exactly once as records are created, terminal spools are evicted, and startup
fails. If an OS write fails after reservation, release every unwritten byte;
release the retained byte count exactly once when that spool is removed. Never
hold the service-record lock while closing a spool, which avoids a
service-lock/spool-lock inversion. Add invariant tests for partial writes,
concurrent readers, and terminal pruning. Under budget pressure, evict the
oldest already-durable terminal live records before limiting an active
producer; never pressure-evict a running or non-durable record. The reader maps
`ProcessOutputLimitError` to `output_limit` and other append/finish errors to
`output_failure`. A long quiet command is unaffected; only captured output
volume trips this guard.

- [ ] **Step 5: Reserve capacity by immutable owner scope**

```python
def _reserve_start(self, owner: ProcessOwner) -> None:
    self._prune()
    with self._lock:
        if self._closed:
            raise ProcessClosedError("Process service is closed")
        active_total = sum(not item.state.terminal for item in self._records.values()) + self._starting
        if active_total >= self._max_active_total:
            raise ProcessLimitError(f"Reached app-wide active process limit of {self._max_active_total}")
        active_owner = sum(
            not item.state.terminal and item.owner == owner for item in self._records.values()
        ) + self._starting_by_owner[owner]
        if active_owner >= self._max_active_per_owner:
            raise ProcessLimitError(
                f"Reached owner scope active process limit of {self._max_active_per_owner}"
            )
        self._starting += 1
        self._starting_by_owner[owner] += 1
```

Release both counters on spawn success and every failure path.

- [ ] **Step 6: Run output, service race, and quota tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_output.py appV2.3.1/tests/test_process_service.py \
  -k "failure or output_limit or spool_budget or limit or owner or concurrent or close"
```

Expected: selected tests pass and the active-job no-eviction contract remains
green.

- [ ] **Step 7: Commit output and quota correctness**

```bash
git add appV2.3.1/appv231/coding_agent/processes/service.py appV2.3.1/appv231/coding_agent/processes/types.py appV2.3.1/appv231/coding_agent/processes/output.py appV2.3.1/tests/test_process_service.py appV2.3.1/tests/test_process_output.py
git commit -m "fix(appv231): bound process output and quotas"
```

### Task 4: Descendant-Aware Local Containment

**Files:**
- Modify: `appV2.3.1/pyproject.toml`
- Modify: `appV2.3.1/appv231/coding_agent/processes/transport.py`
- Create: `appV2.3.1/appv231/coding_agent/processes/containment.py`
- Modify: `appV2.3.1/appv231/coding_agent/processes/local.py`
- Modify: `appV2.3.1/appv231/coding_agent/processes/service.py`
- Extend: `appV2.3.1/tests/test_process_local.py`
- Extend: `appV2.3.1/tests/test_process_service.py`

**Interfaces:**
- Produces: `ProcessTreeController.signal(signal_name)` and `ProcessTransport.signal_tree(signal_name)`.
- Consumes: internal PID from local `subprocess.Popen`; PID never crosses the transport boundary.
- Guarantees: escaped descendants observable in the process tree receive TERM/KILL during timeout, explicit control, and app close.

- [ ] **Step 1: Add a failing escaped-descendant regression**

```python
@pytest.mark.skipif(os.name != "posix", reason="process-tree containment requires POSIX")
def test_timeout_kills_descendant_that_calls_setsid(service, owner, tmp_path) -> None:
    pid_file = tmp_path / "escaped.pid"
    child = (
        "import os,time; os.setsid(); "
        f"open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(60)"
    )
    parent = f"import subprocess,time; subprocess.Popen(['python','-c',{child!r}]); time.sleep(60)"
    started = service.start(
        owner,
        request(f"python -c {shlex.quote(parent)}", cwd=tmp_path, timeout_seconds=0.25),
        local_factory(),
        yield_time_ms=0,
    )
    terminal = wait_for_terminal(service, owner, started.session_id)
    escaped_pid = int(pid_file.read_text())

    assert terminal.state is ProcessState.TIMED_OUT
    assert wait_until(lambda: not psutil.pid_exists(escaped_pid), timeout=3)
```

Add a deterministic fake-psutil regression where a tracked PID exits and is
reused with a different `create_time`; signaling must skip the replacement
process. PID reuse must never turn cleanup into a signal against an unrelated
job.

- [ ] **Step 2: Run the escaped-descendant test and confirm survival**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_local.py -k setsid
```

Expected: failure because process-group signaling does not reach the escaped
child.

- [ ] **Step 3: Add the process-tree dependency and transport contract**

Add `"psutil>=6.1"` to `[project].dependencies` in sorted position. Extend the
transport protocol and all fakes:

```python
class ProcessTransport(Protocol):
    # Existing methods remain unchanged.
    def signal_tree(self, signal_name: SignalName) -> None: ...
```

- [ ] **Step 4: Implement descendant tracking without leaking PIDs**

```python
@dataclass(frozen=True)
class _TrackedProcess:
    pid: int
    create_time: float
    depth: int


class ProcessTreeController:
    def __init__(self, root_pid: int) -> None:
        self._root_pid = root_pid
        root = psutil.Process(root_pid)
        self._known: dict[int, _TrackedProcess] = {
            root_pid: _TrackedProcess(root_pid, root.create_time(), 0)
        }
        self._lock = threading.Lock()

    def refresh(self) -> None:
        try:
            root = psutil.Process(self._root_pid)
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            descendants = []
        observed: list[_TrackedProcess] = []
        for process in descendants:
            try:
                depth = 1 + sum(parent.pid != self._root_pid for parent in process.parents())
                observed.append(_TrackedProcess(process.pid, process.create_time(), depth))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        with self._lock:
            self._known.update((item.pid, item) for item in observed)

    def signal(self, signal_name: SignalName) -> None:
        self.refresh()
        selected = {
            "interrupt": signal.SIGINT,
            "terminate": signal.SIGTERM,
            "kill": signal.SIGKILL,
        }[signal_name]
        with self._lock:
            identities = tuple(sorted(self._known.values(), key=lambda item: item.depth, reverse=True))
        for identity in identities:
            try:
                process = psutil.Process(identity.pid)
                if process.create_time() != identity.create_time:
                    continue
                process.send_signal(selected)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
```

Refresh from the monitor at least every 100 ms while running. Local transports
first signal the original group, then tracked descendants deepest-first, and
rescan before KILL. Keep identity data private to the transport; never expose
PID/create-time values through snapshots or events.

- [ ] **Step 5: Route lifecycle signals through `signal_tree`**

Replace service calls to `signal_group` with `signal_tree`. Keep
`signal_group` as an internal local helper and preserve first-wins causes.

- [ ] **Step 6: Run local process-tree tests in source and Linux container**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_local.py -k "descendant or setsid or timeout or close"
docker build --no-cache -f Dockerfile.appv231.release -t appv231-process-v2-test .
docker run --rm \
  -v "$PWD/appV2.3.1/tests:/tests:ro" \
  --entrypoint python appv231-process-v2-test \
  -m pytest -q -p no:cacheprovider /tests/test_process_local.py -k "descendant or setsid"
```

Expected: source tests pass where supported; Linux tests prove same-group and
`setsid` descendants are gone.

- [ ] **Step 7: Commit local containment**

```bash
git add appV2.3.1/pyproject.toml appV2.3.1/appv231/coding_agent/processes/transport.py appV2.3.1/appv231/coding_agent/processes/containment.py appV2.3.1/appv231/coding_agent/processes/local.py appV2.3.1/appv231/coding_agent/processes/service.py appV2.3.1/tests/test_process_local.py appV2.3.1/tests/test_process_service.py
git commit -m "fix(appv231): terminate escaped process descendants"
```

### Task 5: Sequential `process.wait` Tool and Terminal Artifacts

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/artifacts.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/process.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/bash.py`
- Modify: `appV2.3.1/appv231/coding_agent/policies/tool_guardrails.py`
- Extend: `appV2.3.1/tests/test_process_tools.py`
- Extend: `appV2.3.1/tests/test_coding_policy.py`
- Extend: `appV2.3.1/tests/test_output_spool.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Consumes: `ProcessSessionService.wait_terminal`, durable tail lookup, `ArtifactRegistry`, and terminal artifact fields.
- Produces: `process(action="wait")`, `wait_time_ms`, borrowed durable artifacts, and explicit sequential execution mode.
- Guarantees: one long host wait produces one bounded terminal tool result; truncated output is read-authorized without letting session cleanup delete completion data; prompt text prefers wait when final output is required.

- [ ] **Step 1: Add failing tool-contract tests**

```python
def test_process_wait_uses_terminal_wait_and_streams_updates(managed_tools) -> None:
    bash, process, transport = managed_tools
    started = bash.execute("b1", {"command": "build", "yield_time_ms": 0})
    session_id = started.details["sessionId"]
    updates: list[str] = []

    def finish() -> None:
        transport.emit_output(b"progress\n")
        transport.exit(0)

    threading.Timer(0.05, finish).start()
    result = process.execute(
        "p1",
        {
            "action": "wait",
            "session_id": session_id,
            "cursor": started.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
        on_update=lambda update: updates.append(text(update)),
    )

    assert result.details["status"] == "exited"
    assert result.details["durableOutput"] is True
    assert updates
    assert process.execution_mode == "sequential"
```

Add validation tests for 999/900001 ms, forbidden wait fields, cancellation,
large detached output, durable artifact path, and a scripted provider transcript
with one wait despite multiple output updates. The 2 MiB case must assert
`nextCursor == outputSize`, bounded tail text, truncation metadata, a resolvable
`artifactId`, exact full artifact length, and that `ArtifactRegistry.close()`
does not delete the borrowed completion file. Reopen a new app/session and prove
a new owner-authorized wait registers a new readable artifact reference.

- [ ] **Step 2: Run the wait-tool tests to witness missing action**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_tools.py -k "process_wait or detached_output or execution_mode"
```

Expected: failures because `wait`, `wait_time_ms`, artifact details, and
sequential mode are absent.

- [ ] **Step 3: Add the provider-safe wait schema and execution**

```python
PROCESS_ACTIONS = ("poll", "wait", "write", "resize", "interrupt", "terminate", "kill", "list")
PROCESS_SCHEMA["properties"]["wait_time_ms"] = {
    "type": "integer",
    "minimum": 1_000,
    "maximum": 900_000,
}
_ACTION_FIELDS["wait"] = {"action", "session_id", "cursor", "wait_time_ms", "max_bytes"}
```

In `_execute_process`:

```python
elif action == "wait":
    snapshot = service.wait_terminal(
        owner,
        session_id,
        args["cursor"],
        wait_ms=args.get("wait_time_ms", 60_000),
        max_bytes=args.get("max_bytes", 51_200),
        signal=signal,
        on_update=(lambda update: on_update(_snapshot_result(update))) if on_update else None,
    )
    if snapshot.state.terminal:
        return _terminal_process_result(service, owner, snapshot, artifacts)
```

Pass `artifacts` from `create_process_tool_definition` into execution and set
`execution_mode="sequential"` on `ToolDefinition`. A host-deadline return that
is still running uses the ordinary snapshot result and keeps its exact cursor.

- [ ] **Step 4: Register durable output as a borrowed artifact**

Extend `ArtifactRef`/`ArtifactRegistry.register` with a defaulted ownership flag
such as `remove_on_close=True`. Existing command spools retain the default.
Durable process results register with `remove_on_close=False`, and registry
close unlinks only owned refs.

Pass the session's `ArtifactRegistry` into
`create_process_tool_definition(service, owner, artifacts)`. When a wait reaches
terminal state, obtain the owner-authorized `service.tail_snapshot`, set
`nextCursor` to the terminal `outputSize`, attach truncation details, and
register `snapshot.full_output_path` as a borrowed `process-output` artifact.
Return both `fullOutputPath` and `artifactId`. Apply the same helper to managed
bash that reaches terminal state; keep the current exported-copy fallback only
when durable persistence failed.

`process.poll` keeps its exact incremental cursor slices. Only terminal `wait`
collapses a large unread range to one bounded tail plus artifact, preventing a
large completed log from forcing dozens of model polling iterations.

- [ ] **Step 5: Replace polling guidance with dual-mode guidance**

Use these exact rules:

```python
[
    "Use the exact nextCursor returned by bash/process so output is not repeated.",
    "Use process.poll only for interactive input, quick status checks, or intentionally incremental output.",
    "When a command result is required, continue independent work first and then use process.wait; wait ignores output-only wakeups and does not set the command timeout.",
    "Leave a process detached only for a requested server/watcher or when its result is not required.",
    "Set bash.timeout only when an actual execution deadline is intended.",
]
```

Running result footers recommend `process.wait` when the final result is
required while preserving `nextCursor` and quick-poll delay. A terminal
`failureCode=output_limit` footer says the command was stopped after reaching
the sanitized-output budget and explicitly does not call it a timeout.

- [ ] **Step 6: Keep guardrails action-aware**

Zero/short polls remain idempotent observations; cooperative poll/wait is a
host wait; controls remain mutations. `wait` does not enter generic
no-progress blocking simply because its host deadline elapsed.

```python
if tool_name == "process":
    action = args.get("action")
    if action == "poll":
        wait_ms = args.get("yield_time_ms", COOPERATIVE_PROCESS_POLL_WAIT_MS)
        return isinstance(wait_ms, int) and wait_ms < COOPERATIVE_PROCESS_POLL_WAIT_MS
    return action == "list"
```

- [ ] **Step 7: Run tool, artifact-policy, and loop regressions**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_tools.py \
  appV2.3.1/tests/test_coding_policy.py \
  appV2.3.1/tests/test_output_spool.py \
  appV2.3.1/tests/test_coding_agent.py \
  -k "process or artifact or output_spool or bash_yields or iteration or tool_loop_guardrail"
```

Expected: pass with no redzone modification; borrowed completion files survive
registry close while ordinary owned temp artifacts are still removed.

- [ ] **Step 8: Commit the model-facing wait contract**

```bash
git add appV2.3.1/appv231/coding_agent/artifacts.py appV2.3.1/appv231/coding_agent/tools/process.py appV2.3.1/appv231/coding_agent/tools/bash.py appV2.3.1/appv231/coding_agent/policies/tool_guardrails.py appV2.3.1/tests/test_process_tools.py appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_output_spool.py appV2.3.1/tests/test_coding_agent.py
git commit -m "feat(appv231): add host-side process wait"
```

### Task 6: App-Owned Completion Wiring and Compatibility

**Files:**
- Modify: `appV2.3.1/appv231/app.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Extend: `appV2.3.1/tests/test_app_integration.py`
- Extend: `appV2.3.1/tests/test_cli.py`

**Interfaces:**
- Consumes: agent directory, completion store, shared process service, session ID.
- Produces: one completion store and service per `CodingApp`; launch requests carry internal launch-session identity.
- Guarantees: replacement sessions share live service/store, new app instances recover terminal results only, and direct sessions stay synchronous.

- [ ] **Step 1: Add failing app restart and compatibility tests**

```python
def test_new_app_instance_recovers_terminal_result_but_not_running_process(tmp_path: Path) -> None:
    first = build_app(tmp_path)
    result = run_managed_command_to_completion(first, "printf done")
    process_id = result.details["sessionId"]
    first.close()

    second = build_app(tmp_path)
    recovered = second.process_service.poll(second.process_owner(), process_id, 0, wait_ms=0)
    assert recovered.state is ProcessState.EXITED
    assert recovered.output == "done"


def test_direct_agent_session_still_uses_synchronous_bash(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    assert "process" not in session.get_active_tool_names()
```

- [ ] **Step 2: Run integration tests and confirm missing recovery**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_cli.py -k "completion or process_service or direct_agent"
```

Expected: restart recovery fails because CodingApp creates only a temp service.

- [ ] **Step 3: Construct durable store before shared service**

```python
process_result_root = Path(self._agent_dir) / "process-results"
self.process_completion_store = ProcessCompletionStore(process_result_root)
self.process_service = ProcessSessionService(
    completion_store=self.process_completion_store,
    max_active_per_owner=4,
    max_active_total=16,
)
```

Inject both into every replacement session. Add
`launch_session_id=self.session_id or None` to managed launch requests; it is
internal and absent from provider details. Extend the app owner factory without
changing its default:

```python
def process_owner(self, origin: Literal["agent", "user"] = "agent") -> ProcessOwner:
    return ProcessOwner(self._app_instance_id, self._workspace_key, origin)
```

The model-facing tools continue to receive only the default `agent` owner.

- [ ] **Step 4: Preserve cleanup ordering**

```python
try:
    self.process_service.close()
except Exception as error:
    first_error = first_error or error
try:
    self.process_completion_store.close()
except Exception as error:
    first_error = first_error or error
```

The completion store does not delete durable terminal records on close.

- [ ] **Step 5: Run app, CLI, and legacy tool suites**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_app_integration.py \
  appV2.3.1/tests/test_cli.py \
  appV2.3.1/tests/test_process_tools.py \
  appV2.3.1/tests/test_coding_agent.py \
  -k "process or bash or close or session_replacement"
```

Expected: pass, including custom operations and subagent synchronous behavior.

- [ ] **Step 6: Commit app wiring**

```bash
git add appV2.3.1/appv231/app.py appV2.3.1/appv231/coding_agent/agent_session.py appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_cli.py
git commit -m "feat(appv231): wire durable process completion"
```

### Task 7: Runtime Plan Acceptance Gate

**Files:**
- Verify only; no production edits expected.

**Interfaces:**
- Consumes: all tasks in this plan.
- Produces: evidence that runtime is ready for session-context and TUI plans.

- [ ] **Step 1: Run all process tests five times**

```bash
for run in 1 2 3 4 5; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    appV2.3.1/tests/test_process_output.py \
    appV2.3.1/tests/test_process_completions.py \
    appV2.3.1/tests/test_process_service.py \
    appV2.3.1/tests/test_process_local.py \
    appV2.3.1/tests/test_process_tools.py || exit 1
done
```

Expected: five passes with no leaked waiter, monitor, reader, or input threads.

- [ ] **Step 2: Run related integration and policy tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_app_integration.py \
  appV2.3.1/tests/test_coding_policy.py \
  appV2.3.1/tests/test_coding_agent.py \
  -k "process or bash or iteration_limit or tool_loop_guardrail"
```

Expected: pass with unchanged core iteration-limit semantics.

- [ ] **Step 3: Prove redzone integrity**

```bash
if git diff --name-only 96b38b9..HEAD | rg '^appV2\.3\.1/appv231/(agent|compaction)/'; then
  echo 'redzone modified' >&2
  exit 1
fi
```

Expected: no output and exit zero.

- [ ] **Step 4: Record the runtime checkpoint**

```bash
git status --short
git log -7 --oneline
```

Expected: intentional commits plus unrelated pre-existing worktree entries; do
not create an empty checkpoint commit.
