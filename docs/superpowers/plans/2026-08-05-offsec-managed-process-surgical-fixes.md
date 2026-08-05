# OffSec Managed-Process Surgical Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Repository policy still forbids subagents unless the user explicitly requests them, so default to inline execution with `superpowers:executing-plans`.

**Goal:** Eliminate the confirmed managed-process validation, foreground-output, PTY EOF, child-ownership, and durable-completion failure modes without changing the agent loop, compaction, iteration budgeting, or tmux lifecycle.

**Architecture:** Keep the existing `bash -> ProcessSessionService -> process` control plane. Repair malformed model arguments only at the process-tool preparation boundary, coalesce foreground updates inside the process service before snapshots are constructed, make PTY EOF limitations explicit, clean child-owned managed jobs when internal subagents finish, and optimize durable completion using an atomic same-filesystem hard link with the current secure-copy fallback.

**Tech Stack:** Python 3.13, pytest, `jsonschema`, POSIX PTYs, `psutil`, SQLite, Node 20 launcher tests, uv package builds, Docker release smoke tests.

## Global Constraints

- Work only in `/Users/htooayelwin/lewis/travis234-offsec` on branch `offsec-agent`; do not modify or merge `main`.
- Treat commit `49e6e5278b4d5cc855c25a2bf451a964a033d250` as the implementation baseline when auditing newly changed files.
- Do not modify `travis/agent/agent_loop.py`, `travis/compaction/`, provider streaming, agent iteration/tool ordering, bounded subagent concurrency, or tmux ownership/lifecycle.
- Add a failing regression before every behavior change and run the focused test before and after the implementation.
- Preserve canonical snake_case process arguments after preparation and retain strict JSON-schema validation.
- Preserve the process state precedence: output/input/monitor failure -> `FAILED`; hard command deadline -> `TIMED_OUT`; explicit stop/abort/shutdown -> `TERMINATED`; natural completion -> `EXITED`.
- Preserve the distinction between `bash.timeout` and `process.wait`: wait expiry never kills or changes the command deadline.
- Preserve output cursors, sanitization, artifact registration, 64 MiB per-process output bounds, owner isolation, completion retention, and current file modes.
- Keep credentials out of tracked files, test fixtures, command output, and container layers.
- Do not commit, push, publish, or build release tags until the user separately approves those GitOps/release actions.
- Subagents may not be used during implementation unless the user explicitly requests them.

## File Responsibility Map

- `travis/coding_agent/tools/process.py`: model-facing process schema, argument recovery, action validation, and process-result wording.
- `travis/coding_agent/processes/service.py`: managed-process lifecycle, foreground-update cadence, PTY input validation, completion publication, and child process controls.
- `travis/coding_agent/processes/types.py`: stable process state and durable-completion contracts.
- `travis/coding_agent/processes/local.py`: pipe and POSIX PTY transport implementation; no behavioral rewrite is planned.
- `travis/coding_agent/processes/completions.py`: private durable-output promotion, SQLite indexing, retention, and restart recovery.
- `travis/coding_agent/session_subagents.py`: internal child-session ownership and final cleanup.
- `travis/coding_agent/tools/bash.py`: tool guidance only; managed execution behavior remains delegated to the service.
- `tests/test_process_tools.py`: process-tool preparation, schema, agent integration, and subagent/PTY tool flows.
- `tests/test_process_service.py`: lifecycle-level output coalescing and PTY EOF invariants.
- `tests/test_process_local.py`: real local pipe/PTY regression coverage.
- `tests/test_process_completions.py`: hard-link promotion, copy fallback, retention, and restart durability.
- `tests/test_coding_tools_and_subagents.py`: internal subagent cleanup without parent/tmux interference.
- `README.md` and `docs/offsec/manual.md`: operator-visible wait, PTY, and lifecycle contracts.

---

### Task 1: Recover narrowly malformed process arguments

**Files:**
- Modify: `travis/coding_agent/tools/process.py:24-182`
- Test: `tests/test_process_tools.py:88-190`
- Test: `tests/test_process_tools.py:712-786`

**Interfaces:**
- Consumes: raw model tool-call mappings accepted by `prepare_process_arguments(raw_args)`.
- Produces: canonical mutable arguments using `session_id`, `cursor`, `yield_time_ms`, `wait_time_ms`, and `max_bytes`; values remain subject to the existing action-specific schema and `_validate_args`.

- [ ] **Step 1: Add the exact failing malformed-wait regression**

Add a test containing the production payload verbatim:

```python
def test_process_argument_preparation_recovers_collapsed_wait_fields_and_id() -> None:
    arguments = {
        "action": "wait",
        "cursor": 17,
        "sessionid": "proc8e355b88e4fad64f5a9bd8c1e9cbc284",
        "waittimems": 120_000,
    }

    assert process_tool_module.prepare_process_arguments(arguments) == {
        "action": "wait",
        "cursor": 17,
        "session_id": "proc_8e355b88e4fad64f5a9bd8c1e9cbc284",
        "wait_time_ms": 60_000,
    }
```

Also replace `test_process_tool_rejects_compatibility_arguments` with parameterized recovery tests for `sessionId`, `nextCursor`, `yieldTimeMs`, `waitTimeMs`, and `maxBytes`. Add a separate test proving an unknown key such as `sessionHandle` remains untouched and subsequently fails `_validate_args`.

- [ ] **Step 2: Add ambiguity regressions**

Cover canonical-plus-alias conflicts explicitly:

```python
@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("session_id", "sessionid"),
        ("cursor", "nextCursor"),
        ("yield_time_ms", "yieldTimeMs"),
        ("wait_time_ms", "waittimems"),
        ("max_bytes", "maxBytes"),
    ],
)
def test_process_argument_preparation_rejects_conflicting_aliases(canonical: str, alias: str) -> None:
    arguments = {"action": "wait", canonical: 1, alias: 2}

    with pytest.raises(ValueError, match="conflicting process fields"):
        process_tool_module.prepare_process_arguments(arguments)
```

For identical values, assert preparation retains the canonical field and removes the alias. Numeric aliases must compare after integer-string coercion, so `60_000` and `"60000"` are identical.

- [ ] **Step 3: Run the focused tests and confirm failure**

Run:

```bash
uv run python -m pytest -q \
  tests/test_process_tools.py::test_process_argument_preparation_recovers_collapsed_wait_fields_and_id \
  tests/test_process_tools.py::test_process_argument_preparation_rejects_conflicting_aliases
```

Expected: the production payload fails because `sessionid` is not recognized, and the conflict test has no corresponding recovery boundary.

- [ ] **Step 4: Implement bounded alias and process-ID normalization**

Define `MAX_PROCESS_WAIT_MS = 60_000` before `_PROCESS_FIELDS` and use it in both the schema and runtime validation. Add a closed alias vocabulary rather than globally rewriting arbitrary names:

```python
_PROCESS_FIELD_TOKEN_MAP = {
    "sessionid": "session_id",
    "processid": "session_id",
    "cursor": "cursor",
    "nextcursor": "cursor",
    "yieldtimems": "yield_time_ms",
    "waittimems": "wait_time_ms",
    "maxbytes": "max_bytes",
}
_COLLAPSED_PROCESS_ID = re.compile(r"^proc([0-9a-f]{32})$")


def _process_field_token(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()
```

Implement `_normalize_process_field_aliases(args)` with these rules:

1. Canonical keys remain canonical.
2. Only keys whose collapsed token exists in `_PROCESS_FIELD_TOKEN_MAP` are recovered.
3. Canonical-plus-alias values are compared after `_coerce_process_integer` for numeric fields.
4. Conflicting values raise `ValueError("conflicting process fields: <canonical> and <alias>")`.
5. Identical aliases are removed.

Implement process-ID repair only for the exact missing-delimiter shape:

```python
def _normalize_process_session_id(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = _COLLAPSED_PROCESS_ID.fullmatch(value)
    return f"proc_{match.group(1)}" if match else value
```

Call field normalization first, integer coercion second, process-ID repair third, and the existing action normalization afterward. For `wait`, cap `wait_time_ms` at `MAX_PROCESS_WAIT_MS`; this shortens only a non-destructive observation deadline and never changes the command timeout.

- [ ] **Step 5: Add an agent-loop integration regression without modifying the loop**

Extend the existing faux-provider integration test so its second provider turn emits the malformed production shape. Assert the persisted assistant `ToolCall` contains canonical prepared arguments and the process result reaches `EXITED`:

```python
assert process_call.arguments == {
    "action": "wait",
    "session_id": started.details["sessionId"],
    "cursor": started.details["nextCursor"],
    "wait_time_ms": 60_000,
}
```

- [ ] **Step 6: Run focused process-tool verification**

Run:

```bash
uv run python -m pytest -q tests/test_process_tools.py
```

Expected: all process schema, preparation, wait, write, and agent integration tests pass.

- [ ] **Step 7: Prepare the task commit, but do not commit without approval**

When GitOps is authorized:

```bash
git add travis/coding_agent/tools/process.py tests/test_process_tools.py
git commit -m "fix(offsec): recover malformed process control arguments"
```

---

### Task 2: Coalesce foreground output before snapshot construction

**Files:**
- Modify: `travis/coding_agent/processes/service.py:58-204`
- Modify: `travis/coding_agent/processes/service.py:547-590`
- Test: `tests/test_process_service.py:207-309`
- Test: `tests/test_process_tools.py:361-419`

**Interfaces:**
- Consumes: `ProcessSessionService.start(..., on_update=listener)` and raw reader chunks.
- Produces: the same cumulative `ProcessSnapshot` callback shape, but at most one constructed foreground snapshot per configured interval plus the normal initial-handoff/final result.

- [ ] **Step 1: Add a regression that counts foreground callbacks for many chunks**

Use `FakeProcessTransport`, whose `BlockingReader` splits a large queued payload into 4 KiB reads:

```python
def test_foreground_output_is_coalesced_before_snapshot_construction(tmp_path: Path, owner) -> None:
    payload = b"x" * 1_000_000
    transport = FakeProcessTransport(initial_output=payload, initial_exit_code=0)
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        foreground_update_interval_seconds=60,
        termination_grace_seconds=0.02,
        drain_timeout_seconds=0.1,
    )
    updates = []
    try:
        terminal = service.start(
            owner,
            request("chatty"),
            Factory(transport),
            yield_time_ms=1_000,
            on_update=updates.append,
        )
        assert terminal.state is ProcessState.EXITED
        assert terminal.output_size == len(payload)
        assert len(updates) == 1
    finally:
        service.close()
```

- [ ] **Step 2: Run the regression and confirm amplification**

Run:

```bash
uv run python -m pytest -q tests/test_process_service.py::test_foreground_output_is_coalesced_before_snapshot_construction
```

Expected before the fix: many updates are observed because every 4 KiB read constructs a snapshot.

- [ ] **Step 3: Add service-owned foreground cadence**

Add `foreground_update_interval_seconds: float = 0.1` to `ProcessSessionService.__init__`, reject negative values, and store it as `_foreground_update_interval_seconds`.

Add `next_foreground_update_at` to `_ManagedProcess`, initialized to `started_at`. In `_read_output`, decide whether an update is due while holding `record.condition`, but construct and deliver the snapshot after releasing the lock:

```python
listener = None
with record.condition:
    now = self._clock()
    if (
        record.foreground_update is not None
        and now >= record.next_foreground_update_at
    ):
        listener = record.foreground_update
        record.next_foreground_update_at = now + self._foreground_update_interval_seconds
    record.condition.notify_all()
if listener is not None:
    try:
        listener(self._snapshot(record, 0, self._max_output_bytes))
    except BaseException:
        pass
```

Keep `BASH_UPDATE_THROTTLE_SECONDS` unchanged as a downstream defensive guard. Do not change snapshot cursor semantics or tool-update rendering in this task.

- [ ] **Step 4: Add final-output and two-reader regressions**

Add tests proving:

- Coalescing does not omit bytes from the terminal snapshot/tail.
- Concurrent stdout/stderr readers cannot both win the same cadence window.
- A listener exception still cannot kill the monitor or command.
- A process that exits inside the cadence window still returns its final terminal result.

- [ ] **Step 5: Run focused lifecycle and tool-streaming tests**

Run:

```bash
uv run python -m pytest -q \
  tests/test_process_service.py \
  tests/test_process_tools.py::test_managed_bash_streams_sanitized_updates_before_handoff \
  tests/test_process_tools.py::test_agent_uses_one_wait_call_despite_multiple_process_updates
```

Expected: all tests pass and the new amplification regression observes one foreground callback in its 60-second window.

- [ ] **Step 6: Prepare the task commit, but do not commit without approval**

When GitOps is authorized:

```bash
git add travis/coding_agent/processes/service.py tests/test_process_service.py tests/test_process_tools.py
git commit -m "fix(offsec): coalesce managed process output updates"
```

---

### Task 3: Make PTY EOF behavior honest and non-destructive

**Files:**
- Modify: `travis/coding_agent/processes/service.py:286-320`
- Modify: `travis/coding_agent/tools/process.py:26-84`
- Modify: `travis/coding_agent/tools/process.py:185-225`
- Test: `tests/test_process_service.py:310-400`
- Test: `tests/test_process_local.py:282-307`
- Test: `tests/test_process_tools.py:861-946`

**Interfaces:**
- Consumes: `ProcessSessionService.write(owner, session_id, data, eof=...)`.
- Produces: real half-close for pipe transports; immediate `ProcessStateError` for `eof=true` on PTYs before input is queued or marked closed.

- [ ] **Step 1: Add a failing PTY EOF service regression**

Use a running `FakeProcessTransport(tty=True)` and assert rejection leaves the process writable:

```python
def test_pty_rejects_pipe_style_eof_without_closing_input(service, owner) -> None:
    transport = FakeProcessTransport(tty=True)
    started = service.start(owner, request("interactive", tty=True), Factory(transport), yield_time_ms=0)

    with pytest.raises(ProcessStateError, match="PTY.*write_raw.*Ctrl-D"):
        service.write(owner, started.session_id, "partial", eof=True, wait_ms=0)

    assert transport.stdin_closed is False
    service.write(owner, started.session_id, "still-usable\n", wait_ms=0)
    assert transport.writes == [b"still-usable\n"]
```

- [ ] **Step 2: Add a real POSIX PTY regression**

Start the existing `input()`-based PTY command. Call `service.write(..., eof=True)` with a partial value, assert immediate rejection, then submit a normal newline-terminated value and assert `EXITED` with the expected marker. Keep the test skipped on non-POSIX platforms like the existing PTY tests.

- [ ] **Step 3: Run both regressions and confirm failure**

Run:

```bash
uv run python -m pytest -q \
  tests/test_process_service.py::test_pty_rejects_pipe_style_eof_without_closing_input \
  tests/test_process_local.py::test_real_pty_eof_rejection_keeps_follow_up_input_available
```

Expected before the fix: the service queues EOF, marks input closed, and cannot accept the follow-up write.

- [ ] **Step 4: Reject PTY EOF before mutating input state**

Inside `ProcessSessionService.write`, after `_require_running` and before changing `pending_input_bytes` or `input_closed`, add:

```python
if eof and record.request.tty:
    raise ProcessStateError(
        "PTY sessions do not support pipe-style EOF; send an explicit Ctrl-D "
        "with process write_raw input \\"\\u0004\\" when the program expects that keystroke"
    )
```

Do not implement double Ctrl-D, do not close the PTY master, and do not change `_PTYTransport.close_stdin`; the service boundary prevents the unsupported operation while preserving internal cleanup behavior.

- [ ] **Step 5: Clarify the model-facing EOF contract**

Change the `eof` schema description to state that it closes pipe stdin and is invalid for PTYs. Add a process prompt guideline:

```text
Use eof only for pipe-backed commands. A PTY has no pipe-style half-close; send an explicit Ctrl-D with write_raw input "\u0004" only when the interactive program expects that keystroke.
```

- [ ] **Step 6: Verify pipe EOF remains unchanged**

Run:

```bash
uv run python -m pytest -q \
  tests/test_process_service.py::test_write_is_ordered_and_eof_closes_after_payload \
  tests/test_process_local.py::test_pipe_transport_accepts_ordered_input_and_eof \
  tests/test_process_local.py -k pty \
  tests/test_process_tools.py -k process_write
```

Expected: pipe EOF, PTY input, PTY resize, write, and write_raw tests all pass.

- [ ] **Step 7: Prepare the task commit, but do not commit without approval**

When GitOps is authorized:

```bash
git add \
  travis/coding_agent/processes/service.py \
  travis/coding_agent/tools/process.py \
  tests/test_process_service.py \
  tests/test_process_local.py \
  tests/test_process_tools.py
git commit -m "fix(offsec): reject unsupported PTY pipe EOF"
```

---

### Task 4: Reap child-owned managed processes at subagent completion

**Files:**
- Modify: `travis/coding_agent/session_subagents.py:389-456`
- Test: `tests/test_coding_tools_and_subagents.py:1532-1620`
- Test: `tests/test_process_tools.py:639-709`

**Interfaces:**
- Consumes: the child-specific `ProcessOwner` returned by `_subagent_process_owner` and `ProcessSessionService.list/kill`.
- Produces: no active child-owned `proc_*` jobs after `_run_internal_subagent` returns; parent-owned processes and detached tmux sessions remain unchanged.

- [ ] **Step 1: Add a child-leak regression**

Build an internal child faux-provider sequence that starts `sleep 60` through `bash` with `yield_time_ms=0`, then returns a final text response without waiting. After `_run_internal_subagent` returns, use the existing `eventually` helper pattern to assert the child-owned process reaches a terminal state:

```python
eventually(
    lambda: all(snapshot.state.terminal for snapshot in service.list(child_owner)),
    timeout=2,
)
assert service.list(parent_owner) == ()
```

Before the fix, assert the test observes a child-owned `RUNNING` process.

- [ ] **Step 2: Add parent and tmux isolation regressions**

In the same test module:

- Start one parent-owned managed process before the child; assert it remains `RUNNING` after child cleanup.
- Use the existing fake/real tmux operation fixture to create a child-visible namespaced tmux session; assert subagent cleanup does not stop it.
- Ensure test cleanup explicitly kills the parent process and stops the tmux session.

- [ ] **Step 3: Run the regressions and confirm the child process leaks**

Run:

```bash
uv run python -m pytest -q \
  tests/test_coding_tools_and_subagents.py::test_internal_child_reaps_active_managed_processes_on_completion \
  tests/test_coding_tools_and_subagents.py::test_internal_child_cleanup_preserves_parent_processes_and_tmux
```

Expected before the fix: the child-owned managed process remains active.

- [ ] **Step 4: Implement bounded child-owner cleanup**

Add a private helper to the session-subagent controller:

```python
def _kill_active_subagent_processes(self, owner: ProcessOwner | None) -> None:
    if owner is None or self.process_service is None:
        return
    try:
        snapshots = self.process_service.list(owner)
    except Exception:
        return
    for snapshot in snapshots:
        if snapshot.state.terminal:
            continue
        try:
            self.process_service.kill(owner, snapshot.session_id)
        except Exception:
            continue
```

Call it in `_run_internal_subagent`'s `finally` block before `child.shutdown()`. Use immediate `kill`, not `terminate`, because current `terminate` can block for its grace interval per child process. The cleanup is scoped to the exact child owner and cannot match parent/user owners.

- [ ] **Step 5: Verify cancellation and successful-child paths**

Add or extend tests so cleanup runs when:

- The child returns normally.
- The child provider raises.
- The parent cancels the child.
- The child has no managed process service.

The original child exception/result remains authoritative; cleanup exceptions must never replace it.

- [ ] **Step 6: Run the subagent and process integration suites**

Run:

```bash
uv run python -m pytest -q \
  tests/test_coding_tools_and_subagents.py \
  tests/test_process_tools.py::test_internal_child_can_send_follow_up_input_to_managed_pty
```

Expected: all child tool, result-pack, PTY, cancellation, and cleanup tests pass.

- [ ] **Step 7: Prepare the task commit, but do not commit without approval**

When GitOps is authorized:

```bash
git add travis/coding_agent/session_subagents.py tests/test_coding_tools_and_subagents.py
git commit -m "fix(offsec): reap child-owned managed processes"
```

---

### Task 5: Promote terminal output without a same-filesystem full copy

**Files:**
- Modify: `travis/coding_agent/processes/types.py:44-65`
- Modify: `travis/coding_agent/processes/service.py:929-951`
- Modify: `travis/coding_agent/processes/completions.py:93-150`
- Modify: `travis/coding_agent/processes/completions.py:480-648`
- Test: `tests/test_process_completions.py`
- Test: `tests/test_process_service.py:686-746`

**Interfaces:**
- Consumes: a finalized, immutable sanitized spool plus authoritative `output_size` and `total_lines` from `SanitizedOutputSpool`.
- Produces: the same durable completion path and SQLite record; same-filesystem storage uses an atomic hard link, unsupported/cross-filesystem storage uses the current atomic secure copy.

- [ ] **Step 1: Add hard-link and fallback regressions**

Add a POSIX test proving same-filesystem promotion shares an inode while both paths exist:

```python
@pytest.mark.skipif(os.name == "nt", reason="hard-link inode assertion is POSIX-specific")
def test_completion_uses_hard_link_for_same_filesystem_output(tmp_path: Path) -> None:
    # Build owner, source, record, and store using existing helpers.
    persisted = store.persist(owner, record, source)
    assert persisted.stat().st_ino == source.stat().st_ino
    source.unlink()
    assert persisted.read_text(encoding="utf-8") == "done\n"
```

Add a fallback test monkeypatching `os.link` to raise `OSError(errno.EXDEV, "cross-device link")`; assert persistence succeeds, content is identical, and source/destination inodes differ on POSIX.

- [ ] **Step 2: Add authoritative line-count regressions**

Extend `ProcessCompletionRecord` construction in tests with `total_lines`. Add rejection tests for negative line counts and assert restart/tail recovery uses the provided count exactly.

- [ ] **Step 3: Run the new completion tests and confirm failure**

Run:

```bash
uv run python -m pytest -q \
  tests/test_process_completions.py::test_completion_uses_hard_link_for_same_filesystem_output \
  tests/test_process_completions.py::test_completion_falls_back_to_atomic_copy_across_filesystems
```

Expected before the fix: `_atomic_copy_0600` always creates a different inode and `ProcessCompletionRecord` has no authoritative line count.

- [ ] **Step 4: Add `total_lines` to the completion contract**

Change the dataclass:

```python
@dataclass(frozen=True)
class ProcessCompletionRecord:
    session_id: str
    state: ProcessState
    exit_code: int | None
    output_size: int
    total_lines: int
    elapsed_ms: int
    completed_at: float
    launch_session_id: str | None
    failure_code: str | None
    tty: bool = False
```

In `ProcessSessionService._persist_completion`, obtain `total_lines` from `record.output.tail_snapshot().total_lines` after `record.output.finish()` and before constructing the completion. Update every test constructor found by:

```bash
rg -n "ProcessCompletionRecord\(" travis tests
```

- [ ] **Step 5: Implement atomic link-first promotion**

Add `errno` import and a link-first helper:

```python
_LINK_FALLBACK_ERRNOS = {
    errno.EXDEV,
    errno.EPERM,
    errno.EACCES,
    getattr(errno, "EOPNOTSUPP", errno.EPERM),
    getattr(errno, "ENOTSUP", errno.EPERM),
}


def _promote_output_0600(
    source: Path,
    destination: Path,
    *,
    output_size: int,
    total_lines: int,
) -> _OutputMetrics:
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno not in _LINK_FALLBACK_ERRNOS:
            raise
        return _atomic_copy_0600(source, destination)
    destination.chmod(0o600)
    _fsync_directory(destination.parent)
    return _OutputMetrics(size=output_size, total_lines=total_lines)
```

Extract the existing directory fsync block into `_fsync_directory(path)`. Replace `_atomic_copy_0600` in `persist` with `_promote_output_0600(...)`. Verify both returned size and line count against the completion record. Existing exception handling must unlink the destination if SQLite insertion fails.

- [ ] **Step 6: Preserve completion security and retention behavior**

Run:

```bash
uv run python -m pytest -q tests/test_process_completions.py tests/test_process_service.py
```

Expected: permissions, corruption quarantine, concurrent stores, TTL/count/size pruning, UTF-8 cursoring, terminal eviction, and restart recovery all pass.

- [ ] **Step 7: Prepare the task commit, but do not commit without approval**

When GitOps is authorized:

```bash
git add \
  travis/coding_agent/processes/types.py \
  travis/coding_agent/processes/service.py \
  travis/coding_agent/processes/completions.py \
  tests/test_process_completions.py \
  tests/test_process_service.py
git commit -m "perf(offsec): promote durable process output without copying"
```

---

### Task 6: Align tool guidance and operator documentation

**Files:**
- Modify: `travis/coding_agent/tools/bash.py:592-640`
- Modify: `travis/coding_agent/tools/process.py:185-225`
- Modify: `README.md:102-124`
- Modify: `docs/offsec/manual.md:61-77`
- Test: `tests/test_process_tools.py:539-590`
- Test: `tests/test_process_tools.py:854-875`

**Interfaces:**
- Consumes: the final runtime contracts from Tasks 1-5.
- Produces: one consistent model/operator contract: 60-second maximum per wait call, repeated waits from exact `nextCursor`, `yield_time_ms=0` for planned interaction, pipe-only EOF, and tmux for cross-app durability.

- [ ] **Step 1: Add prompt-contract assertions**

Assert generated bash/process guidance contains these exact concepts:

```text
For planned interactive follow-up, launch bash with tty=true and yield_time_ms=0.
Each process wait observes for at most 60000 ms and never changes bash.timeout.
Use eof only for pipe stdin; use write_raw with an explicit Ctrl-D keystroke for PTYs.
```

- [ ] **Step 2: Run prompt tests and confirm the missing guidance**

Run:

```bash
uv run python -m pytest -q \
  tests/test_process_tools.py::test_managed_bash_warns_models_not_to_infer_execution_deadlines \
  tests/test_process_tools.py::test_process_write_explains_raw_input_and_line_submission
```

Expected before documentation changes: at least the interactive zero-yield and PTY EOF assertions fail.

- [ ] **Step 3: Update tool descriptions and prompt guidelines**

In `bash.py`, retain the default 10-second handoff for ordinary commands but explicitly tell the model to use `tty=true` and `yield_time_ms=0` when follow-up input is planned.

In `process.py`, state that one wait call is bounded to 60 seconds, a returned running state requires another wait from the exact `nextCursor`, and the observation deadline never kills the command.

- [ ] **Step 4: Correct public documentation**

Change `docs/offsec/manual.md` from “1 to 900 seconds” to “1 to 60 seconds per model-facing wait call.” Explain that Travis may issue repeated waits and that internal user-command waiting can use longer service intervals without changing the public tool contract.

Document:

- `tty=true` implicitly keeps PTY input available.
- `eof=true` is pipe-only.
- Managed `proc_*` handles survive turns but not application restarts.
- tmux remains the supported cross-turn/cross-app durable terminal.
- `/exit` cleans all app-owned managed processes, including child-owned jobs.

- [ ] **Step 5: Run documentation contract tests**

Run:

```bash
uv run python -m pytest -q tests/test_process_tools.py tests/test_coding_tools_and_subagents.py
```

Expected: all generated-system-prompt and tool-guidance tests pass.

- [ ] **Step 6: Prepare the task commit, but do not commit without approval**

When GitOps is authorized:

```bash
git add \
  travis/coding_agent/tools/bash.py \
  travis/coding_agent/tools/process.py \
  README.md \
  docs/offsec/manual.md \
  tests/test_process_tools.py
git commit -m "docs(offsec): clarify managed PTY and wait contracts"
```

---

### Task 7: Run red-zone and release-level qualification

**Files:**
- Verify only: repository and package outputs
- Do not modify: versions, changelogs, release tags, registries, or `main`

**Interfaces:**
- Consumes: all changes from Tasks 1-6.
- Produces: evidence that Python, npm launcher, wheel/sdist, and container behavior remain release-ready.

- [ ] **Step 1: Verify the exact production regressions together**

Run the named malformed-wait, chatty-output, PTY EOF, child cleanup, and hard-link tests in one command. Expected: every regression passes and no process/container remains active afterward.

- [ ] **Step 2: Run the complete managed-process cluster**

Run:

```bash
uv run python -m pytest -q \
  tests/test_process_local.py \
  tests/test_process_service.py \
  tests/test_process_tools.py \
  tests/test_process_output.py \
  tests/test_process_context.py \
  tests/test_process_completions.py \
  tests/test_process_regressions.py \
  tests/test_tmux_tool.py \
  tests/test_coding_tools_and_subagents.py
```

Expected: all tests pass; only platform-declared skips are allowed.

- [ ] **Step 3: Run repository-level Python verification**

Run:

```bash
PYTHONPATH=. uv run --with 'pytest>=8,<10' pytest -q tests
```

Expected: the complete suite passes with no new skips or warnings attributable to these changes.

- [ ] **Step 4: Run npm launcher verification**

Run:

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run build
```

Expected: Node tests pass and `npm pack --dry-run` includes only the declared package files.

- [ ] **Step 5: Build Python packages**

Run:

```bash
uv build
```

Expected: wheel and source distribution build successfully. Do not upload them.

- [ ] **Step 6: Run the release-container smoke**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234-offsec:process-fix-smoke .
python evals/container_smoke.py --image travis234-offsec:process-fix-smoke
```

Expected: the installed CLI and required Kali/terminal tools pass the existing smoke contract. Remove only the test container created by this smoke; retain the local image unless the user asks for deletion.

- [ ] **Step 7: Check branch integrity and diff scope**

Run:

```bash
git status --short --branch
git diff --check
git diff --stat
git diff --name-only 49e6e5278b4d5cc855c25a2bf451a964a033d250...HEAD
```

Expected:

- Branch remains `offsec-agent`.
- No implementation diff from the recorded baseline exists under `travis/agent/agent_loop.py` or `travis/compaction/`.
- Existing unrelated untracked files remain untouched.
- No credential or dotenv file is staged.

- [ ] **Step 8: Stop and present evidence before GitOps or release**

Report focused and full test counts, npm results, package artifacts, container smoke result, changed files, remaining untracked files, and any failed attempts. Wait for explicit user approval before commits, push, GHCR, npm, or PyPI actions.

## Explicit Follow-Up Boundaries

The following findings are real lifecycle/performance concerns but are intentionally excluded from this surgical implementation because they require separate state-machine or supervision designs:

1. **Abrupt-crash orphan recovery:** clean shutdown already terminates managed processes, but crash-safe reaping needs an identity-safe persisted registry or supervisor. Implementing it here would change transport identity, startup recovery, and cross-platform process ownership.
2. **Nonblocking terminate escalation:** making `terminate(yield_time_ms=0)` return immediately requires moving grace-deadline escalation into the monitor state machine. That changes STOPPING/kill races and must receive its own plan and regression matrix.
3. **Process-tree refresh cadence:** the current 100 ms recursive refresh protects against descendants that detach into new sessions. Do not slow it based on intuition; first profile 16 long-running process trees on Kali and prove meaningful CPU cost without weakening escaped-descendant cleanup.

Create separate, user-approved plans for those three items only after Tasks 1-7 are verified. They are not required to ship the confirmed P0/P1 smoking-gun repairs.
