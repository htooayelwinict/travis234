# appv231 Persistent Session UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose appv231's existing JSONL persistence through Pi-compatible startup flags and TUI session commands.

**Architecture:** A coding-profile `SessionCatalog` owns discovery, metadata, and path/ID resolution. `CodingApp` composes the existing `AgentSessionRuntime` for replacement lifecycle, while `InteractiveMode` owns selection and UI rehydration. The generic agent core and compaction redzone remain unchanged.

**Tech Stack:** Python 3.13, argparse, pathlib, append-only JSONL, existing `SessionStore`, `AgentSessionRuntime`, `SessionCommandExecutor`, differential TUI, pytest, Docker/npm launcher.

## Global Constraints

- Do not modify `appV2.3.1/appv231/agent/` or `appV2.3.1/appv231/compaction/`.
- Preserve existing JSONL shape and default new persistent session startup.
- Keep discovery and replacement policy under `appv231.coding_agent` or `appv231.tui`.
- Run every production change through a witnessed red-green TDD cycle.
- Preserve explicit `--cwd` as an override; never silently substitute a missing session cwd.
- Do not print `.env`, credentials, or unbounded message content.
- Do not commit, push, publish, release, or mutate unrelated untracked artifacts.

---

### Task 1: Session Catalog and Shared Path Authority

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/session_catalog.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session_services.py:253-275`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session_runtime.py:328-345`
- Create: `appV2.3.1/tests/test_session_catalog.py`

**Interfaces:**
- Produces: `SessionInfo(path: Path, session_id: str, cwd: Path, created_at: datetime, modified_at: datetime, name: str | None, preview: str, model: str | None)`.
- Produces: `SessionCatalog(agent_dir: str, *, session_dir: str | None = None)`.
- Produces: `SessionCatalog.new_session_path(cwd: str, session_id: str | None = None) -> tuple[str, str]`.
- Produces: `SessionCatalog.list_for_cwd(cwd: str) -> list[SessionInfo]` and `list_all() -> list[SessionInfo]`.
- Produces: `SessionCatalog.continue_recent(cwd: str) -> SessionInfo`.
- Produces: `SessionCatalog.resolve(value: str, *, cwd: str, launch_dir: str) -> SessionInfo`.
- Produces: `SessionCatalog.diagnostics: tuple[str, ...]` for invalid files skipped during listing.

- [x] **Step 1: Add catalog resolution regressions**

Create fixtures with `SessionStore`, append distinct user messages/model changes,
and assert newest-first workspace filtering, exact path resolution, unique ID
resolution, ambiguous-ID failure, corrupt explicit-target failure, corrupt list
skipping, and deterministic tie ordering.

```python
def test_continue_recent_returns_latest_valid_session_for_exact_cwd(tmp_path):
    catalog = SessionCatalog(str(tmp_path / "agent"))
    older = write_session(catalog, tmp_path / "project", "older", modified_ns=10)
    newer = write_session(catalog, tmp_path / "project", "newer", modified_ns=20)
    write_session(catalog, tmp_path / "other", "other", modified_ns=30)

    assert catalog.continue_recent(str(tmp_path / "project")).path == newer
    assert older in [info.path for info in catalog.list_for_cwd(str(tmp_path / "project"))]
```

- [x] **Step 2: Witness the missing catalog**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_session_catalog.py
```

Expected: collection fails because `appv231.coding_agent.session_catalog` does not exist.

- [x] **Step 3: Implement read-only discovery and explicit validation**

Read JSONL incrementally, require the first record to be a session header, keep
only bounded preview metadata, and validate explicit targets with `SessionStore`.
Use `Path.stat().st_mtime_ns` for activity ordering and `(mtime_ns, path)` as the
stable sort key. Ignore `.lock`, `.partial`, and non-JSONL files.

```python
class SessionCatalog:
    def continue_recent(self, cwd: str) -> SessionInfo:
        sessions = self.list_for_cwd(cwd)
        if not sessions:
            raise SessionNotFoundError("No previous session for this workspace.")
        return sessions[0]
```

- [x] **Step 4: Replace private path generation with the catalog authority**

Keep `_new_session_path(cwd, agent_dir, session_id=None)` as the existing public
adapter, but delegate to `SessionCatalog(agent_dir).new_session_path(...)`.
Inject the catalog into `AgentSessionRuntime.services` and use it for `_next_session_path()`;
ephemeral `/new` must no longer fall back to `<cwd>/.appv231-sessions`.

- [x] **Step 5: Run catalog and persistence tests**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_session_catalog.py appV2.3.1/tests/test_session_store_recovery.py appV2.3.1/tests/test_coding_agent.py -k "session_path or session_store or runtime_switch or runtime_new"
```

Expected: pass with no JSONL format changes.

### Task 2: Pi-Compatible CLI Startup Modes

**Files:**
- Modify: `appV2.3.1/appv231/cli.py:326-455`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py:76-160,220-270`
- Extend: `appV2.3.1/tests/test_cli.py`

**Interfaces:**
- Produces: `_StartupSessionSelection(cwd: Path, session_path: str | None, persistent: bool, open_resume_picker: bool)`.
- Produces: `_resolve_startup_session(args, *, cwd: Path, cwd_was_explicit: bool, launch_dir: Path, catalog: SessionCatalog) -> _StartupSessionSelection`.
- Changes: `InteractiveMode(..., open_resume_picker: bool = False)`.

- [x] **Step 1: Add parser and resolution regressions**

Cover default-new, `-c/--continue`, `-r/--resume`, `--session path`,
`--session id`, `--no-session`, explicit-cwd override, missing stored cwd,
picker cancellation, and every mutually-exclusive pair.

```python
def test_cli_continue_passes_latest_session_without_creating_another(tmp_path, monkeypatch):
    session_path = seed_session(tmp_path)
    captured = capture_coding_app(monkeypatch)

    assert cli.main(["--cwd", str(tmp_path), "--continue", "--plain"]) == 0

    assert captured["session_path"] == str(session_path)
    assert list(session_path.parent.glob("*.jsonl")) == [session_path]
```

- [x] **Step 2: Witness unsupported flags**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_cli.py -k "continue_session or resume_session or exact_session or no_session"
```

Expected: fail because argparse rejects the new flags.

- [x] **Step 3: Add one mutually-exclusive argparse group**

```python
session_group = parser.add_mutually_exclusive_group()
session_group.add_argument("-c", "--continue", dest="continue_session", action="store_true")
session_group.add_argument("-r", "--resume", dest="resume_session", action="store_true")
session_group.add_argument("--session", dest="session_target")
session_group.add_argument("--no-session", action="store_true")
```

Change `--cwd` default to `None`, record whether it was explicitly supplied,
then resolve `args.cwd or "."`. For selected sessions, use the header cwd unless
the user supplied `--cwd`. Resolve dotenv after final cwd selection.

- [x] **Step 4: Route startup selection into `CodingApp`**

Default mode calls `catalog.new_session_path`; continue/exact modes pass the
selected path; no-session and resume-picker boot pass `None`. Pass
`open_resume_picker=True` only for `--resume`. Startup errors go through
`parser.error` and do not create a JSONL file.

- [x] **Step 5: Run all CLI tests**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_cli.py
```

Expected: pass.

### Task 3: CodingApp Session Replacement Composition

**Files:**
- Modify: `appV2.3.1/appv231/app.py:83-170`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session_runtime.py:45-175,328-345`
- Extend: `appV2.3.1/tests/test_app_integration.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Produces: `CodingApp.session_runtime: AgentSessionRuntime`.
- Produces: `CodingApp.switch_session(path: str, *, cwd_override: str | None = None) -> dict[str, bool]`.
- Produces: `CodingApp.new_session() -> dict[str, bool]`.
- Produces: `CodingApp.subscribe_session_rebound(listener: Callable[[AgentSession], None]) -> Callable[[], None]`.
- Guarantees: replacement reuses one provider control plane/settings profile and recreates session-local compaction state.

- [x] **Step 1: Add replacement integration regressions**

Prove that switching restores messages/model/thinking, updates app cwd, replaces
the compaction manager, keeps the provider-control-plane identity, unsubscribes
the old renderer, emits one rebind notification, and leaves the old session
active when target creation fails.

```python
def test_app_switch_session_rebinds_all_session_local_state(tmp_path):
    app, control_plane = make_app(tmp_path / "first")
    target = seed_restorable_session(tmp_path / "second")
    old_session = app.session
    old_compaction = app.compaction

    app.switch_session(str(target))

    assert app.session is not old_session
    assert app.compaction is not old_compaction
    assert app.session.provider_control_plane is control_plane
    assert message_texts(app.messages) == ["persisted marker"]
```

- [x] **Step 2: Witness absent app replacement API**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py -k "switch_session or new_session or session_rebound"
```

Expected: fail because `CodingApp` has no replacement API.

- [x] **Step 3: Extract session construction and binding inside `CodingApp`**

Store immutable construction inputs, create sessions through `_create_session(options)`,
and bind through `_bind_session(session)`. `_bind_session` recreates the
session-local compressor/manager, installs it on the new session, creates the
renderer with current tools/cwd, and stores unsubscribe callbacks. The model
summarizer continues to read `self.session` dynamically.

- [x] **Step 4: Compose `AgentSessionRuntime`**

Construct the runtime with services containing `cwd`, `agentDir`, and
`sessionCatalog`; set a before-invalidate callback to remove app-owned
subscriptions and a rebind callback to call `_bind_session` then notify app
listeners. Make replacement transactional by constructing and validating the
target session before disposing the old session; on failure, retain the old
session and subscriptions.

- [x] **Step 5: Run app/runtime tests**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_coding_agent.py -k "session_runtime or switch_session or new_session or session_rebound or compaction"
```

Expected: pass.

### Task 4: TUI Resume, New, and Session Commands

**Files:**
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py:76-205,220-410,869-925,1390-1430,1626-1700`
- Extend: `appV2.3.1/tests/test_tui.py`
- Extend: `appV2.3.1/tests/test_session_commands.py`

**Interfaces:**
- Produces: `_parse_session_command(prompt: str) -> Literal["resume", "new", "session"] | None`.
- Produces: `InteractiveMode._run_resume_command(*, startup: bool = False) -> bool`.
- Produces: `InteractiveMode._run_new_session_command() -> None`.
- Produces: `InteractiveMode._run_session_info_command() -> None`.
- Produces: `InteractiveMode._rebind_session_ui() -> None`.

- [x] **Step 1: Add command and UI lifecycle regressions**

Cover autocomplete/help entries, picker labels/order, cancellation, command
serialization, successful/failed switch, loaded-history replacement, footer cwd
and model changes, event subscription replacement, `/new`, `/session`, and
startup `--resume` before editor creation.

```python
def test_resume_switches_through_session_executor_and_rehydrates_history(mode, target):
    mode.prompt_extension_select = lambda *_args, **_kwargs: mode.session_label(target)

    mode._run_resume_command()

    assert mode.app.session.session_path == str(target.path)
    assert "persisted marker" in render_text(mode.history)
    assert mode.footer.cwd == str(target.cwd)
```

- [x] **Step 2: Witness slash commands are unknown**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py -k "resume_command or new_session_command or session_info_command"
```

Expected: fail because the command parser routes them to unknown-command status.

- [x] **Step 3: Add picker and serialized command handlers**

Build labels from immutable `SessionInfo` records and map each label back to its
record. `/resume` selects before submitting `app.switch_session(...)` through
`_run_session_command("resume", ...)`; `/new` uses the same executor. Cancellation
does not submit a command.

- [x] **Step 4: Rehydrate UI on app rebind**

Unsubscribe the old TUI session listener, clear history, set
`_history_populated=False`, attach the new renderer output container, repopulate
existing messages, rebuild autocomplete, update header/footer fields, subscribe
to the new session, scroll to bottom, and request one forced render on the UI
owner.

- [x] **Step 5: Add startup picker gate**

After `init()` starts the TUI but before creating the first editor, invoke
`_run_resume_command(startup=True)`. Return exit code `0` on cancellation and
never create a session file. A successful selection enters the ordinary prompt
loop with restored history.

- [x] **Step 6: Run TUI/session-command tests**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_session_commands.py -k "resume or new_session or session_info or history or footer or executor"
```

Expected: pass with all render mutations on the dispatcher owner.

### Task 5: Documentation and End-to-End Persistence Gates

**Files:**
- Modify: `appV2.3.1/README.md`
- Modify: `packages/appv231-cli/README.md`
- Extend: `packages/appv231-cli/test/appv231-cli.test.js`
- Create: `appV2.3.1/evals/session_resume_smoke.py`

**Interfaces:**
- Produces: deterministic `session_resume_smoke.py --python <path> --workspace <path> --agent-dir <path>`.
- Documents: source, npm, and container invocations for all session startup modes.

- [x] **Step 1: Add wrapper passthrough and two-launch smoke regressions**

Assert the npm launcher forwards `--continue`, `--resume`, `--session`, and
`--no-session` after `--`; assert two isolated CLI processes using one agent
directory restore a marker from the first process without creating a third
session.

- [x] **Step 2: Witness missing smoke behavior**

Run:

```bash
node --test packages/appv231-cli/test/appv231-cli.test.js
```

Expected before additions: existing tests pass but no session-persistence case is reported.

- [x] **Step 3: Implement the deterministic smoke harness and concise docs**

Use a faux provider for automated lifecycle proof. Record only session path,
session ID, restored message count, and exit codes. Document:

```bash
npx @htooayelwinict/appv231 -- --continue
npx @htooayelwinict/appv231 -- --resume
npx @htooayelwinict/appv231 -- --session <path-or-id>
npx @htooayelwinict/appv231 -- --no-session
```

- [ ] **Step 4: Run focused and full automated gates**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_session_catalog.py appV2.3.1/tests/test_cli.py appV2.3.1/tests/test_app_integration.py appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_session_commands.py
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests
npm --prefix packages/appv231-cli test
npm --prefix packages/appv231-cli run build
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: all tests/builds pass and redzone diff is empty.

- [ ] **Step 5: Run the 21-prompt actual source TUI protocol with persistence**

Use a fresh demo directory and isolated `APPV231_CODING_AGENT_DIR`. Start the
actual TUI with the configured OpenRouter Mimo model, thinking `medium`, and
temperature `0.2`. Send 21 natural, complex SDLC prompts one at a time in one
logical session; do not prefix prompts with scenario names or numbers. Exercise
`/compact` at protocol intervals. Exit the process mid-protocol, restart once
with `--continue`, exit again, restart with `--resume`, select the same session,
and finish all 21 prompts. Record each exact prompt and visible agent output,
selected model, session path/ID continuity, and process exit codes without
printing credentials.

- [ ] **Step 6: Run two-container persistence through the npm mount contract**

Run two separate `docker run --rm -it` processes against the production image,
mounting one fresh workspace and one fresh host agent-home. The first writes a
marker and exits; the second uses `--continue`, proves marker recall, runs
`/session`, and exits `0`. Verify exactly one continued JSONL session file
persists below the mounted `/agent-home/agent/sessions` tree.

## Completion Gate

- [ ] Every startup mode and TUI command is covered by a witnessed red-green cycle.
- [ ] Source and container TUI each prove cross-process continuation.
- [ ] Full Python suite, npm test, and npm build pass from the documented cwd.
- [ ] `git diff --exit-code -- appV2.3.1/appv231/compaction` exits `0`.
- [ ] `git status --short` contains no unexpected artifacts or tracked changes outside this plan.
