# appv231 Data Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent overlapping runs, unbounded command-output retention, rolling-summary loss, active-turn compaction races, and total session loss from a truncated tail record.

**Architecture:** Add a run lease independent of mutable agent state, replace output accumulation with a streaming spool, adapt persisted compaction summaries at the coding-agent boundary, coordinate manual compaction with active runs, and recover only incomplete final JSONL records. The compaction package remains unchanged.

**Tech Stack:** Python 3.13, pytest, asyncio/threading primitives already used by appv231, append-only JSONL, existing Hermes `ContextCompressor` API.

## Global Constraints

- Do not edit `appV2.3.1/appv231/compaction/`.
- Do not perform mutating git operations; read-only status and diff checks are permitted.
- Preserve existing session-file readability.
- Every task starts with a failing regression and ends with focused green tests.
- Do not expose credentials or unrestricted temporary-directory access.

---

### Task 1: Independent Agent Run Lease

**Files:**
- Create: `appV2.3.1/appv231/agent/run_lease.py`
- Modify: `appV2.3.1/appv231/agent/agent.py:70-379`
- Modify: `appV2.3.1/appv231/agent/__init__.py`
- Create: `appV2.3.1/tests/test_agent_runtime_hardening.py`

**Interfaces:**
- Produces: `RunLease.acquire(error_message: str) -> RunLeaseToken`
- Produces: `RunLease.active: bool`
- Produces: `RunLease.owned_by_current_thread: bool`
- Produces: `RunLease.wait(timeout: float | None = None) -> bool`
- Produces: `RunLeaseToken.release() -> None`, idempotent
- Produces: `Agent.run_lease: RunLease` read-only property
- Consumed later by: compaction coordination and the async core-runtime plan

- [ ] **Step 1: Write the overlapping-reset regression**

```python
def test_reset_does_not_release_active_run_for_second_prompt():
    entered = threading.Event()
    release = threading.Event()
    provider_calls: list[int] = []

    def blocking_stream(*_args, **_kwargs):
        provider_calls.append(len(provider_calls) + 1)
        entered.set()
        release.wait(timeout=2)
        return assistant_stream("done")

    agent = make_agent(stream_fn=blocking_stream)
    first = threading.Thread(target=lambda: agent.prompt("first"))
    first.start()
    assert entered.wait(timeout=1)

    with pytest.raises(RuntimeError, match="active run"):
        agent.reset()
    with pytest.raises(RuntimeError, match="already processing"):
        agent.prompt("second")

    release.set()
    first.join(timeout=2)
    assert provider_calls == [1]
```

- [ ] **Step 2: Run the regression and verify current failure**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_runtime_hardening.py::test_reset_does_not_release_active_run_for_second_prompt
```

Expected before repair: the reset succeeds or a second provider call begins.

- [ ] **Step 3: Implement the lease**

```python
class RunLease:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active_token: object | None = None
        self._owner_thread_id: int | None = None

    def acquire(self, error_message: str) -> "RunLeaseToken":
        with self._condition:
            if self._active_token is not None:
                raise RuntimeError(error_message)
            token = object()
            self._active_token = token
            self._owner_thread_id = threading.get_ident()
            return RunLeaseToken(self, token)

    def _release(self, token: object) -> None:
        with self._condition:
            if token is not self._active_token:
                return
            self._active_token = None
            self._owner_thread_id = None
            self._condition.notify_all()

    @property
    def active(self) -> bool:
        with self._condition:
            return self._active_token is not None

    @property
    def owned_by_current_thread(self) -> bool:
        with self._condition:
            return self._active_token is not None and self._owner_thread_id == threading.get_ident()

    def wait(self, timeout: float | None = None) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._active_token is None, timeout=timeout)

class RunLeaseToken:
    def __init__(self, lease: RunLease, token: object) -> None:
        self._lease = lease
        self._token = token
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lease._release(self._token)
```

Store the token in `Agent._begin_run()`, release it in `Agent._finish_run()`, and make `reset()` reject while `RunLease.active` is true. Keep `AgentState.is_streaming` as presentation state only.

- [ ] **Step 4: Add lease lifecycle tests**

Cover idempotent release, timeout, owner detection, failure cleanup, and a fresh run after abort.

- [ ] **Step 5: Run focused tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_runtime_hardening.py appV2.3.1/tests/test_agent_loop.py
```

Expected: pass.

### Task 2: Bounded Complete Output Spool

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/tools/output_spool.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/bash.py:20-360`
- Modify: `appV2.3.1/appv231/coding_agent/bash_executor.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:79,3390-3430`
- Delete after callers migrate: `appV2.3.1/appv231/coding_agent/tools/output_accumulator.py`
- Create: `appV2.3.1/tests/test_output_spool.py`

**Interfaces:**
- Produces: `OutputSpool.append(data: bytes) -> None`
- Produces: `OutputSpool.snapshot(final: bool = False) -> OutputSnapshot`
- Produces: `OutputSpool.close() -> None`
- Produces: `OutputSnapshot.content`, `truncation`, `full_output_path`, `total_bytes`
- Preserves the result fields consumed by `_format_output()`

- [ ] **Step 1: Write regressions for bounded memory and complete artifacts**

```python
def test_spool_bounds_memory_and_persists_every_byte(tmp_path):
    spool = OutputSpool(max_bytes=1024, max_lines=20, directory=tmp_path)
    payload = b"x" * (10 * 1024 * 1024)
    for offset in range(0, len(payload), 8192):
        spool.append(payload[offset : offset + 8192])
        spool.snapshot()
    final = spool.snapshot(final=True)
    spool.close()

    assert len(final.content.encode("utf-8")) <= 1024
    assert Path(final.full_output_path).read_bytes() == payload
    assert stat.S_IMODE(Path(final.full_output_path).stat().st_mode) == 0o600
    assert not hasattr(spool, "_raw")
```

Add separate tests proving later snapshots append rather than freeze, invalid UTF-8 becomes `\ufffd`, and `close()` flushes and closes the file.

- [ ] **Step 2: Verify the current accumulator fails**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_output_spool.py
```

Expected before repair: missing class, unbounded retention, incomplete artifact, or incorrect mode.

- [ ] **Step 3: Implement streaming storage**

Use `tempfile.mkstemp()`, `os.fchmod(fd, 0o600)`, a binary file handle, and `codecs.getincrementaldecoder("utf-8")(errors="replace")`. On every append:

```python
self._file.write(data)
self._total_bytes += len(data)
decoded = self._decoder.decode(data, final=False)
self._tail = truncate_tail(
    self._tail + decoded,
    max_lines=self.max_lines,
    max_bytes=self.max_bytes,
).content
```

Maintain cumulative `total_bytes`, `total_lines`, and `was_truncated` counters separately from the retained tail, so a later snapshot cannot forget earlier eviction. Do not retain the complete byte or text stream in object fields. Finalization calls the decoder with `final=True`, flushes the file, and produces a final snapshot from the cumulative counters plus bounded tail.

- [ ] **Step 4: Migrate all three output call paths**

Update built-in bash, user bash, and `bash_executor.py`. Preserve existing truncation detail keys and status text so renderers remain compatible.

- [ ] **Step 5: Run focused output and bash tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_output_spool.py appV2.3.1/tests/test_coding_agent.py -k "bash or output or truncat"
```

Expected: pass with bounded-memory regression included.

### Task 3: Compaction Boundary Adapter

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/compaction_adapter.py`
- Modify: `appV2.3.1/appv231/app.py:75-380`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:615-680,3205-3335`
- Create: `appV2.3.1/tests/test_compaction_integration.py`
- Read only: `appV2.3.1/appv231/compaction/compressor.py`

**Interfaces:**
- Produces: `CompactionBoundaryAdapter.to_compressor_messages(messages: Sequence[AgentMessage]) -> list[AgentMessage]`
- Produces: `CompactionBoundaryAdapter.summary_from_result(messages: Sequence[AgentMessage]) -> str`
- Consumed by: preflight, post-response, overflow, failed-turn, and manual compaction paths

- [ ] **Step 1: Write the persisted two-compaction regression**

```python
def test_second_persisted_compaction_receives_previous_summary(tmp_path):
    prompts: list[str] = []
    app = make_persisted_app(tmp_path, summarizer=lambda prompt: prompts.append(prompt) or f"summary-{len(prompts)}")
    seed_first_large_history(app)
    app.session.compact()
    reload_same_session(app)
    append_second_large_history(app)
    app.session.compact()

    assert len(prompts) == 2
    assert "Previous summary" in prompts[1]
    assert "summary-1" in prompts[1]
    assert app.session.messages[0].role == "compactionSummary"
```

- [ ] **Step 2: Verify failure against the current integration**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_compaction_integration.py::test_second_persisted_compaction_receives_previous_summary
```

Expected before repair: the second summarizer prompt lacks the first summary.

- [ ] **Step 3: Implement only the external adapter**

For each `CompactionSummaryMessage`, create a normal user or assistant message containing exactly:

```python
f"{SUMMARY_PREFIX}\n{message.summary.strip()}\n\n{SUMMARY_END_MARKER}"
```

Mark the temporary message with the existing compressed-summary metadata key. Use an assistant message when the next ordinary message is user-role; otherwise use a user message. This envelope exists only for compressor input, so it is never persisted or sent directly to the provider. Do not mutate the persisted message or import private compressor methods.

- [ ] **Step 4: Route every compressor input through the adapter**

Apply it immediately before calls into `CompactionManager` from `CodingApp` and `AgentSession.compact()`. Persistence still writes one `compaction` entry through `SessionStore.append_compaction()`.

- [ ] **Step 5: Add repeated-cycle and no-duplication coverage**

Run at least five save/reload/compact cycles and assert each summary appears once in the next summarizer prompt. Assert sessions without compaction summaries are unchanged by identity where possible and by equality otherwise.

- [ ] **Step 6: Run integration tests without modifying redzone tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_compaction_integration.py appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_coding_agent.py -k "compact or compaction"
```

Expected: pass.

### Task 4: Active-Turn Compaction Coordination

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/compaction_coordinator.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:3205-3335`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py:849-885`
- Extend: `appV2.3.1/tests/test_compaction_integration.py`
- Extend: `appV2.3.1/tests/test_tui.py`

**Interfaces:**
- Produces: `CompactionCoordinator.prepare(timeout: float | None) -> Literal["ready", "deferred"]`
- Produces: `CompactionBusyError` for callers that require immediate completion
- Consumes: `Agent.run_lease`, `Agent.abort()`, and `Agent.wait_for_idle()`

- [ ] **Step 1: Write the active-turn regression**

```python
def test_manual_compaction_aborts_and_waits_for_active_turn(app):
    entered, release = blocking_provider_events()
    turn = threading.Thread(target=lambda: app.run_turn("long task"))
    turn.start()
    assert entered.wait(timeout=1)

    compacted = threading.Thread(target=lambda: app.session.compact())
    compacted.start()
    assert app.session.agent.signal.aborted is True
    release.set()
    turn.join(timeout=2)
    compacted.join(timeout=2)
    assert not turn.is_alive()
    assert not compacted.is_alive()
```

Add a same-owner test asserting `prepare()` returns `"deferred"` without waiting on itself.

- [ ] **Step 2: Verify the current race**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_compaction_integration.py -k active_turn
```

Expected before repair: compaction runs while streaming remains active.

- [ ] **Step 3: Implement coordination**

```python
def prepare(self, timeout: float | None = None) -> Literal["ready", "deferred"]:
    lease = self._agent.run_lease
    if not lease.active:
        return "ready"
    if lease.owned_by_current_thread:
        return "deferred"
    self._agent.abort()
    if not lease.wait(timeout):
        raise TimeoutError("Timed out waiting for the active run before compaction")
    return "ready"
```

Call this before manual compaction snapshots messages. TUI displays a deferred status rather than invoking compression concurrently.

- [ ] **Step 4: Keep automatic in-run compaction separate**

Preflight context transformation already runs under the active run owner. It uses a stable context snapshot and must not call `prepare()` or abort itself.

- [ ] **Step 5: Run compaction and TUI command tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_compaction_integration.py appV2.3.1/tests/test_tui.py -k "compact or compaction"
```

Expected: pass with no deadlock.

### Task 5: Session Tail Recovery

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/session_store.py:102-149,410-425`
- Create: `appV2.3.1/tests/test_session_store_recovery.py`

**Interfaces:**
- Produces: `SessionCorruptionError(path: Path, line_number: int, detail: str)`
- Produces: `SessionStore.recovered_tail_path: Path | None`
- Preserves: valid historical entries and public `build_context()` behavior

- [ ] **Step 1: Write tail and middle-corruption regressions**

```python
def test_load_recovers_only_truncated_final_record(tmp_path):
    path = tmp_path / "session.jsonl"
    header = b'{"type":"session","version":3,"id":"s","timestamp":"2026-07-10T00:00:00Z","cwd":"/work"}\n'
    message = b'{"type":"message","id":"m1","parentId":null,"timestamp":"2026-07-10T00:00:01Z","message":{"role":"user","content":"ok","timestamp":1}}\n'
    path.write_bytes(header + message + b'{"type":"message"')
    store = SessionStore(str(path), cwd=str(tmp_path))
    assert [entry["type"] for entry in store.file_entries] == ["session", "message"]
    assert store.recovered_tail_path.read_bytes() == b'{"type":"message"'
    assert path.read_bytes().endswith(b"\n")

def test_load_rejects_corruption_before_final_record(tmp_path):
    path = tmp_path / "session.jsonl"
    header = b'{"type":"session","version":3,"id":"s","timestamp":"2026-07-10T00:00:00Z","cwd":"/work"}\n'
    message = b'{"type":"message","id":"m1","parentId":null,"timestamp":"2026-07-10T00:00:01Z","message":{"role":"user","content":"ok","timestamp":1}}\n'
    path.write_bytes(header + b"not-json\n" + message)
    with pytest.raises(SessionCorruptionError, match="line 2"):
        SessionStore(str(path), cwd=str(tmp_path))
```

- [ ] **Step 2: Verify current behavior loses the whole load**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_session_store_recovery.py
```

Expected before repair: raw `JSONDecodeError` for the truncated tail.

- [ ] **Step 3: Implement bounded recovery**

Read bytes and retain line endings. If and only if the final non-empty record lacks a newline and fails JSON parsing:

1. write those exact bytes to a mode-`0600` sibling quarantine file
2. atomically replace the source with its valid prefix
3. load the valid entries
4. expose the quarantine path for diagnostics

All other parse failures raise `SessionCorruptionError` without modifying the source.

- [ ] **Step 4: Test reload and append after recovery**

Reopen the repaired session, append a message, reopen again, and assert all valid entries are present once.

- [ ] **Step 5: Run focused persistence tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_session_store_recovery.py appV2.3.1/tests/test_coding_agent.py -k "session_store or persists_and_reloads"
```

Expected: pass.

### Task 6: Data-Safety Gate

**Files:**
- Modify: none

**Interfaces:**
- Consumes all Task 1-5 deliverables
- Produces a verified baseline for the core-runtime plan

- [ ] **Step 1: Run the complete focused set**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_agent_runtime_hardening.py \
  appV2.3.1/tests/test_output_spool.py \
  appV2.3.1/tests/test_compaction_integration.py \
  appV2.3.1/tests/test_session_store_recovery.py \
  appV2.3.1/tests/test_app_integration.py
```

Expected: pass.

- [ ] **Step 2: Run the complete project suite**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
```

Expected: zero failures.

- [ ] **Step 3: Verify redzone and scope**

```bash
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: no output. Review `git diff --stat` and confirm only files named in this plan changed.
