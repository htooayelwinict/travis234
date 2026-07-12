# appv231 Responsive TUI Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep steering, `/allow`, user `!` commands, and cancellation responsive while an agent turn or managed-process wait is active.

**Architecture:** A coding-profile mailbox serializes external steering onto the existing agent run thread without changing the core queue. `/allow` mutates the already thread-safe capability store directly. A new `UserCommandController` runs `!`/`!!` through the app-owned process service with `origin="user"`, streams through the TUI dispatcher, and queues session recording without waiting on the active-turn executor.

**Tech Stack:** Python 3.13, `threading`, existing `SessionCommandExecutor`, `ProcessSessionService`, differential TUI dispatcher, pytest fake terminal/live terminal harness.

## Global Constraints

- Complete `2026-07-12-appv231-01-process-runtime-v2.md` first.
- Do not modify any file under `appV2.3.1/appv231/agent/`.
- Do not modify any file under `appV2.3.1/appv231/compaction/`.
- The TUI input/dispatcher owner thread must never wait on a Future queued behind an active turn.
- `/allow package-install` must affect the next protected tool call in the current turn.
- Preserve `!` inclusion and `!!` exclusion from future model context.
- Preserve extension `user_bash` hooks, command prefix, shell path, and custom operations.
- User processes use `ProcessOwner(origin="user")`; agent processes remain invisible to user-command internals except through `/processes`.
- One Ctrl-C routes to one focused operation; idle double-Ctrl-C exit behavior remains.
- Use red-green TDD and scoped commits only.

---

### Task 1: Thread-Safe CodingTurnMailbox

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/mailbox.py`
- Modify: `appV2.3.1/appv231/coding_agent/__init__.py`
- Create: `appV2.3.1/tests/test_coding_mailbox.py`

**Interfaces:**
- Produces: `QueuedCodingMessage` and `CodingTurnMailbox.enqueue`, `drain`, `acknowledge`, `clear`, `snapshot`, and `close`.
- Guarantees: FIFO ordering, stable queue IDs, duplicate-text identity, one-at-a-time/all modes, and no accepted-message loss during concurrent enqueue/drain.

- [ ] **Step 1: Write failing mailbox concurrency tests**

```python
def test_concurrent_enqueue_during_drain_is_not_lost() -> None:
    mailbox = CodingTurnMailbox()
    first = mailbox.enqueue("steering", "same text")
    barrier = threading.Barrier(2)
    drained: list[QueuedCodingMessage] = []

    def drain() -> None:
        barrier.wait(timeout=1)
        drained.extend(mailbox.drain("steering", mode="one-at-a-time"))

    thread = threading.Thread(target=drain)
    thread.start()
    barrier.wait(timeout=1)
    second = mailbox.enqueue("steering", "same text")
    thread.join(timeout=1)

    assert [item.id for item in drained] == [first.id]
    assert [item.id for item in mailbox.snapshot("steering")] == [second.id]
    assert first.id != second.id
```

Also cover close rejection, clear return values, follow-up isolation, all-mode
drain, and acknowledgment by ID rather than text.

- [ ] **Step 2: Run tests and witness the missing mailbox**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_mailbox.py
```

Expected: collection fails because `coding_agent.mailbox` does not exist.

- [ ] **Step 3: Implement typed locked queues**

```python
MailboxKind = Literal["steering", "follow_up"]


@dataclass(frozen=True)
class QueuedCodingMessage:
    id: str
    kind: MailboxKind
    text: str
    images: tuple[ImageContent, ...]


class CodingTurnMailbox:
    def __init__(self) -> None:
        self._items: dict[MailboxKind, deque[QueuedCodingMessage]] = {
            "steering": deque(),
            "follow_up": deque(),
        }
        self._inflight: dict[str, QueuedCodingMessage] = {}
        self._closed = False
        self._lock = threading.RLock()

    def enqueue(
        self, kind: MailboxKind, text: str, images: Sequence[ImageContent] | None = None
    ) -> QueuedCodingMessage:
        with self._lock:
            if self._closed:
                raise RuntimeError("coding turn mailbox is closed")
            item = QueuedCodingMessage(uuid.uuid4().hex, kind, text, tuple(images or ()))
            self._items[kind].append(item)
            return item

    def drain(self, kind: MailboxKind, *, mode: str) -> list[QueuedCodingMessage]:
        with self._lock:
            queue = self._items[kind]
            count = len(queue) if mode == "all" else min(1, len(queue))
            drained = [queue.popleft() for _ in range(count)]
            self._inflight.update((item.id, item) for item in drained)
            return drained
```

`acknowledge` removes only the matching inflight ID. `snapshot` returns queued
plus inflight items as an immutable tuple. `restore_unacknowledged` moves
inflight items back to the front in original FIFO order when an agent run ends
before their `message_start`. `close` rejects new messages but leaves accepted
items available for final drain.

- [ ] **Step 4: Stress the mailbox**

```bash
for run in 1 2 3 4 5 6 7 8 9 10; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_mailbox.py || exit 1
done
```

Expected: ten passes with every accepted ID observed exactly once.

- [ ] **Step 5: Commit the mailbox**

```bash
git add appV2.3.1/appv231/coding_agent/mailbox.py appV2.3.1/appv231/coding_agent/__init__.py appV2.3.1/tests/test_coding_mailbox.py
git commit -m "feat(appv231): add coding turn mailbox"
```

### Task 2: AgentSession Mailbox Adapter Without Core Changes

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`
- Extend: `appV2.3.1/tests/test_app_integration.py`

**Interfaces:**
- Consumes: Task 1 mailbox and existing `_prepare_next_turn` callback.
- Produces: queue-ID-bearing user messages transferred to core queues only on the run thread.
- Guarantees: active-turn steering cannot race core drain; equal text remains distinct; internal guardrail steering remains immediate.

- [ ] **Step 1: Add a deterministic lost-steering regression**

Use barriers around provider response and `_prepare_next_turn`, not sleeps.

```python
def test_concurrent_external_steering_is_delivered_once_in_fifo_order(tmp_path: Path) -> None:
    provider_entered = threading.Event()
    release_provider = threading.Event()
    seen_user_text: list[str] = []

    def provider(model, context):
        seen_user_text.extend(all_user_text(context.messages))
        provider_entered.set()
        release_provider.wait(timeout=2)
        return text_response_events(model, "continue")

    session = AgentSession(cwd=str(tmp_path), model=faux_model(), stream_fn=provider)
    turn = threading.Thread(target=lambda: session.prompt("initial"))
    turn.start()
    assert provider_entered.wait(timeout=1)
    first = session.steer("duplicate")
    second = session.steer("duplicate")
    release_provider.set()
    turn.join(timeout=2)

    assert first != second
    assert seen_user_text.count("duplicate") == 2
    assert session.pending_message_count == 0
```

- [ ] **Step 2: Run the regression repeatedly and witness loss/identity failure**

```bash
for run in 1 2 3 4 5; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_agent.py -k concurrent_external_steering || exit 1
done
```

Expected before implementation: missing return IDs or a duplicate message is
lost/removed by text.

- [ ] **Step 3: Route active external messages into the mailbox**

```python
def steer(self, text: str, images: list[ImageContent] | None = None) -> str:
    self._raise_if_extension_command(text)
    if not self.agent.state.is_streaming:
        message = _user_message(text, images)
        self.agent.steer(message)
        return _ensure_queue_id(message)
    queued = self._turn_mailbox.enqueue("steering", text, images)
    self._emit_queue_update()
    return queued.id


def follow_up(self, text: str, images: list[ImageContent] | None = None) -> str:
    self._raise_if_extension_command(text)
    if not self.agent.state.is_streaming:
        message = _user_message(text, images)
        self.agent.follow_up(message)
        return _ensure_queue_id(message)
    queued = self._turn_mailbox.enqueue("follow_up", text, images)
    self._emit_queue_update()
    return queued.id
```

Remove the parallel `_steering_messages`/`_follow_up_messages` text lists.

- [ ] **Step 4: Flush mailbox on the existing run-thread hook**

At the start of `_prepare_next_turn`, transfer accepted messages into unchanged
core queues:

```python
def _flush_turn_mailbox(self) -> None:
    for kind, mode, sender in (
        ("steering", self.agent.steering_mode, self.agent.steer),
        ("follow_up", self.agent.follow_up_mode, self.agent.follow_up),
    ):
        for queued in self._turn_mailbox.drain(kind, mode=mode):
            message = _user_message(queued.text, list(queued.images))
            setattr(message, "_coding_queue_id", queued.id)
            sender(message)
```

Call this before rebuilding `AgentLoopTurnUpdate`. In `message_start`, update UI
queue state by `_coding_queue_id`, never by message text.

In `agent_end`, call `restore_unacknowledged()` after all delivered
`message_start` events have acknowledged their IDs. This keeps a steering
message pending if an abort/error ends the run before the core queue consumes
it.

- [ ] **Step 5: Preserve queue inspection APIs**

`pending_message_count`, `get_steering_messages`, `get_follow_up_messages`, and
`clear_queue` read mailbox snapshots plus core queues only when idle. Return
text for public compatibility and IDs in internal queue events.

- [ ] **Step 6: Run coding-session and integration tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_coding_mailbox.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_app_integration.py \
  -k "steer or follow_up or queue or process"
```

Expected: pass with no modification under `appv231/agent/`.

- [ ] **Step 7: Commit the mailbox adapter**

```bash
git add appV2.3.1/appv231/coding_agent/agent_session.py appV2.3.1/tests/test_coding_agent.py appV2.3.1/tests/test_app_integration.py
git commit -m "fix(appv231): serialize external steering"
```

### Task 3: Nonblocking `/allow` During Active Turns

**Files:**
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Extend: `appV2.3.1/tests/test_tui.py`
- Extend: `appV2.3.1/tests/test_coding_policy.py`

**Interfaces:**
- Consumes: already locked `TurnCapabilities.grant`.
- Produces: immediate capability acknowledgment without `SessionCommandExecutor`.
- Guarantees: a grant entered during a blocked provider/tool wait is available to the next package mutation in that same turn.

- [ ] **Step 1: Add an active-turn grant regression**

```python
def test_allow_grants_during_active_turn_without_waiting_for_turn_executor(tmp_path: Path) -> None:
    mode, release_turn = active_turn_mode(tmp_path)
    submitted = threading.Event()

    thread = threading.Thread(
        target=lambda: (mode._run_allow_command("package-install", 1), submitted.set())
    )
    thread.start()

    assert submitted.wait(timeout=0.25)
    assert mode.app.session._turn_capabilities.remaining("package_mutation") == 1
    assert release_turn.is_set() is False
```

- [ ] **Step 2: Run and confirm the call blocks behind `turn`**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py -k allow_grants_during_active
```

Expected: timeout because `_run_allow_command` waits on the occupied executor.

- [ ] **Step 3: Call the thread-safe grant directly**

```python
def _run_allow_command(self, capability: str, uses: int) -> None:
    if capability != "package-install":
        self._show_status(f"Unknown capability: {capability}", kind="error")
        return
    if uses <= 0:
        self._show_status("Capability use count must be a positive integer", kind="error")
        return
    try:
        self.app.session.grant_capability("package_mutation", uses)
    except Exception as error:
        self._show_status(f"Capability grant failed: {error}", kind="error")
        return
    suffix = "use" if uses == 1 else "uses"
    self._show_status(f"Allowed package installation for {uses} {suffix}", kind="auth")
```

Retain event-trace emission after the successful grant.

- [ ] **Step 4: Prove same-turn policy consumption**

Add a policy test that pauses before the protected tool call, grants from a
second thread, then asserts the package call executes once and remaining uses
become zero.

- [ ] **Step 5: Run TUI and policy tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_coding_policy.py \
  -k "allow or package_mutation"
```

Expected: pass without releasing the active-turn fixture first.

- [ ] **Step 6: Commit nonblocking capability grants**

```bash
git add appV2.3.1/appv231/tui/interactive_mode.py appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_coding_policy.py
git commit -m "fix(appv231): grant capabilities during active turns"
```

### Task 4: Asynchronous UserCommandController

**Files:**
- Create: `appV2.3.1/appv231/tui/user_commands.py`
- Create: `appV2.3.1/tests/test_tui_user_commands.py`
- Extend: `appV2.3.1/tests/test_process_service.py`

**Interfaces:**
- Consumes: shared `ProcessSessionService`, user owner factory, an execution resolver, and TUI callbacks.
- Produces: `UserCommandBinding`, `ResolvedUserCommand`, `UserCommandHandle`, and `UserCommandController.start`, `interrupt_focused`, `terminate`, `list`, and `close`.
- Guarantees: start returns before extension resolution or command completion; output uses dispatcher callbacks; managed completion callback runs once; focused cancellation is deterministic; all user execution variants share a four-command controller limit.

- [ ] **Step 1: Write failing controller tests**

```python
def test_start_returns_before_command_finishes_and_streams_to_dispatcher(fake_service) -> None:
    output: list[tuple[str, str]] = []
    completed: list[tuple[UserCommandHandle, BashResult]] = []
    controller = UserCommandController(
        service=fake_service,
        owner_factory=lambda: ProcessOwner("app", "/workspace", "user"),
        resolver=lambda command, binding, signal: ResolvedUserCommand.managed(
            fake_request_factory(command, binding)
        ),
        on_output=lambda command_id, text: output.append((command_id, text)),
        on_complete=lambda handle, result: completed.append((handle, result)),
        on_error=lambda handle, message: pytest.fail(message),
    )
    binding = UserCommandBinding(
        session=fake_agent_session(),
        session_id="session-a",
        session_path="/sessions/a.jsonl",
        exclude_from_context=False,
    )

    handle = controller.start("long command", binding)
    assert handle.command_id.startswith("user_")
    assert completed == []

    assert wait_until(lambda: controller.inspect(handle.command_id).process_id is not None)
    process_id = controller.inspect(handle.command_id).process_id
    fake_service.emit_output(process_id, "progress\n")
    fake_service.exit(process_id, 0)
    assert wait_until(lambda: len(completed) == 1)
    assert "progress" in "".join(text for _, text in output)
```

Also cover a resolver blocked on a barrier while `start` returns within 250 ms,
two concurrent commands, immediate extension results, cooperative custom
operations, focused selection, interrupt once before/after managed launch,
close termination, nonzero exit, cancellation, output truncation, resolver
failure, callback exception isolation, and rejection of a fifth active command
even when the first four are custom extension operations.

- [ ] **Step 2: Run tests and witness the missing controller**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui_user_commands.py
```

Expected: collection fails because `tui.user_commands` does not exist.

- [ ] **Step 3: Define immutable binding and handle contracts**

```python
@dataclass(frozen=True)
class UserCommandBinding:
    session: AgentSession = field(repr=False, compare=False)
    session_id: str | None
    session_path: str | None
    exclude_from_context: bool


@dataclass(frozen=True)
class UserCommandHandle:
    command_id: str
    binding: UserCommandBinding
    command: str


@dataclass(frozen=True)
class ResolvedUserCommand:
    result: BashResult | None = None
    managed_request: ProcessLaunchRequest | None = None
    custom_runner: Callable[[AbortSignal, Callable[[str], None]], BashResult] | None = None
```

Validate that exactly one `ResolvedUserCommand` execution variant is present.
Commands are retained only in controller memory and the eventual
`BashExecutionMessage`; they are not added to process completion metadata.

- [ ] **Step 4: Implement immediate acceptance and background resolution/drain**

```python
def start(self, command: str, binding: UserCommandBinding) -> UserCommandHandle:
    handle = UserCommandHandle(f"user_{uuid.uuid4().hex}", binding, command)
    state = _UserCommandState(
        handle=handle,
        owner=self._owner_factory(),
        signal=AbortSignal(),
    )
    with self._lock:
        if self._closed:
            raise RuntimeError("user command controller is closed")
        if len(self._states) >= self._max_active:
            raise UserCommandLimitError(f"Reached active user command limit of {self._max_active}")
        self._states[handle.command_id] = state
        self._focused_id = handle.command_id
    thread = threading.Thread(target=self._run, args=(state,), daemon=True)
    state.thread = thread
    thread.start()
    return handle
```

`_run` invokes the resolver on this worker, never on the input/dispatcher
thread. An immediate result completes directly. A custom runner receives the
per-command abort signal and bounded output callback. A managed resolution
starts `ProcessSessionService` with `origin="user"`, records its opaque process
ID in state, then calls `wait_terminal(..., wait_ms=900_000)` repeatedly only
when the host deadline expires. Its update callback advances the cursor and
emits only new output keyed by `command_id`. Convert terminal state to
`BashResult` and invoke completion exactly once in `finally`.

- [ ] **Step 5: Implement focused interruption and bounded close**

```python
def interrupt_focused(self) -> bool:
    with self._lock:
        state = self._states.get(self._focused_id or "")
        if state is None or state.interrupt_requested:
            return False
        state.interrupt_requested = True
        process_id = state.process_id
    state.signal.abort()
    if process_id is not None:
        self._service.interrupt(state.owner, process_id, wait_ms=0)
    return True
```

`close()` rejects new starts, terminates remaining user-origin jobs, joins
managed workers within the process-service grace bound, and still delivers one
terminal callback per accepted managed command. Custom extension runners must
honor their `AbortSignal`; an uncooperative runner is marked abandoned once,
emits a bounded extension error, and cannot block process exit because its
worker is daemonized.

- [ ] **Step 6: Run controller tests repeatedly**

```bash
for run in 1 2 3 4 5; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    appV2.3.1/tests/test_tui_user_commands.py appV2.3.1/tests/test_process_service.py \
    -k "user_command or user_origin" || exit 1
done
```

Expected: five passes and no controller threads survive fixture teardown.

- [ ] **Step 7: Commit the controller**

```bash
git add appV2.3.1/appv231/tui/user_commands.py appV2.3.1/tests/test_tui_user_commands.py appV2.3.1/tests/test_process_service.py
git commit -m "feat(appv231): run user shell commands asynchronously"
```

### Task 5: Integrate Async `!`/`!!` and Session Recording

**Files:**
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Modify: `appV2.3.1/appv231/app.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Extend: `appV2.3.1/tests/test_tui.py`
- Extend: `appV2.3.1/tests/test_app_integration.py`

**Interfaces:**
- Consumes: Task 4 controller and existing extension `user_bash` event.
- Produces: nonblocking start, dispatcher-only rendering, and launch-session recording.
- Guarantees: completion cannot mutate the wrong resumed session, the TUI never waits on the active turn to start a user command, and `/processes` can inspect/control both agent- and user-origin jobs without weakening owner authorization.

- [ ] **Step 1: Add active-turn and cross-session persistence regressions**

```python
def test_bang_runs_while_agent_waits_and_records_after_turn(tmp_path: Path) -> None:
    mode, release_turn = active_turn_mode(tmp_path)
    started_at = time.monotonic()
    mode._run_bash_command("printf user", exclude_from_context=False)

    assert time.monotonic() - started_at < 0.25
    assert release_turn.is_set() is False
    assert wait_until(lambda: "user" in render_text(mode.history))
    release_turn.set()
    mode._wait_for_active_turn()
    assert any(message.role == "bashExecution" for message in mode.app.session.messages)


def test_bang_completion_records_against_launch_session_after_resume(tmp_path: Path) -> None:
    mode, first_path, second_path = mode_with_two_sessions(tmp_path)
    mode._run_bash_command("delayed", exclude_from_context=False)
    mode.app.switch_session(str(second_path))
    finish_user_command(mode, output="done")

    assert "done" in bash_outputs(SessionStore(str(first_path), cwd=str(tmp_path)).build_context().messages)
    assert "done" not in bash_outputs(SessionStore(str(second_path), cwd=str(tmp_path)).build_context().messages)


def test_slow_user_bash_extension_resolution_does_not_block_input(tmp_path: Path) -> None:
    mode = idle_mode(tmp_path)
    release = threading.Event()

    def slow_handler(_event):
        release.wait(timeout=2)
        return None

    mode.app.session.extension_runner.on("user_bash", slow_handler)

    try:
        started = time.monotonic()
        mode._run_bash_command("printf extension", exclude_from_context=False)

        assert time.monotonic() - started < 0.25
        assert mode._run_allow_command("package-install", 1) is None
    finally:
        release.set()
        mode._user_commands.close()
        mode.app.close()


def test_processes_combines_agent_and_user_owners_and_controls_selected_owner(tmp_path: Path) -> None:
    mode, agent_process, user_process = mode_with_agent_and_user_processes(tmp_path)
    mode.prompt_extension_select = select_user_process_then_terminate

    mode._run_processes_command()

    assert rendered_process_ids(mode) == {agent_process.session_id, user_process.session_id}
    assert mode.app.process_service.control_calls[-1].owner.origin == "user"
    assert mode.app.process_service.control_calls[-1].session_id == user_process.session_id
```

- [ ] **Step 2: Run tests and confirm active-turn blocking**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py \
  -k "bang_runs_while_agent or bang_completion_records or slow_user_bash_extension or processes_combines"
```

Expected: the first test blocks in `Future.result()`, cross-session ownership
is unavailable, or `/processes` cannot see/control the user-origin job.

- [ ] **Step 3: Build controller callbacks through the dispatcher**

InteractiveMode creates one controller with callbacks:

```python
self._user_commands = UserCommandController(
    service=self.app.process_service,
    owner_factory=lambda: self.app.process_owner(origin="user"),
    resolver=self._resolve_user_command,
    transport_factory=self.app.user_command_transport,
    on_output=lambda command_id, text: self.tui.post(
        lambda: self._append_user_command_output(command_id, text)
    ),
    on_complete=lambda handle, result: self.tui.post(
        lambda: self._finish_user_command(handle, result)
    ),
    on_error=lambda handle, message: self.tui.post(
        lambda: self._fail_user_command(handle.command_id, message)
    ),
)
```

`CodingApp.user_command_request()` applies existing settings, environment, and
execution backend without exposing internals to the TUI module.

Update `_run_processes_command` to build immutable `(owner, snapshot)` rows for
both `self.app.process_owner(origin="agent")` and
`self.app.process_owner(origin="user")`. Include the origin in each visible
label and invoke poll/signal/terminate/kill with the selected row's owner. Never
list a foreign workspace/app owner, and never let the model-facing process tool
query the user owner. Merge controller-only `starting`/custom-operation rows by
`command_id`, deduplicate controller rows that already have a managed process
snapshot, and offer only `Interrupt` for a custom operation with no process ID.

- [ ] **Step 4: Preserve extension user-bash behavior**

`_run_bash_command` captures the current `AgentSession` in
`UserCommandBinding`, creates the component, calls `controller.start`, and
returns. The controller worker calls `_resolve_user_command`, which dispatches
`binding.session.extension_runner.emit_user_bash` in original handler order.
Immediate extension results become `ResolvedUserCommand.result`; custom
operations become a cooperative `custom_runner`; the default becomes a managed
request using the launch session's command prefix, shell path, environment,
spawn hook, and execution backend. No extension dispatch or operation runs on
the input thread, and no path submits bash to the occupied turn executor.

- [ ] **Step 5: Queue recording without waiting**

The immutable handle retains the strong launch-session binding until completion.
On completion:

```python
def _finish_user_command(self, handle: UserCommandHandle, result: BashResult) -> None:
    component = self._user_command_components.pop(handle.command_id, None)
    if component is not None:
        component.set_complete(result.exit_code, result.cancelled, result.truncated, result.full_output_path)
    self._command_executor().submit(
        "record-user-bash",
        lambda: handle.binding.session.record_bash_result(
            handle.command,
            result,
            {"excludeFromContext": handle.binding.exclude_from_context},
        ),
    )
```

Do not call `.result()`. The callback closure keeps the captured session alive
until recording finishes. A disposed old session may append to its own
SessionStore but must not emit UI events into the new session.

- [ ] **Step 6: Close controller before session executor**

In TUI teardown: stop accepting commands, terminate/join user jobs, drain posted
completion callbacks, then close `SessionCommandExecutor`. This guarantees
accepted command results are recorded before app close deletes live spools.

- [ ] **Step 7: Run TUI, app, extension, and persistence tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_tui_user_commands.py \
  appV2.3.1/tests/test_tui.py \
  appV2.3.1/tests/test_app_integration.py \
  appV2.3.1/tests/test_coding_agent.py \
  -k "bashExecution or user_bash or bang or process or session_replacement"
```

Expected: pass; `!!` remains absent from provider context.

- [ ] **Step 8: Commit TUI integration**

```bash
git add appV2.3.1/appv231/tui/interactive_mode.py appV2.3.1/appv231/app.py appV2.3.1/appv231/coding_agent/agent_session.py appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_app_integration.py
git commit -m "feat(appv231): keep user shell responsive"
```

### Task 6: Deterministic Ctrl-C Routing and Plan Acceptance

**Files:**
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Extend: `appV2.3.1/tests/test_tui.py`
- Extend: `appV2.3.1/tests/test_tui_user_commands.py`

**Interfaces:**
- Consumes: active overlay state, focused user command, agent run state.
- Produces: one cancellation target per keypress.
- Guarantees: no repeated Ctrl-C is needed to regain input; idle exit contract remains.

- [ ] **Step 1: Add cancellation-priority regressions**

```python
def test_ctrl_c_interrupts_focused_user_command_without_aborting_agent(tmp_path: Path) -> None:
    mode = mode_with_active_turn_and_user_command(tmp_path)
    mode._handle_editor_escape()

    assert mode._user_commands.focused_interrupt_count == 1
    assert mode.app.session.agent.signal.aborted is False
    assert mode.status.message == "Interrupting user command"


def test_ctrl_c_aborts_agent_once_when_no_user_command_is_focused(tmp_path: Path) -> None:
    mode = mode_with_active_turn(tmp_path)
    mode._handle_editor_escape()
    mode._handle_editor_escape()
    assert mode.app.session.agent.signal.aborted is True
    assert mode.agent_abort_requests == 1
```

- [ ] **Step 2: Run tests and witness ambiguous/double routing**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_tui.py -k "focused_user_command or aborts_agent_once"
```

Expected: failures because user command focus does not exist and the current
handler can route to multiple cancellation paths.

- [ ] **Step 3: Implement one-target routing**

```python
def _handle_editor_escape(self) -> None:
    if self._cancel_focused_overlay():
        return
    if self._user_commands.interrupt_focused():
        self.status.set_message("Interrupting user command")
        self._refresh_footer()
        self.tui.request_render()
        return
    if self._is_turn_active() or self.app.session.is_streaming:
        if not self._agent_abort_requested:
            self._agent_abort_requested = True
            self.status.set_message("Aborting")
            self.app.session.agent.abort()
        self._refresh_footer()
        self.tui.request_render()
        return
    self._handle_idle_escape()
```

Reset `_agent_abort_requested` only when the turn completion callback reaches
idle. Preserve existing idle double-Ctrl-C timing.

- [ ] **Step 4: Run the full focused TUI concurrency set repeatedly**

```bash
for run in 1 2 3 4 5; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    appV2.3.1/tests/test_tui_user_commands.py \
    appV2.3.1/tests/test_tui.py \
    -k "ctrl_c or allow or bang or active_turn or user_command or steering" || exit 1
done
```

Expected: five passes without blocked test threads.

- [ ] **Step 5: Prove redzone integrity**

```bash
if git diff --name-only 96b38b9..HEAD | rg '^appV2\.3\.1/appv231/(agent|compaction)/'; then
  echo 'redzone modified' >&2
  exit 1
fi
```

Expected: no output and exit zero.

- [ ] **Step 6: Commit cancellation routing**

```bash
git add appV2.3.1/appv231/tui/interactive_mode.py appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_tui_user_commands.py
git commit -m "fix(appv231): route TUI cancellation once"
```
