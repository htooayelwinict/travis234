# appv231 Managed Process Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Codex-like foreground-yield and managed background command sessions to the appv231 coding profile without changing the existing agent loop.

**Architecture:** `CodingApp` owns one workspace-scoped `ProcessSessionService` and injects it into replacement coding sessions. Managed `bash` returns an ordinary running result after its yield window, while a companion `process` tool polls and controls the retained subprocess. The existing agent loop and compaction packages remain untouched.

**Tech Stack:** Python 3.13, `subprocess`, `threading`, `tempfile`, POSIX `pty`/`termios`/`fcntl`, existing `ExecutionBackend`, coding policies, differential TUI, pytest, Docker/npm launcher.

## Global Constraints

- Do not modify any file under `appV2.3.1/appv231/agent/`.
- Do not modify any file under `appV2.3.1/appv231/compaction/`.
- The 10,000 ms default is a foreground yield window, never a kill deadline.
- Omitted `timeout` means the job runs until natural exit, explicit control, or app shutdown.
- Keep direct SDK/custom `BashOperations` and internal subagent bash synchronous.
- Preserve existing command prefix, shell path, spawn hook, execution backend, extension hooks, and package consent.
- Process handles are opaque, workspace-scoped, and never expose OS PIDs or environment values.
- Use red-green TDD for every production change and commit only the files in that task.
- Do not push, publish, release, or modify unrelated worktree files.

---

### Task 1: Process Contracts and Cursor-Safe Output

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/processes/__init__.py`
- Create: `appV2.3.1/appv231/coding_agent/processes/types.py`
- Create: `appV2.3.1/appv231/coding_agent/processes/output.py`
- Create: `appV2.3.1/tests/test_process_output.py`

**Interfaces:**
- Produces: `ProcessState`, `StopCause`, `ProcessOwner`, `ProcessLaunchRequest`, `OutputSlice`, `ProcessSnapshot`, `ProcessEvent`, and typed process errors.
- Produces: `SanitizedOutputSpool.append(data)`, `read(cursor, max_bytes)`, `finish()`, `export_copy()`, and `close()`.
- Guarantees: cursors are byte offsets in sanitized UTF-8, reads are deterministic, and service spool paths are private.

- [ ] **Step 1: Write failing output and type-contract tests**

Cover split UTF-8 code points, invalid bytes, CSI/OSC/C0 removal, concurrent
append/read, repeated cursor determinism, max-byte boundaries, finish behavior,
mode bits, and private export copies.

```python
def test_output_cursor_is_stable_across_split_utf8_and_osc(tmp_path):
    spool = SanitizedOutputSpool(tmp_path)
    encoded = "before \N{SNOWMAN} after\n".encode()
    spool.append(encoded[:9])
    spool.append(encoded[9:] + b"\x1b]52;c;c2VjcmV0\x07")

    first = spool.read(0, 8)
    second = spool.read(first.next_cursor, 512)

    assert (first.text + second.text) == "before \N{SNOWMAN} after\n"
    assert spool.read(0, 8) == first
    assert "52" not in second.text
```

- [ ] **Step 2: Witness the missing process package**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_output.py
```

Expected: collection fails because `appv231.coding_agent.processes` does not
exist.

- [ ] **Step 3: Add immutable process contracts**

Use string enums so details serialize without custom JSON hooks. Keep the owner
free of JSONL session identity so same-workspace `/resume` can reconnect.

```python
class ProcessState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    DRAINING = "draining"
    EXITED = "exited"
    TIMED_OUT = "timed_out"
    TERMINATED = "terminated"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {self.EXITED, self.TIMED_OUT, self.TERMINATED, self.FAILED}


class StopCause(StrEnum):
    TIMEOUT = "timeout"
    ABORT_BEFORE_YIELD = "abort_before_yield"
    TERMINATE = "terminate"
    KILL = "kill"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class ProcessOwner:
    app_instance_id: str
    workspace_key: str
    origin: Literal["agent", "user"] = "agent"


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


@dataclass(frozen=True)
class OutputSlice:
    text: str
    cursor: int
    next_cursor: int


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
    suggested_poll_delay_ms: int = 1000


@dataclass(frozen=True)
class ProcessEvent:
    session_id: str
    state: ProcessState
    exit_code: int | None
    owner: ProcessOwner
```

`ProcessSnapshot.as_details()` must emit camelCase keys used by current tool
results: `sessionId`, `nextCursor`, `outputSize`, `exitCode`, `elapsedMs`,
`suggestedPollDelayMs`, and `tty`.

- [ ] **Step 4: Implement the sanitized append-only spool**

Use one incremental UTF-8 decoder and one `RLock`. Write sanitized UTF-8 to a
mode-0600 binary file, track `written_bytes`, and trim a read to the last valid
UTF-8 boundary at or below `max_bytes`.

```python
def read(self, cursor: int, max_bytes: int) -> OutputSlice:
    with self._lock:
        if cursor < 0 or cursor > self._written_bytes:
            raise InvalidCursorError(cursor, self._written_bytes)
        self._file.flush()
        with self._path.open("rb") as reader:
            reader.seek(cursor)
            data = reader.read(min(max_bytes, self._written_bytes - cursor))
        while data:
            try:
                text = data.decode("utf-8")
                break
            except UnicodeDecodeError as error:
                if error.reason != "unexpected end of data":
                    text = data.decode("utf-8", errors="replace")
                    break
                data = data[: error.start]
        else:
            text = ""
        return OutputSlice(text=text, cursor=cursor, next_cursor=cursor + len(data))
```

Sanitize incrementally before writing. Preserve tab/newline and normalize CRLF;
remove complete or split CSI, OSC, DCS, C0, and C1 control sequences. Export by
copying to a separate mode-0600 file so a session artifact registry can delete
its copy without deleting the service spool.

- [ ] **Step 5: Run the focused output tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_output.py appV2.3.1/tests/test_output_spool.py
```

Expected: all tests pass and the existing `OutputSpool` contract is unchanged.

- [ ] **Step 6: Commit the contract layer**

```bash
git add appV2.3.1/appv231/coding_agent/processes appV2.3.1/tests/test_process_output.py
git commit -m "feat(appv231): add managed process output contracts"
```

### Task 2: Deterministic Process Service State Machine

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/processes/transport.py`
- Create: `appV2.3.1/appv231/coding_agent/processes/service.py`
- Create: `appV2.3.1/tests/test_process_service.py`

**Interfaces:**
- Consumes: Task 1 process contracts and `SanitizedOutputSpool`.
- Produces: `ProcessTransport` protocol and `ProcessTransportFactory` callable.
- Produces: `ProcessSessionService.start`, `poll`, `write`, `resize`, `interrupt`, `terminate`, `kill`, `list`, `subscribe`, and `close`.
- Guarantees: one locked state machine, first-wins terminal cause, bounded input, no active-job eviction, and idempotent close.

The public signatures are fixed for later tasks:

```python
def start(self, owner, request, transport_factory, *, yield_time_ms=10_000, signal=None) -> ProcessSnapshot: ...
def poll(self, owner, session_id, cursor, *, wait_ms=1_000, max_bytes=51_200) -> ProcessSnapshot: ...
def write(self, owner, session_id, data, *, eof=False, wait_ms=1_000) -> ProcessSnapshot: ...
def resize(self, owner, session_id, *, rows, cols) -> ProcessSnapshot: ...
def interrupt(self, owner, session_id, *, wait_ms=1_000) -> ProcessSnapshot: ...
def terminate(self, owner, session_id, *, wait_ms=2_000) -> ProcessSnapshot: ...
def kill(self, owner, session_id) -> ProcessSnapshot: ...
def list(self, owner) -> tuple[ProcessSnapshot, ...]: ...
def subscribe(self, listener: Callable[[ProcessEvent], None]) -> Callable[[], None]: ...
def close(self) -> None: ...
```

- [ ] **Step 1: Add deterministic fake-transport regressions**

Build `FakeProcessTransport` in the test module with explicit output, exit, and
signal events. Test short completion, immediate detach, delayed detach, abort on
both sides of handoff, natural-exit/timeout races, repeated termination,
drain-before-terminal, ordered input, EOF, invalid owner, active limit, terminal
retention, subscriptions, and close escalation.

```python
def test_abort_after_atomic_handoff_does_not_kill_job(service, owner, fake_factory):
    signal = AbortSignal()
    snapshot = service.start(
        owner,
        request("long"),
        fake_factory,
        yield_time_ms=0,
        signal=signal,
    )
    signal.abort()

    assert snapshot.state is ProcessState.RUNNING
    assert fake_factory.last.signals == []
    assert service.poll(owner, snapshot.session_id, 0, wait_ms=0).state is ProcessState.RUNNING
```

- [ ] **Step 2: Witness the absent service**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_service.py
```

Expected: collection fails because `ProcessSessionService` is undefined.

- [ ] **Step 3: Define the transport protocol**

```python
class ProcessTransport(Protocol):
    tty: bool

    def read_sources(self) -> tuple[BinaryIO, ...]: ...
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def write(self, data: bytes) -> int: ...
    def close_stdin(self) -> None: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def signal_group(self, signal_name: Literal["interrupt", "terminate", "kill"]) -> None: ...
    def close(self) -> None: ...


ProcessTransportFactory = Callable[[ProcessLaunchRequest], ProcessTransport]
```

`read_sources()` returns two streams for pipes and one PTY master stream.
Implementations own their descriptors and make `close()` idempotent.

- [ ] **Step 4: Implement service ownership and state transitions**

Use one `_ManagedProcess` record per opaque `proc_` plus 16 random bytes encoded
as 32 lowercase hex characters.
Each record owns an `RLock`, `Condition`, output spool, input queue, stop event,
reader threads, input thread, and monitor thread.

```python
def _claim_stop(self, cause: StopCause) -> bool:
    with self.condition:
        if self.state.terminal or self.stop_cause is not None:
            return False
        self.stop_cause = cause
        self.state = ProcessState.STOPPING
        self.condition.notify_all()
        return True


def _publish_terminal(self, exit_code: int | None) -> None:
    with self.condition:
        if self.state.terminal:
            return
        self.exit_code = exit_code
        if self.stop_cause is StopCause.TIMEOUT:
            self.state = ProcessState.TIMED_OUT
        elif self.stop_cause is not None:
            self.state = ProcessState.TERMINATED
        else:
            self.state = ProcessState.EXITED
        self.output.finish()
        self.condition.notify_all()
```

The monitor polls against `time.monotonic()`, claims timeout only while the
transport is live, sends TERM, waits 2 seconds, then KILLs. After process exit,
set internal `DRAINING`, join readers for at most one second, close descriptors,
then publish terminal state and one `ProcessEvent`.

If timeout or shutdown is claimed before the initial foreground handoff, keep
`start()` waiting through bounded escalation/drain and return the terminal
snapshot instead of detaching a process that is already stopping. Isolate
subscriber exceptions so a TUI listener cannot kill the monitor thread.

- [ ] **Step 5: Add bounded ordered input and cursor polling**

`write` validates ownership and `RUNNING`, rejects payloads over 16 KiB or a
write that would raise pending input above 64 KiB, and enqueues bytes plus an
optional EOF sentinel. The writer thread serializes transport writes; broken
pipe marks input closed without relabeling a naturally exited process. `poll`
waits on the condition until output size changes,
terminal publication, or `wait_ms` expires, then reads exactly from the supplied
cursor.

- [ ] **Step 6: Implement retention and shutdown**

Limit active records to four. Keep at most 64 terminal records for 15 minutes,
evicting oldest terminal records before each public operation. `close()` first
marks the service closed, claims shutdown for every active record, performs
TERM-to-KILL escalation, joins supervisors, closes spools, removes the private
app-instance directory, and tolerates repeated calls.

- [ ] **Step 7: Run state-machine tests repeatedly**

```bash
for run in 1 2 3 4 5; do TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_service.py || exit 1; done
```

Expected: five clean passes with no leaked test threads.

- [ ] **Step 8: Commit the service**

```bash
git add appV2.3.1/appv231/coding_agent/processes appV2.3.1/tests/test_process_service.py
git commit -m "feat(appv231): add managed process state machine"
```

### Task 3: Local Pipe and POSIX PTY Transports

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/processes/local.py`
- Modify: `appV2.3.1/appv231/coding_agent/execution_backend.py:11-55`
- Create: `appV2.3.1/tests/test_process_local.py`
- Extend: `appV2.3.1/tests/test_coding_policy.py`

**Interfaces:**
- Consumes: `ProcessTransport` and `ProcessLaunchRequest`.
- Produces: `create_local_process_transport(request, backend) -> ProcessTransport`.
- Changes: `ExecutionBackend.spawn` accepts explicit stdio options while its existing defaults remain byte-for-byte compatible.

- [ ] **Step 1: Add real subprocess integration regressions**

Test pipe stdout/stderr merge, stdin/EOF, child-process tree termination,
timeout escalation, output flood bounds, PTY `isatty`, PTY write/resize, Linux
EIO handling, missing cwd, unsupported PTY platform, and descriptor cleanup.

```python
@pytest.mark.skipif(os.name != "posix", reason="v1 PTY is POSIX-only")
def test_pty_transport_detects_tty_and_resizes(service, owner, tmp_path):
    started = service.start(
        owner,
        request("python -c 'import os; print(os.isatty(0))'", cwd=tmp_path, tty=True),
        local_factory(),
        yield_time_ms=5_000,
    )
    assert started.state is ProcessState.EXITED
    assert "True" in started.output
```

- [ ] **Step 2: Witness missing local transport behavior**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_local.py
```

Expected: collection fails because `create_local_process_transport` is missing.

- [ ] **Step 3: Extend the declared execution backend without changing defaults**

Read optional `stdin`, `stdout`, and `stderr` keys from the existing options
mapping. Keep `DEVNULL`, `PIPE`, `PIPE`, and `start_new_session=True` as the
default path used by legacy `BashOperations`.

```python
return subprocess.Popen(
    [shell, "-c", command],
    cwd=cwd,
    env=dict(env),
    stdin=options.get("stdin", subprocess.DEVNULL),
    stdout=options.get("stdout", subprocess.PIPE),
    stderr=options.get("stderr", subprocess.PIPE),
    start_new_session=bool(options.get("start_new_session", os.name == "posix")),
)
```

- [ ] **Step 4: Implement pipe transport**

Spawn with stdin/stdout/stderr pipes and a new process session. Map interrupt,
terminate, and kill to the whole POSIX process group; use the process methods on
non-POSIX systems. Never expose `pid` through snapshots or tool details.

- [ ] **Step 5: Implement PTY transport**

On POSIX, call `pty.openpty()`, apply `TIOCSWINSZ` before spawn, pass the slave
for all three child streams, close the parent slave immediately, and wrap the
master once for read/write. `resize` applies a packed four-short winsize.

```python
def resize(self, rows: int, cols: int) -> None:
    if self._master_fd is None:
        raise ProcessOperationError("resize requires tty=true")
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
```

Close the slave on spawn failure, treat either a zero-byte read or
`OSError(errno.EIO)` from the master as EOF on POSIX, and make all close paths
idempotent.

- [ ] **Step 6: Run local, backend, and legacy bash tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_local.py appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_coding_agent.py -k "local_backend or container_backend or local_bash_operations or bash_tool_runs"
```

Expected: pass with unchanged legacy spawn defaults.

- [ ] **Step 7: Commit local transports**

```bash
git add appV2.3.1/appv231/coding_agent/processes/local.py appV2.3.1/appv231/coding_agent/execution_backend.py appV2.3.1/tests/test_process_local.py appV2.3.1/tests/test_coding_policy.py
git commit -m "feat(appv231): add pipe and PTY process transports"
```

### Task 4: Managed Bash and Companion Process Tool

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/tools/bash.py:25-430`
- Create: `appV2.3.1/appv231/coding_agent/tools/process.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/__init__.py:1-150`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:900-1230`
- Create: `appV2.3.1/tests/test_process_tools.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`
- Extend: `appV2.3.1/tests/test_app_integration.py`

**Interfaces:**
- Consumes: app-owned `ProcessSessionService`, current `ExecutionBackend`, workspace owner, artifacts, and spawn settings.
- Produces: extended managed `bash` schema and `create_process_tool_definition`.
- Guarantees: the loop sees ordinary tool results; legacy callers remain synchronous; internal subagents do not receive unusable detached handles.

- [ ] **Step 1: Add model-tool and compatibility regressions**

Prove the 10-second default is passed as yield, `yield_time_ms=0` detaches,
timeout remains independent, fast nonzero still errors, detached nonzero is a
successful poll observation, cursor details round-trip, action validation is
strict, completed output artifacts are copies, and custom `BashOperations`
remain synchronous.

Also run a scripted provider turn where `bash` returns `running`, then assert the
provider receives that result and emits its next assistant response while the
fake process is still live. This is the regression proving no core-loop edit is
needed.

```python
def test_agent_continues_after_bash_yields_without_process_exit(app_with_fake_process):
    app, transport, provider_calls = app_with_fake_process
    app.run_turn("start the long build and continue")

    assert transport.poll() is None
    assert provider_calls == ["bash", "after-running-result"]
    details = last_tool_result(app.session.messages).details
    assert details["status"] == "running"
    assert details["sessionId"].startswith("proc_")
```

- [ ] **Step 2: Witness unsupported managed arguments and tool**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_tools.py appV2.3.1/tests/test_app_integration.py -k "process or bash_yields"
```

Expected: tests fail because `yield_time_ms`, `tty`, and `process` are absent.

- [ ] **Step 3: Add the managed bash adapter**

Add optional `process_service`, `process_owner`, and `transport_factory`
factory arguments. When all three are present, validate managed arguments,
resolve command prefix/spawn hook once, and call `service.start`. Otherwise call
the current `BashOperations.exec` path unchanged.

```python
snapshot = process_service.start(
    process_owner,
    ProcessLaunchRequest(
        command=spawn_context.command,
        cwd=spawn_context.cwd,
        env=spawn_context.env,
        shell_path=shell_path or os.environ.get("SHELL") or "/bin/bash",
        tty=tty,
        rows=rows,
        cols=cols,
        timeout_seconds=timeout,
    ),
    transport_factory,
    yield_time_ms=yield_time_ms,
    signal=signal,
)
```

For terminal fast paths, preserve existing error text and tail/artifact details.
For running snapshots, return bounded cursor output plus a status footer and
`snapshot.as_details()`.

The managed schema adds only these provider-facing fields to the existing
command/timeout contract:

```python
"yield_time_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
"tty": {"type": "boolean"},
"rows": {"type": "integer", "minimum": 2, "maximum": 200},
"cols": {"type": "integer", "minimum": 20, "maximum": 500},
```

- [ ] **Step 4: Implement the `process` tool**

Use a flat provider-safe schema with action enum and optional fields. Validate
the required/forbidden fields per action before service access. Render commands
as `process poll proc_abcd`, never including stdin payload text in the call
header.

```python
PROCESS_ACTIONS = ("poll", "write", "resize", "interrupt", "terminate", "kill", "list")

def _execute_process(service, owner, _tid, args, signal=None, on_update=None, ctx=None):
    action = _validated_action(args)
    if action == "list":
        snapshots = service.list(owner)
        return _list_result(snapshots)
    session_id = _required_string(args, "session_id")
    if action == "poll":
        snapshot = service.poll(
            owner,
            session_id,
            _required_nonnegative_int(args, "cursor"),
            wait_ms=_bounded_wait(args.get("yield_time_ms"), default=1000),
            max_bytes=_bounded_max_bytes(args.get("max_bytes")),
        )
        return _snapshot_result(snapshot)
```

Implement the remaining actions as direct service calls with the same envelope.

- [ ] **Step 5: Register process only for app-managed sessions**

Keep the Pi-compatible global base registry intact for callers without a
service. In `AgentSession`, accept `process_service=None`; append the process
definition and include `process` in default active tools only when a service is
injected. Pass managed options into `bash`. Internal child sessions continue to
omit the service and therefore wait synchronously.

Build the transport factory in `_builtin_tool_options` without bypassing the
declared backend:

```python
transport_factory = lambda request: create_local_process_transport(
    request,
    self.execution_backend,
)
```

- [ ] **Step 6: Run tool, session, and loop regression suites**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_tools.py appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_coding_agent.py -k "bash or process or subagent or active_tool"
```

Expected: managed production sessions expose `process`; direct SDK and child
sessions preserve their old tool set and synchronous behavior.

- [ ] **Step 7: Commit model tools**

```bash
git add appV2.3.1/appv231/coding_agent/tools/bash.py appV2.3.1/appv231/coding_agent/tools/process.py appV2.3.1/appv231/coding_agent/tools/__init__.py appV2.3.1/appv231/coding_agent/agent_session.py appV2.3.1/tests/test_process_tools.py appV2.3.1/tests/test_coding_agent.py appV2.3.1/tests/test_app_integration.py
git commit -m "feat(appv231): yield long bash calls to managed sessions"
```

### Task 5: Process Policy and Guardrail Integration

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/policies/package_consent.py:20-80`
- Modify: `appV2.3.1/appv231/coding_agent/policies/tool_guardrails.py:1-900`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:3520-3690`
- Extend: `appV2.3.1/tests/test_coding_policy.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Consumes: ordinary `before_tool_call`/`after_tool_call` flow; no loop changes.
- Produces: action-aware process mutation classification and poll no-progress signatures.
- Guarantees: cross-workspace service checks are authoritative and package consent cannot be trivially bypassed through one `process.write` call.

- [ ] **Step 1: Add policy regressions**

Test package commands in `process.write`, single-use consent consumption,
non-package input, poll/list observation classification, write/signal mutation
classification, repeated same-cursor polling, advancing cursors, extension hook
blocking, unknown/cross-workspace handles, and command/input redaction.

```python
def test_process_write_package_mutation_requires_capability():
    call = ToolCallView(
        id="p1",
        name="process",
        args={"action": "write", "session_id": "proc_x", "input": "npm install left-pad\n"},
    )
    assert isinstance(PackageMutationPolicy().evaluate(call, _context()), RequireConsent)
```

- [ ] **Step 2: Witness current process misclassification**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py -k process
```

Expected: failures show package policy ignores process input and generic
guardrails treat every process action as mutating.

- [ ] **Step 3: Extend package consent without claiming shell containment**

```python
def _package_mutation_payload(call: ToolCallView) -> object:
    if call.name == "bash":
        return call.args.get("command")
    if call.name == "process" and call.args.get("action") == "write":
        return call.args.get("input")
    return None
```

Call `_is_package_mutation` on this payload and consume the existing capability
only for a detected mutation. Keep the policy documentation explicit that
lexical inspection is not a sandbox boundary.

- [ ] **Step 4: Make tool-loop classification action-aware**

Replace tool-name-only checks with call-aware helpers:

```python
def _process_is_observation(args: Mapping[str, Any]) -> bool:
    return args.get("action") in {"poll", "list"}


def _tool_call_may_change_state(tool_name: str, args: Mapping[str, Any]) -> bool:
    if tool_name == "process":
        return not _process_is_observation(args)
    if tool_name in MUTATING_TOOL_NAMES:
        return True
    return tool_name == "bash" and _bash_command_may_change_state(str(args.get("command", "")))
```

For `process.poll`, canonicalize only action, session ID, and cursor. Repeated
empty results at one cursor then use existing warning/block thresholds; output
progress or a larger cursor resets no-progress behavior.

- [ ] **Step 5: Bind workspace owner and extension hooks through AgentSession**

Build `ProcessOwner` from the app instance ID and canonical current `cwd` each
time a replacement session is constructed. Do not accept owner fields from
model arguments. Keep `process` in ordinary extension `tool_call` and
`tool_result` events; never expose `input` in TUI rendering or error logs.

- [ ] **Step 6: Run policy and guardrail tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_coding_agent.py -k "package_mutation or process or tool_loop_guardrail"
```

Expected: pass, including repeated-poll halt and capability consumption.

- [ ] **Step 7: Commit policy integration**

```bash
git add appV2.3.1/appv231/coding_agent/policies/package_consent.py appV2.3.1/appv231/coding_agent/policies/tool_guardrails.py appV2.3.1/appv231/coding_agent/agent_session.py appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_coding_agent.py
git commit -m "fix(appv231): enforce policy on managed process controls"
```

### Task 6: App Ownership, Session Replacement, and CLI Teardown

**Files:**
- Modify: `appV2.3.1/appv231/app.py:83-320`
- Modify: `appV2.3.1/appv231/cli.py:450-570`
- Extend: `appV2.3.1/tests/test_app_integration.py`
- Extend: `appV2.3.1/tests/test_cli.py`
- Extend: `appV2.3.1/tests/test_session_commands.py`

**Interfaces:**
- Produces: `CodingApp.process_service`, `CodingApp.process_owner(origin="agent")`, and idempotent `CodingApp.close()`.
- Guarantees: same service across session replacement, workspace isolation, and cleanup on every CLI return/error path.

- [ ] **Step 1: Add ownership and teardown regressions**

Prove a running process survives same-workspace `/resume`, remains hidden after a
different-workspace resume, reappears when switching back, and is killed only by
app close. Verify close is idempotent and prompt/plain/TUI CLI paths call it when
returning or raising.

```python
def test_session_replacement_reuses_app_process_service(app, running_job, target_session):
    service = app.process_service
    app.switch_session(str(target_session))

    assert app.process_service is service
    assert service.poll(app.process_owner(), running_job.session_id, 0, wait_ms=0).state is ProcessState.RUNNING
```

- [ ] **Step 2: Witness missing app lifetime authority**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_cli.py -k "process_service or app_close"
```

Expected: failures because `CodingApp` has no process service or close method.

- [ ] **Step 3: Create and inject one app-owned service**

Construct the service before `_create_session`, retain one random app instance
ID, and pass both service and instance ID into every `AgentSession` created by
`_create_session`. Do not put the service in generic agent state.

```python
self._app_instance_id = uuid.uuid4().hex
self.process_service = ProcessSessionService()

def process_owner(self, origin: Literal["agent", "user"] = "agent") -> ProcessOwner:
    return ProcessOwner(
        app_instance_id=self._app_instance_id,
        workspace_key=str(Path(self.cwd).resolve()),
        origin=origin,
    )
```

The replacement factory passes the same service and instance ID while deriving
the new owner from the replacement cwd.

- [ ] **Step 4: Add idempotent app close**

```python
def close(self) -> None:
    if self._closed:
        return
    self._closed = True
    self._unbind_session()
    self.process_service.close()
    self.session_runtime.dispose()
```

Ensure runtime disposal is attempted even if process cleanup raises by using a
`try/finally`, and preserve the first cleanup exception after all resources have
been attempted.

- [ ] **Step 5: Put all CLI execution behind `try/finally`**

After `CodingApp` construction, route prompt, TUI, and plain input through one
helper and close in the caller:

```python
try:
    return _run_configured_app(app, args, config, generation_warnings)
finally:
    app.close()
```

Parser/model failures before app construction require no service cleanup.

- [ ] **Step 6: Run app, session, and CLI tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_session_commands.py appV2.3.1/tests/test_cli.py
```

Expected: all pass; process service identity remains stable across replacement.

- [ ] **Step 7: Commit lifecycle ownership**

```bash
git add appV2.3.1/appv231/app.py appV2.3.1/appv231/cli.py appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_session_commands.py appV2.3.1/tests/test_cli.py
git commit -m "feat(appv231): own process sessions at app lifecycle"
```

### Task 7: TUI Completion Events and Process Management

**Files:**
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py:76-450,820-900,1390-1550`
- Modify: `appV2.3.1/appv231/tui/interactive.py:108-180`
- Extend: `appV2.3.1/tests/test_tui.py`
- Extend: `appV2.3.1/tests/test_tui_dispatcher.py`

**Interfaces:**
- Produces: `/processes` parser/handler and process completion subscription.
- Guarantees: background callbacks post through the TUI dispatcher, no automatic LLM turn starts, and stdin payloads never render.

- [ ] **Step 1: Add TUI event and selector regressions**

Cover command autocomplete/help, active/terminal ordering, current-workspace
filtering, selector cancellation, refresh, interrupt, terminate, kill, one
completion notice, dispatcher ownership, narrow-terminal rendering, and teardown
unsubscribe.

```python
def test_process_completion_posts_one_status_without_starting_turn(mode, service, owner):
    started_turns = mode.turn_start_count
    service.emit_for_test(ProcessEvent("proc_1234", ProcessState.EXITED, exit_code=0, owner=owner))
    mode.tui.drain_dispatcher()

    assert "Process proc_1234 exited (0)" in render_text(mode.history)
    assert mode.turn_start_count == started_turns
```

- [ ] **Step 2: Witness unknown `/processes` behavior**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_tui_dispatcher.py -k processes
```

Expected: failures because the command and subscription are absent.

- [ ] **Step 3: Subscribe through the UI dispatcher**

Subscribe during `InteractiveMode` initialization. The service callback must do
only `self.tui.post(lambda: self._handle_process_event(event))`. Ignore events
after shutdown, deduplicate terminal notices by session ID, and unsubscribe in
the existing `finally` block before `app.close()` runs in CLI.

- [ ] **Step 4: Add `/processes` selector and actions**

List current-workspace app records sorted active first, then newest terminal.
Rows show shortened opaque ID, status, elapsed time, `tty`/`pipe`, and at most
80 command characters. Selecting a row opens only actions valid for its state:
`Refresh`, `Interrupt`, `Terminate`, and `Kill`; terminal rows offer `Refresh`.
Invoke service methods directly as explicit user controls and render bounded
output through `StatusLine`/`Text`, never through a shell.

- [ ] **Step 5: Keep generic tool rendering safe**

When `ToolExecutionComponent` receives a running process result, render a stable
`running: proc_abcd` suffix and bounded sanitized output. `process.write` call
headers show only the action and shortened session ID. Do not render `input` in
collapsed or expanded views.

- [ ] **Step 6: Run all TUI tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_tui_dispatcher.py
```

Expected: pass with no off-owner UI mutation errors.

- [ ] **Step 7: Commit TUI behavior**

```bash
git add appV2.3.1/appv231/tui/interactive_mode.py appV2.3.1/appv231/tui/interactive.py appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_tui_dispatcher.py
git commit -m "feat(appv231): manage background processes in TUI"
```

### Task 8: Documentation and Production Verification Gates

**Files:**
- Modify: `appV2.3.1/README.md`
- Modify: `packages/appv231-cli/README.md`
- Verify only: `Dockerfile.appv231.release`
- Verify only: `packages/appv231-cli/bin/appv231.js`

**Interfaces:**
- Documents: yield vs timeout, process actions, PTY opt-in, `/processes`, lifecycle, and restart non-persistence.
- Verifies: source runtime, npm package, and production container behavior.

- [ ] **Step 1: Add concise end-user documentation**

Document these exact facts near the TUI/tool sections:

```text
Managed commands wait up to 10 seconds for a normal result. A command still
running after that window receives a process handle and continues in the same
app instance. The window is not a timeout. Use an explicit command timeout or
the process controls to stop it. Managed processes end when appv231 exits and
cannot be resumed after a container restart.
```

Add `/processes` to the command map. Do not claim user `!`/`!!` commands are
backgrounded in this release.

- [ ] **Step 2: Run the focused process suite**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_output.py appV2.3.1/tests/test_process_service.py appV2.3.1/tests/test_process_local.py appV2.3.1/tests/test_process_tools.py appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_session_commands.py appV2.3.1/tests/test_cli.py appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_tui_dispatcher.py
```

Expected: all pass.

- [ ] **Step 3: Run the full Python suite**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests
```

Expected: all tests pass with no leaked-thread warning or hanging process.

- [ ] **Step 4: Prove both redzones are untouched**

```bash
git diff --exit-code HEAD~8..HEAD -- appV2.3.1/appv231/agent appV2.3.1/appv231/compaction
```

Expected: no output and exit code 0. If commits were squashed, compare the
implementation base commit instead of `HEAD~8`.

- [ ] **Step 5: Run npm build and launcher tests**

```bash
npm --prefix packages/appv231-cli test
npm --prefix packages/appv231-cli run build
```

Expected: node tests pass and npm dry-run packing succeeds.

- [ ] **Step 6: Run an actual source TUI smoke**

Use a disposable workspace and the existing real TUI entry point, not an eval
runner. Configure the normal provider through the existing `.env` without
printing credentials. Send three prompts in one session:

```text
Run a command that prints START, sleeps 15 seconds, then prints DONE. Continue working as soon as it becomes a managed process.
Check that process once and report only its current status and new output.
Check it again after it finishes and report its exit code and final output.
```

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m appv231.cli --cwd /tmp/appv231-process-smoke --thinking medium --temperature 0.2
```

Expected: first tool result returns near 10 seconds with a `proc_` handle, the
same TUI session accepts prompts while the command lives, and the final poll
shows `DONE` with exit code 0.

- [ ] **Step 7: Build and test the production image without cache**

```bash
docker build --no-cache -f Dockerfile.appv231.release -t appv231:managed-process-test .
docker run --rm --entrypoint python -v "$PWD/appV2.3.1/tests:/tests:ro" appv231:managed-process-test -m pytest -q -p no:cacheprovider /tests/test_process_output.py /tests/test_process_service.py /tests/test_process_local.py /tests/test_process_tools.py
```

Expected: image builds from current source and focused tests pass inside it.

- [ ] **Step 8: Run the production image as an actual TUI**

Launch through the npm/Docker contract with a disposable host workspace and the
normal persisted agent-home mount:

```bash
node packages/appv231-cli/bin/appv231.js --image appv231:managed-process-test --no-pull --cwd /tmp/appv231-process-container-smoke --thinking medium --temperature 0.2
```

Repeat the three source-TUI prompts, then start another 30-second process and
exit appv231.

Expected: managed behavior matches source; after container exit, no matching
child remains, and a new container cannot list the old handle.

- [ ] **Step 9: Commit documentation**

```bash
git add appV2.3.1/README.md packages/appv231-cli/README.md
git commit -m "docs(appv231): document managed process sessions"
```

- [ ] **Step 10: Final worktree and history audit**

```bash
git status --short
git log --oneline -8
git diff --check HEAD~8..HEAD
```

Expected: only known unrelated user artifacts remain untracked, task commits are
scoped, and `git diff --check` reports no whitespace errors.
