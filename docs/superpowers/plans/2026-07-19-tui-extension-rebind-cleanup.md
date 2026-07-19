# TUI Extension Rebind Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove extension-owned TUI callbacks before a replacement session binds so stale contexts can never receive later terminal input.

**Architecture:** `ExtensionHostAdapter` gains one optional replacement-only `before_rebind` callback. Interactive mode wires its existing complete extension-UI reset into that callback; the existing `on_rebound` callback remains post-bind and refreshes ordinary TUI session state.

**Tech Stack:** Python 3.13, pytest, Travis234 TUI and extension runtime.

## Global Constraints

- Do not modify the agent loop, session runtime/persistence, providers, compaction, or context-envelope code.
- Do not weaken generation guards or catch the stale-context `RuntimeError`.
- Preserve initial binding and all non-TUI host behavior.
- Add failing regressions before production changes.
- Do not commit or push without a separate user request.

---

### Task 1: Prove host-adapter replacement ordering

**Files:**
- Modify: `tests/test_extension_host_runtime.py`
- Modify: `travis/coding_agent/extension_host.py`

**Interfaces:**
- Consumes: `ExtensionHostAdapter(app, *, mode, bindings_factory, on_rebound=None)`.
- Produces: optional `before_rebind: Callable[[object], object] | None`; it receives the replacement session and runs exactly once before `bind(session)`.

- [ ] **Step 1: Write the failing ordering test**

Extend the existing replacement test with an `events` list. Record the replacement callback as `("before", session.name)`, record binding inside `_FakeSession.bind_extensions`, and record `("after", session.name)`. Assert initial startup does not produce `before`, then assert replacement ordering is `before`, `bind`, `after`.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest -q -p no:cacheprovider tests/test_extension_host_runtime.py::test_extension_host_adapter_binds_initial_and_replacement_before_rebound_callback
```

Expected: failure because `ExtensionHostAdapter.__init__` does not accept `before_rebind`.

- [ ] **Step 3: Implement the minimal adapter hook**

Add the optional constructor argument, store it, and change `_handle_rebound` to:

```python
if self._before_rebind is not None:
    self._before_rebind(session)
self.bind(session)
if self._on_rebound is not None:
    self._on_rebound(session)
```

Do not invoke `before_rebind` in `start()` because initial startup has no old UI to dispose.

- [ ] **Step 4: Run the focused adapter test and verify GREEN**

Run the Step 2 command. Expected: one passing test.

### Task 2: Prove stale terminal listeners are removed during replacement

**Files:**
- Modify: `tests/test_extension_host_runtime.py`
- Modify: `travis/tui/interactive_mode.py`

**Interfaces:**
- Consumes: `_InteractiveRuntime._reset_extension_ui()` and the new `before_rebind` adapter argument.
- Produces: TUI replacement order `reset old extension UI -> bind replacement -> post ordinary rebound refresh`.

- [ ] **Step 1: Write the failing end-to-end TUI regression**

Create a project extension in `tmp_path/.travis234/extensions/listener.py`. Its `session_start` handler registers a terminal listener that appends the current `ctx.session_manager.session_id` to a module-level `SEEN` list. Initialize `InteractiveMode`, retain the first listener object, call `app.new_session()`, drain the TUI dispatcher, and assert:

```python
assert len(mode._terminal_input_listeners) == 1
assert old_listener not in mode._terminal_input_listeners
assert mode._dispatch_terminal_input("x") == (False, "x")
```

The listener must access its captured context so the pre-fix test reproduces the stale-generation failure instead of merely counting callbacks.

- [ ] **Step 2: Run the TUI regression and verify RED**

Run:

```bash
uv run pytest -q -p no:cacheprovider tests/test_extension_host_runtime.py::test_interactive_session_replacement_discards_stale_extension_terminal_listener
```

Expected: failure showing two listeners remain or the stale-context `RuntimeError` is raised.

- [ ] **Step 3: Wire TUI cleanup before replacement binding**

Pass this callback when constructing `ExtensionHostAdapter`:

```python
before_rebind=lambda _session: self._reset_extension_ui(),
```

Keep the existing `on_rebound=lambda _session: self.tui.post(self._rebind_session_ui)` unchanged so it remains post-bind.

- [ ] **Step 4: Run focused regressions and verify GREEN**

Run:

```bash
uv run pytest -q -p no:cacheprovider \
  tests/test_extension_host_runtime.py \
  tests/test_tui_commands_and_extensions.py
```

Expected: all tests in both files pass.

### Task 3: Verify scope and repository health

**Files:**
- Inspect only: production and test diff

**Interfaces:**
- Consumes: completed changes from Tasks 1 and 2.
- Produces: verified bug fix with no protected-layer drift.

- [ ] **Step 1: Run the exact AniFooter reproduction**

Use the existing read-only AniFooter trace: initialize with a temporary agent directory, replace the session, and dispatch one printable character. Expected: one listener after replacement, old listener absent, no exception.

- [ ] **Step 2: Run repository-level Python verification**

```bash
PYTHONPATH=. uv run --with "pytest>=8,<10" pytest -q -p no:cacheprovider tests
```

Expected: zero failures.

- [ ] **Step 3: Run required release-surface verification**

```bash
npm test --prefix packages/travis234-cli
uv build --clear
uv run twine check dist/*
docker build -f Dockerfile.release -t travis234:extension-rebind-smoke .
uv run python -m evals.container_smoke --image travis234:extension-rebind-smoke
```

Expected: npm tests, package build/check, Docker build, and container smoke all exit zero.

- [ ] **Step 4: Audit the final scope**

```bash
git diff --check
git diff --name-only
git status -sb
```

Expected: only the two production files, one regression file, and these two documents are changed; pre-existing `appv231/` remains untracked and untouched.
