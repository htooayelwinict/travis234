# appv231 Session and TUI Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make session persistence transactional and ensure one owner serializes session commands, TUI state mutation, and terminal rendering.

**Architecture:** Strengthen `SessionStore` as a locked disk-first append log. Replace ad hoc turn threads and direct cross-thread UI calls with a session-command executor and a UI dispatcher. Producers enqueue typed events; only the UI owner mutates components or renders, with 16 ms render coalescing.

**Tech Stack:** Python 3.13, `fcntl`, threading, queue/Future, monotonic clocks, existing differential TUI and terminal abstractions, pytest.

## Global Constraints

- Complete Plans 1-4 first.
- Do not edit compaction files or perform mutating git operations; read-only status and diff checks are permitted.
- Preserve session JSONL format and current TUI visual output.
- Do not block the UI owner on network I/O.
- Do not render or mutate TUI components from provider, tool, timer, or file-watch threads.
- Session writes update memory only after disk commit.

---

### Task 1: Single-Writer Disk-First Session Log

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/session_lock.py`
- Modify: `appV2.3.1/appv231/coding_agent/session_store.py:102-149,170-425`
- Extend: `appV2.3.1/tests/test_session_store_recovery.py`
- Modify persistence expectations in: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Produces: `SessionFileLock(path: Path)` context manager
- Produces: `SessionStore.append_checkpoint(entry) -> str`
- Changes: `_append_entry(entry, durable: bool = False) -> str`
- Guarantees: disk append succeeds before `file_entries`, `by_id`, or `leaf_id` mutate

- [ ] **Step 1: Write write-failure and concurrent-writer regressions**

```python
def test_append_failure_does_not_mutate_memory(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    before_entries = list(store.file_entries)
    before_leaf = store.leaf_id
    monkeypatch.setattr(store, "_write_record", lambda *_args, **_kwargs: raise_(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.append_message(user_message("new"))
    assert store.file_entries == before_entries
    assert store.leaf_id == before_leaf

def test_two_store_instances_append_without_lost_or_torn_records(tmp_path):
    left = make_store(tmp_path)
    right = reopen_store(left.path, tmp_path)
    run_concurrently(
        lambda: left.append_message(user_message("left")),
        lambda: right.append_message(user_message("right")),
    )
    loaded = reopen_store(left.path, tmp_path)
    assert sorted(message_texts(loaded)) == ["left", "right"]
```

- [ ] **Step 2: Verify current memory-first append behavior**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_session_store_recovery.py -k "append_failure or two_store"
```

Expected before repair: memory changes despite failure or one writer loses/stales an entry.

- [ ] **Step 3: Implement locking and disk-first append**

Use an in-process `RLock` plus `flock` on a sibling `.lock` file. Under the lock, refresh the current disk leaf, assign ID/parent, serialize one complete UTF-8 JSON line, append it, flush it, and optionally `fsync`. Only after the file operation succeeds should the store update its projections.

```python
with self._thread_lock, SessionFileLock(self.path):
    entry = self._prepare_entry_against_disk_leaf(entry)
    self._write_record(entry, durable=durable)
    self._apply_committed_entry(entry)
```

- [ ] **Step 4: Define durability checkpoints**

Use `durable=True` for completed user/assistant turns, compaction entries, model/thinking changes, and explicit session replacement. Tool streaming updates remain in memory until represented by a completed message entry.

- [ ] **Step 5: Make rewrites atomic**

Session export/import rewrites write a sibling temporary file, flush/fsync, preserve mode, and `os.replace()` the target. Reuse the tail-recovery helper from Plan 1.

- [ ] **Step 6: Run persistence tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_session_store_recovery.py appV2.3.1/tests/test_coding_agent.py -k "session or persist or branch or export"
```

Expected: pass.

### Task 2: Session Command Executor

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/session_commands.py`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py:130-180,630-680,849-990`
- Extend: `appV2.3.1/tests/test_tui.py`

**Interfaces:**
- Produces: `SessionCommandExecutor.submit(name: str, callback: Callable[[], T]) -> Future[T]`
- Produces: `SessionCommandExecutor.busy: bool`
- Produces: `SessionCommandExecutor.close(wait: bool = True) -> None`
- Replaces: ad hoc `_turn_thread` ownership in interactive mode

- [ ] **Step 1: Write serialization and shutdown tests**

```python
def test_session_commands_execute_in_submission_order_on_one_owner_thread():
    executor = SessionCommandExecutor()
    observed: list[tuple[str, str]] = []
    futures = [
        executor.submit(name, lambda name=name: observed.append((name, threading.current_thread().name)))
        for name in ["turn", "compact", "model"]
    ]
    for future in futures:
        future.result(timeout=1)
    assert [name for name, _thread in observed] == ["turn", "compact", "model"]
    assert len({thread for _name, thread in observed}) == 1
    executor.close()
```

Add tests for exception propagation, cancellation before start, and clean close with one active command.

- [ ] **Step 2: Implement a single worker**

Use `queue.Queue`, one non-daemon owner thread, and `concurrent.futures.Future`. The worker sets result/exception and never touches TUI components directly.

- [ ] **Step 3: Route session mutations through commands**

Submit prompt/continue, manual compaction, model changes, auth changes affecting models, session replacement, and capability grants. Replace `_start_turn_thread()` with `submit("turn", ...)` and completion callbacks that enqueue UI events.

- [ ] **Step 4: Add explicit interactive consent command**

Parse exactly:

```text
/allow package-install
/allow package-install 3
```

Submit `session.grant_capability("package_mutation", uses)` through the executor. Reject unknown capability names and non-positive use counts without a provider turn.

- [ ] **Step 5: Run command-order tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py -k "turn_thread or session_command or compact or model_command or allow"
```

Expected: pass with no concurrent session mutations.

### Task 3: Single-Owner UI Dispatcher and Render Coalescing

**Files:**
- Create: `appV2.3.1/appv231/tui/dispatcher.py`
- Modify: `appV2.3.1/appv231/tui/tui.py:200-480`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Modify: `appV2.3.1/appv231/tui/terminal.py`
- Create: `appV2.3.1/tests/test_tui_dispatcher.py`
- Modify: `appV2.3.1/tests/test_tui.py` request-render expectations

**Interfaces:**
- Produces: `UiDispatcher(render, clock, owner_thread_id: int | None = None, render_interval=0.016)`; omitted owner defaults to the constructing thread
- Produces: `post(callback: Callable[[], None]) -> None`
- Produces: `request_render(force: bool = False) -> None`
- Produces: `drain() -> int`
- Produces: `time_until_next_work(default: float) -> float`
- Guarantees: `_do_render()` runs only on owner thread and never concurrently

- [ ] **Step 1: Write concurrent-producer and coalescing regressions**

```python
def test_dispatcher_serializes_and_coalesces_render_burst(fake_clock):
    active = 0
    max_active = 0
    renders = 0

    def render(_force=False):
        nonlocal active, max_active, renders
        active += 1
        max_active = max(max_active, active)
        renders += 1
        active -= 1

    dispatcher = UiDispatcher(render=render, clock=fake_clock, render_interval=0.016)
    run_in_20_threads(lambda: [dispatcher.request_render() for _ in range(100)])
    dispatcher.drain()
    assert max_active == 1
    assert renders == 1
```

Add tests proving force dominates a queued normal render and a second render occurs only after the interval when state changes again.

- [ ] **Step 2: Verify direct TUI calls overlap today**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui_dispatcher.py
```

Expected before repair: missing dispatcher or simultaneous render count exceeds one.

- [ ] **Step 3: Implement dispatcher ownership**

Producer methods only enqueue callbacks and set render flags under a condition lock. `drain()` asserts owner identity, applies queued state callbacks, and performs at most one due render using the strongest queued `force` flag.

- [ ] **Step 4: Integrate TUI request rendering**

On the owner thread, `TUI.request_render()` may render immediately when no coalescing window is active and return `RenderInfo` for compatibility. Off-owner calls enqueue and return `RenderInfo | None` from the last completed render; production callers do not depend on that return value.

- [ ] **Step 5: Enqueue all cross-thread producers**

Terminal input, provider/session events, tool updates, OSC timers, git-watch callbacks, loader timers, and session-command completion callbacks must post state mutations through the dispatcher.

- [ ] **Step 6: Drain from the interactive owner loop**

While waiting for prompt input, use the smaller of 50 ms and `dispatcher.time_until_next_work()` as the queue timeout, then call `dispatcher.drain()`. Force-drain before shutdown and terminal restoration.

- [ ] **Step 7: Run dispatcher and render tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui_dispatcher.py appV2.3.1/tests/test_tui.py -k "render or dispatcher or terminal_input or footer"
```

Expected: pass.

### Task 4: Asynchronous Model Discovery Using the Shared Control Plane

**Files:**
- Create: `appV2.3.1/appv231/tui/model_loader.py`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py:596-627,1750-1830`
- Modify: `appV2.3.1/appv231/coding_agent/provider_control_plane.py`
- Extend: `appV2.3.1/tests/test_tui_dispatcher.py`
- Modify: `appV2.3.1/tests/test_tui.py` model picker tests

**Interfaces:**
- Produces: `ModelCatalogLoader.load(query: str | None) -> Future[list[Model]]`
- Produces: `ModelCatalogLoader.cancel() -> None`
- Consumes: `ProviderControlPlane.models.get_selectable()` and remote catalog service
- Removes: synchronous `_openrouter_model_candidates()` call from UI owner

- [ ] **Step 1: Write nonblocking and stale-result tests**

```python
def test_model_picker_returns_before_remote_catalog_completes(interactive, blocking_catalog):
    started = time.monotonic()
    interactive.run_model_command("")
    assert time.monotonic() - started < 0.05
    assert interactive.model_picker.loading is True
    blocking_catalog.complete([model("remote")])
    interactive.ui_dispatcher.drain()
    assert interactive.model_picker.models == [model("remote")]

def test_cancelled_model_load_does_not_replace_newer_results(interactive):
    first = interactive.model_loader.load("old")
    second = interactive.model_loader.load("new")
    complete_future(first, [model("stale")])
    complete_future(second, [model("current")])
    interactive.ui_dispatcher.drain()
    assert interactive.model_picker.models == [model("current")]
```

- [ ] **Step 2: Implement generation-based cancellation**

The loader runs network discovery in a bounded one-worker executor. Each load captures a monotonically increasing generation; completion posts to the UI only when its generation remains current.

- [ ] **Step 3: Use one model authority**

Initial picker rows come from `control_plane.models.get_selectable(active)`. Merge remote metadata back through the control plane registry, then rerun the same eligibility method. Remove `_models_with_active_fallback` and TUI-specific selection logic.

- [ ] **Step 4: Run model picker tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui_dispatcher.py appV2.3.1/tests/test_tui.py -k "model_command or model_picker or openrouter"
```

Expected: pass without real network calls.

### Task 5: Session-Local Output-Cap Recovery

**Files:**
- Modify: `appV2.3.1/appv231/app.py:180-245`
- Extend: `appV2.3.1/tests/test_app_integration.py`
- Extend: `appV2.3.1/tests/test_provider_control_plane.py`

**Interfaces:**
- Produces: `AgentSession.with_model_overrides(max_tokens: int) -> Model`
- Guarantees: shared registry/catalog `Model` objects remain unchanged

- [ ] **Step 1: Write catalog-immutability regression**

```python
def test_output_cap_recovery_does_not_mutate_registry_model(app, control_plane):
    registered = control_plane.models.find(app.model.provider, app.model.id)
    original = registered.max_tokens
    trigger_output_cap_recovery(app)
    assert control_plane.models.find(app.model.provider, app.model.id).max_tokens == original
    assert app.session.model.max_tokens < original
```

- [ ] **Step 2: Verify current shared mutation**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py -k output_cap_recovery
```

Expected before repair: the shared model's `max_tokens` changes.

- [ ] **Step 3: Apply immutable override**

Use `dataclasses.replace(active_model, max_tokens=negotiated_cap)` or the equivalent immutable copy. Assign it only to session state and persist no catalog mutation.

- [ ] **Step 4: Run overflow/output-cap tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py -k "output_cap or overflow"
```

Expected: pass.

### Task 6: Session/TUI Gate

**Files:**
- Modify: none

**Interfaces:**
- Produces the stable interactive runtime consumed by live evaluation

- [ ] **Step 1: Stress concurrent UI producers**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_tui_dispatcher.py \
  appV2.3.1/tests/test_tui.py \
  appV2.3.1/tests/test_session_store_recovery.py
```

Expected: pass with max simultaneous render count `1`.

- [ ] **Step 2: Run app and CLI integration**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_cli.py
```

Expected: pass.

- [ ] **Step 3: Run the full suite and redzone check**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: zero failures and no redzone diff.
