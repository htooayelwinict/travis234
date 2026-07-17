# Travis234 State Signals Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one restrained, state-aware animated signal to the Travis234 TUI while keeping idle static and leaving Agent, context, compaction, provider, tool, and session behavior unchanged.

**Architecture:** Add cancellable owner-thread scheduling to `UiDispatcher`, then build a focused `MotionController` that owns one semantic animation sequence and sends its current indicator to `StatusLine`. Existing TUI lifecycle handlers explicitly publish motion state; extensions keep using existing presentation APIs, and the local `/motion` command controls only the current process.

**Tech Stack:** Python 3.10+, pytest, Travis234 differential ANSI renderer, existing semantic theme roles, existing extension UI compatibility surface.

## Global Constraints

- Preserve the current transcript-first layout and native terminal scrollback.
- Idle is completely static, with no scheduled animation callback.
- At most one indicator may animate across the entire TUI.
- Active motion is capped at four frames per second; retry countdown updates are capped at one per second.
- Do not add dependencies or core CPU/memory sampling.
- Do not modify `travis/agent/**`, provider payload construction, compaction algorithms, context construction, message schemas, tool schemas, session schemas, or agent-loop behavior.
- Animation must never use forced rendering or animate historical transcript rows.
- Keep `/motion` session-local; use `TRAVIS234_MOTION=0`, `NO_COLOR`, and `TERM=dumb` for static startup behavior.
- Work only in the repository root. Do not dispatch subagents.

---

## File map

- Create `travis/tui/motion.py`: semantic motion states, profiles, priority arbitration, frame scheduling, cancellation, and static fallbacks.
- Modify `travis/tui/dispatcher.py`: owner-thread delayed callbacks and cancellable handles.
- Modify `travis/tui/components/footer.py`: narrow indicator consumption by `StatusLine`.
- Modify `travis/tui/interactive_mode.py`: construct and own one motion controller.
- Modify `travis/tui/interactive_view.py`: retry, extension working indicator, and reusable motion helpers.
- Modify `travis/tui/interactive_command_dispatcher.py`: parse and dispatch `/motion`, turn start, and shutdown states.
- Modify focused `travis/tui/interactive_*.py` lifecycle owners only where an existing status changes between active and idle.
- Modify `travis/tui/__init__.py`: export the presentation types.
- Modify `tests/test_tui_dispatcher.py`: scheduler regression coverage.
- Create `tests/test_tui_motion.py`: controller, status rendering, lifecycle, command, extension, and isolation coverage.
- Modify `tests/test_tui_commands_and_extensions.py`: local command/help integration when the existing test harness is a better fit.
- Modify `README.md`: document State Signals, `/motion`, environment fallback, and extension telemetry guidance.

---

### Task 1: Owner-thread delayed callbacks

**Files:**
- Modify: `travis/tui/dispatcher.py`
- Test: `tests/test_tui_dispatcher.py`

**Interfaces:**
- Produces: `UiDispatcher.call_later(delay: float, callback: Callable[[], None]) -> ScheduledCall`
- Produces: `ScheduledCall.cancel() -> None`
- Preserves: `post`, `request_render`, `drain`, and `time_until_next_work` behavior

- [ ] **Step 1: Add failing scheduling tests**

Add tests that use the existing `FakeClock`:

```python
def test_dispatcher_runs_scheduled_callbacks_at_deadline_in_insertion_order() -> None:
    clock = FakeClock()
    observed: list[str] = []
    dispatcher = UiDispatcher(render=lambda force=False: None, clock=clock)
    dispatcher.call_later(0.25, lambda: observed.append("first"))
    dispatcher.call_later(0.25, lambda: observed.append("second"))

    assert dispatcher.time_until_next_work(1.0) == pytest.approx(0.25)
    dispatcher.drain()
    assert observed == []
    clock.advance(0.25)
    assert dispatcher.drain() == 2
    assert observed == ["first", "second"]


def test_dispatcher_cancelled_scheduled_callback_is_idempotent() -> None:
    clock = FakeClock()
    observed: list[str] = []
    dispatcher = UiDispatcher(render=lambda force=False: None, clock=clock)
    handle = dispatcher.call_later(0.1, lambda: observed.append("late"))
    handle.cancel()
    handle.cancel()
    clock.advance(0.1)

    assert dispatcher.drain() == 0
    assert observed == []
    assert dispatcher.time_until_next_work(0.5) == pytest.approx(0.5)


def test_scheduled_callback_can_request_one_coalesced_render() -> None:
    clock = FakeClock()
    renders: list[bool] = []
    dispatcher = UiDispatcher(render=lambda force=False: renders.append(force), clock=clock, render_interval=0)
    dispatcher.call_later(0.1, lambda: dispatcher.request_render())
    clock.advance(0.1)

    dispatcher.drain()

    assert renders == [False]
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `pytest -q tests/test_tui_dispatcher.py`

Expected: the new tests fail because `UiDispatcher` has no `call_later` method.

- [ ] **Step 3: Implement the delayed-callback heap**

Use `heapq`, a monotonic sequence counter, and a handle with an idempotent cancellation flag:

```python
class ScheduledCall:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def call_later(self, delay: float, callback: Callable[[], None]) -> ScheduledCall:
    handle = ScheduledCall(callback)
    with self._lock:
        self._schedule_sequence += 1
        heapq.heappush(
            self._scheduled,
            (self._clock() + max(0.0, float(delay)), self._schedule_sequence, handle),
        )
    return handle
```

Update `drain()` to pop and execute every non-cancelled due callback on the owner thread, including due-now callbacks scheduled reentrantly. Update `time_until_next_work()` to lazily discard cancelled heap entries and include the nearest deadline.

- [ ] **Step 4: Run dispatcher tests and confirm GREEN**

Run: `pytest -q tests/test_tui_dispatcher.py`

Expected: all dispatcher tests pass, including the existing serialization and reentrancy tests.

- [ ] **Step 5: Commit the scheduler task**

```bash
git add travis/tui/dispatcher.py tests/test_tui_dispatcher.py
git commit -m "feat: schedule owner-thread TUI callbacks"
```

---

### Task 2: Central semantic motion controller

**Files:**
- Create: `travis/tui/motion.py`
- Create: `tests/test_tui_motion.py`
- Modify: `travis/tui/__init__.py`

**Interfaces:**
- Consumes: `UiDispatcher.call_later`
- Produces: `MotionState`, `MotionController`, `MotionSnapshot`
- Produces: `set_signal(source: str, state: MotionState, *, countdown: int | None = None) -> None`
- Produces: `clear_signal(source: str) -> None`, `set_enabled(enabled: bool) -> None`, and `stop() -> None`

- [ ] **Step 1: Add failing controller tests**

Build a test harness from `FakeClock`, `UiDispatcher`, and an `indicators` list. Cover:

```python
def test_idle_motion_schedules_nothing() -> None:
    harness = MotionHarness()
    assert harness.controller.state is MotionState.IDLE
    assert harness.dispatcher.time_until_next_work(1.0) == 1.0
    assert harness.indicators == [""]


def test_working_motion_advances_at_four_fps_with_one_callback() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)
    first = harness.indicators[-1]
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(0.25)
    harness.clock.advance(0.25)
    harness.dispatcher.drain()
    assert harness.indicators[-1] != first
    assert harness.dispatcher.time_until_next_work(1.0) == pytest.approx(0.25)


def test_core_signal_outranks_extension_and_clear_restores_extension() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("extension", MotionState.EXTENSION)
    harness.controller.set_signal("turn", MotionState.WORKING)
    assert harness.controller.state is MotionState.WORKING
    harness.controller.clear_signal("turn")
    assert harness.controller.state is MotionState.EXTENSION


def test_disabled_static_and_stopped_motion_cancel_future_frames() -> None:
    harness = MotionHarness()
    harness.controller.set_signal("turn", MotionState.WORKING)
    harness.controller.set_enabled(False)
    settled = list(harness.indicators)
    harness.clock.advance(1.0)
    harness.dispatcher.drain()
    assert harness.indicators == settled
    harness.controller.stop()
    harness.controller.stop()
```

Also test retry's one-second cadence, equivalent signal updates without phase reset, stale-callback suppression, a non-repeating success/error profile that settles, and plain/static terminal mode.

- [ ] **Step 2: Run controller tests and confirm RED**

Run: `pytest -q tests/test_tui_motion.py`

Expected: collection fails because `travis.tui.motion` does not exist.

- [ ] **Step 3: Implement the minimal controller**

Define string-valued states and immutable profiles:

```python
class MotionState(str, Enum):
    IDLE = "idle"
    EXTENSION = "extension"
    WORKING = "working"
    TOOL = "tool"
    MAINTENANCE = "maintenance"
    RETRY = "retry"
    TERMINATING = "terminating"
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True)
class MotionProfile:
    frames: tuple[str, ...]
    interval: float
    repeat: bool = True
    static_frame: str = "·"


@dataclass(frozen=True)
class MotionSnapshot:
    state: MotionState
    indicator: str
    countdown: int | None
    generation: int
```

`MotionController` stores source-keyed signal claims, resolves the highest priority deterministically, exposes an immutable snapshot, emits an immediate frame, and keeps at most one cancellable scheduled handle. Every scheduled callback captures a generation and becomes a no-op after cancellation or state replacement. Retry uses a one-second interval and decrements its supplied countdown in the snapshot without scheduling a second timer.

- [ ] **Step 4: Export types and run controller tests**

Add `MotionController`, `MotionSnapshot`, and `MotionState` to `travis/tui/__init__.py`.

Run: `pytest -q tests/test_tui_motion.py tests/test_tui_dispatcher.py`

Expected: all tests pass.

- [ ] **Step 5: Commit the controller task**

```bash
git add travis/tui/motion.py travis/tui/__init__.py tests/test_tui_motion.py
git commit -m "feat: add semantic TUI motion controller"
```

---

### Task 3: StatusLine and runtime ownership

**Files:**
- Modify: `travis/tui/components/footer.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_view.py`
- Test: `tests/test_tui_motion.py`

**Interfaces:**
- Consumes: `MotionController(on_frame=..., request_render=...)`
- Produces: `_set_motion_signal(source, state, countdown=None)` and `_clear_motion_signal(source)` TUI-only helpers
- Preserves: `StatusLine.set_indicator()` and theme role rendering

- [ ] **Step 1: Add failing component and runtime tests**

Add tests asserting:

```python
def test_status_line_renders_exactly_one_motion_indicator_with_theme() -> None:
    theme, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    status = StatusLine("Running", theme_context=ThemeContext(theme))
    status.set_indicator("..")
    rendered = "".join(status.render(80))
    assert strip_ansi(rendered) == "status: .. Running"
    assert theme.foreground_ansi["text"] in rendered


def test_interactive_runtime_owns_one_motion_controller(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TRAVIS234_MOTION", raising=False)
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app, input_fn=lambda _prompt: "/exit")
    try:
        assert mode.motion_controller.enabled is True
        assert mode.motion_controller.state is MotionState.IDLE
    finally:
        mode.motion_controller.stop()
        mode.footer_data_provider.dispose()
        app.close()
```

Add parameterized startup tests for `TRAVIS234_MOTION=0`, `NO_COLOR`, and `TERM=dumb`.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `pytest -q tests/test_tui_motion.py`

Expected: runtime ownership assertions fail because no controller is constructed.

- [ ] **Step 3: Wire controller and static policy**

Construct `StatusLine` first, then construct one controller in `_InteractiveRuntime.__init__`:

```python
motion_enabled = os.environ.get("TRAVIS234_MOTION", "1").strip().lower() not in {"0", "false", "off", "no"}
motion_static = color_mode == "none"
self.motion_controller = MotionController(
    schedule=self.tui.dispatcher.call_later,
    on_frame=self.status.set_indicator,
    request_render=lambda: self.tui.request_render(),
    enabled=motion_enabled,
    static=motion_static,
)
```

Add narrow helper methods in `InteractiveView` so lifecycle files do not manipulate controller internals. `stop()` must be called before `tui.stop()` and must remain safe when `init()` was never called.

- [ ] **Step 4: Run focused runtime tests and confirm GREEN**

Run: `pytest -q tests/test_tui_motion.py tests/test_tui_terminal_and_input.py -k 'status or dispatcher or scrollback or motion'`

Expected: all selected tests pass and no native-scrollback assertion changes.

- [ ] **Step 5: Commit runtime ownership**

```bash
git add travis/tui/components/footer.py travis/tui/interactive_mode.py travis/tui/interactive_view.py tests/test_tui_motion.py
git commit -m "feat: render one global TUI state signal"
```

---

### Task 4: Lifecycle mappings, extension compatibility, and `/motion`

**Files:**
- Modify: `travis/tui/interactive_command_dispatcher.py`
- Modify: `travis/tui/interactive_turn_controller.py`
- Modify: `travis/tui/interactive_view.py`
- Modify: `travis/tui/interactive_extensions.py`
- Modify: `travis/tui/interactive_session_commands.py`
- Modify: `travis/tui/interactive_process_commands.py`
- Test: `tests/test_tui_motion.py`
- Test: `tests/test_tui_commands_and_extensions.py`

**Interfaces:**
- Produces: `_parse_motion_command(prompt: str) -> bool | None | object`, using a private sentinel for non-motion prompts
- Produces: `_run_motion_command(enabled: bool | None) -> None`
- Consumes: TUI motion helpers from Task 3
- Preserves: extension `setWorkingIndicator`, `setWorkingMessage`, `setWorkingVisible`, and `setStatus` spellings

- [ ] **Step 1: Add failing local-command and lifecycle tests**

Add parser tests:

```python
@pytest.mark.parametrize(
    ("prompt", "expected"),
    [("/motion", None), ("/motion on", True), ("/motion off", False)],
)
def test_parse_motion_command(prompt: str, expected: bool | None) -> None:
    assert _parse_motion_command(prompt) is expected
```

Add an integration run with inputs `['/motion off', '/motion', '/motion on', '/exit']` and a provider that fails the test if called. Assert the history reports disabled/current/enabled states and the model call count stays zero.

Add lifecycle tests that drive auto-retry, compaction status, turn start/settle, reload, and extension working indicator. Assert core state outranks extension state and clearing core restores extension state.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `pytest -q tests/test_tui_motion.py tests/test_tui_commands_and_extensions.py -k 'motion or working_indicator or retry or compact'`

Expected: parser/import or lifecycle assertions fail because mappings do not exist.

- [ ] **Step 3: Implement `/motion` as a local TUI command**

Parse only exact values:

```python
def _parse_motion_command(prompt: str) -> bool | None | object:
    if prompt == "/motion":
        return None
    if prompt == "/motion on":
        return True
    if prompt == "/motion off":
        return False
    return _NOT_MOTION_COMMAND
```

Dispatch it before unknown slash commands. Report usage for invalid arguments, update the controller without invoking `app.run_turn`, add `motion` to autocomplete, and add `/motion [on|off]` to `/help`.

- [ ] **Step 4: Add explicit lifecycle signals**

Use stable source keys:

```python
self._set_motion_signal("turn", MotionState.WORKING)
self._set_motion_signal("retry", MotionState.RETRY, countdown=seconds)
self._set_motion_signal("compaction", MotionState.MAINTENANCE)
self._set_motion_signal("tool", MotionState.TOOL)
self._set_motion_signal("extension", MotionState.EXTENSION)
self._set_motion_signal("shutdown", MotionState.TERMINATING)
```

Clear each source on its existing settled/error/finally path. Do not parse the visible status text. Normalize extension frame arrays through the shared extension state; never schedule extension-owned frames.

- [ ] **Step 5: Run focused command/lifecycle tests and confirm GREEN**

Run: `pytest -q tests/test_tui_motion.py tests/test_tui_commands_and_extensions.py tests/test_tui_runtime_compaction_and_models.py -k 'motion or working_indicator or retry or compact or extension'`

Expected: all selected tests pass with zero model turns for `/motion`.

- [ ] **Step 6: Commit lifecycle integration**

```bash
git add travis/tui/interactive_command_dispatcher.py travis/tui/interactive_turn_controller.py travis/tui/interactive_view.py travis/tui/interactive_extensions.py travis/tui/interactive_session_commands.py travis/tui/interactive_process_commands.py tests/test_tui_motion.py tests/test_tui_commands_and_extensions.py
git commit -m "feat: connect state signals to TUI lifecycle"
```

---

### Task 5: Isolation, documentation, and release verification

**Files:**
- Modify: `tests/test_tui_motion.py`
- Modify: `README.md`

**Interfaces:**
- Verifies: motion is presentation-only and leaves context/session values unchanged
- Documents: `/motion`, environment fallbacks, one-signal behavior, and extension footer telemetry

- [ ] **Step 1: Add isolation and scrollback regression tests**

Add tests that snapshot `app.messages`, context usage, compression count, and native scrollback render output before motion ticks; advance multiple frames; then assert the snapshots are unchanged and forced-redraw count has not increased. Parse `travis/tui/motion.py` with `ast` and assert it imports no module rooted at `travis.agent`, `travis.ai`, `travis.compaction`, or `travis.coding_agent`.

- [ ] **Step 2: Run isolation tests and confirm the boundary**

Run: `pytest -q tests/test_tui_motion.py -k 'isolation or context or scrollback or imports'`

Expected: all isolation tests pass. If one fails, treat it as a TUI regression and correct production code without weakening the assertion.

- [ ] **Step 3: Correct any TUI-only regression and rerun**

Limit corrections to TUI motion scheduling, lifecycle cleanup, or component invalidation. Do not relax assertions or modify Agent/runtime behavior.

Run: `pytest -q tests/test_tui_motion.py tests/test_tui_dispatcher.py tests/test_tui_terminal_and_input.py tests/test_tui_commands_and_extensions.py tests/test_tui_runtime_compaction_and_models.py`

Expected: all focused TUI tests pass.

- [ ] **Step 4: Update README usage**

Add concise documentation near the theme/TUI section:

```markdown
State Signals add one restrained animated status indicator while Travis234 is actively working; idle sessions are completely static. Use `/motion off` or `/motion on` for the current process, or start with `TRAVIS234_MOTION=0`. `NO_COLOR` and `TERM=dumb` automatically use static status text. Extensions can publish compact CPU/RAM or other telemetry through the existing footer status API, but Travis234 does not sample system metrics in core or run extension-owned animation loops.
```

- [ ] **Step 5: Run repository-level verification**

Run the repository's documented local gates exactly:

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
rm -rf dist build
python -m build
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Install the built wheel outside the checkout and smoke the console entry point:

```bash
rm -rf /tmp/travis234-motion-wheel
uv venv /tmp/travis234-motion-wheel
uv pip install --python /tmp/travis234-motion-wheel/bin/python dist/travis234-*.whl
/tmp/travis234-motion-wheel/bin/python -m pip check
(cd /tmp && /tmp/travis234-motion-wheel/bin/travis234 --help)
```

Run the installed real-PTY five-scenario acceptance protocol from README with isolated `/tmp/travis234-tui-acceptance` state, including `/motion off`, `/motion on`, active provider work, extension footer status, resize, selection/scrollback, and clean `/exit`. Never print `.env` contents or credentials.

When Docker is available, run the relevant release-container gate:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:state-signals-smoke .
python evals/container_smoke.py --image travis234:state-signals-smoke
```

Record exact pass counts and any explicitly unavailable optional environment.

- [ ] **Step 6: Review the final diff and commit implementation documentation**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~4..HEAD
```

Confirm `appv231/` remains untouched and no credential or `.env` content is staged.

```bash
git add README.md tests/test_tui_motion.py
git commit -m "docs: describe restrained TUI state signals"
```

Do not push without explicit user authorization.
