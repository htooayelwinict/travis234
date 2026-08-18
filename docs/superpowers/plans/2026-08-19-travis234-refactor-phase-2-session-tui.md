# Phase 2 — Session and TUI Collaborator Extraction

> **Required skills:** `superpowers:executing-plans`,
> `superpowers:test-driven-development`, `superpowers:systematic-debugging`, and
> `superpowers:verification-before-completion`.

**Goal:** Remove behavior mixin inheritance from `_SessionRuntime` and
`_InteractiveRuntime`, replacing it with explicit collaborator objects while preserving
all session, extension, command, process, subagent, LSP, memory, operation, and turn
behavior.

**Architecture:** Each collaborator receives a narrow state/service port. Runtime
composition owns a typed controller bundle and explicitly delegates supported methods.
Private compatibility overrides continue to land on the runtime and are observed through
injected callback ports. Ownership moves one domain group at a time.

**Do not modify:** `travis/agent/agent_loop.py`, JSONL serialization, provider wire code,
public façade names, command names/aliases, tool schemas, extension event order.

## Extraction rule

For every group below:

1. Add/expand characterization tests before moving code.
2. Run them against the old mixin implementation and record GREEN.
3. Convert only that group to owned collaborators.
4. Run focused and architecture tests.
5. Build/install a wheel and run the named normal-user TUI scenario.
6. Commit the group separately.

Do not clean unrelated imports during a behavior move. Import cleanup happens after all
controllers are composed and the behavioral diff is proven.

Every installed-wheel scenario in this phase launches the wheel's actual `travis234`
console entry in a real attached PTY. `python -m travis.cli`, fake terminals,
`evals.tui_driver`, and scripted prompt runners remain useful for unit diagnostics but
do not count as the scenario acceptance result.

---

## Task 2.1: Add composition containers and explicit ports

**Files:**

- Create: `travis/coding_agent/session_ports.py`
- Create: `travis/coding_agent/session_state.py`
- Create: `travis/coding_agent/session_controllers.py`
- Create: `travis/tui/interactive_state.py`
- Create: `travis/tui/interactive_services.py`
- Create: `travis/tui/interactive_controllers.py`
- Create: `tests/coding_agent/test_session_controller_composition.py`
- Create: `tests/tui/test_interactive_controller_composition.py`
- Modify: `tests/architecture/test_refactor_contracts.py`
- Modify: `tests/architecture/test_facade_boundaries.py`

- [ ] **Step 1: Write failing composition-contract tests**

Require frozen/slotted controller containers:

```python
@dataclass(frozen=True, slots=True)
class SessionControllers:
    events: SessionEventController
    models: SessionModelController
    generation: SessionGenerationParams
    persistence: SessionPersistence
    bash: SessionBashController
    policy: SessionPolicyController
    operations: SessionOperationController
    tools: SessionToolController
    extensions: SessionExtensionController
    subagents: SessionSubagentController
    subagent_trace: SessionSubagentTraceController
    turns: SessionTurnController


@dataclass(frozen=True, slots=True)
class InteractiveControllers:
    command_dispatch: InteractiveCommandDispatcher
    view: InteractiveView
    model_auth: InteractiveModelAuth
    params: InteractiveParams
    processes: InteractiveProcessCommands
    lsp: InteractiveLsp
    memory: InteractiveMemory
    operations: InteractiveOperations
    subagents: InteractiveSubagents
    sessions: InteractiveSessionCommands
    extensions: InteractiveExtensions
    turns: InteractiveTurnController
    shutdown: InteractiveShutdown
    motion: InteractiveMotion
```

The initial containers may hold adapter objects around the old runtime, but tests reject
`AgentSession`, `InteractiveMode`, `CodingApp`, or the complete opposite runtime as a
constructor dependency of a leaf controller.

- [ ] **Step 2: Define narrow ports by responsibility**

Create protocols only for concrete cross-owner needs:

- session event emission/subscription;
- model/settings access;
- persistence append/read/branch operations;
- tool registry and policy evaluation;
- extension calls;
- process context;
- subagent supervision;
- turn state/cancellation mailbox;
- TUI render/status/history;
- TUI app/session rebinding;
- owner-thread dispatch and terminal input.

No protocol may expose `__getattr__`, a generic `state: object`, or the entire runtime.
No protocol is runtime-checkable.

- [ ] **Step 3: Add explicit mutable state records**

Introduce small mutable records only for cohesive state that is already mutated together:

- `SessionTurnState` for active prompt/cancel/retry/mailbox flags;
- `SessionPresentationState` for display-facing session status;
- `InteractiveState` for prompt/history/status/selection/generation parameters;
- `InteractiveLifecycleState` for shutdown and active-worker tracking.

Do not move `Agent.state`, session JSONL data, process service state, operation state, or
provider state into these records.

- [ ] **Step 4: Run contracts and strict typing**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/coding_agent/test_session_controller_composition.py \
  tests/tui/test_interactive_controller_composition.py \
  tests/architecture/test_refactor_contracts.py \
  tests/architecture/test_facade_boundaries.py
uv run --locked --all-extras --dev pyright \
  travis/coding_agent/session_ports.py \
  travis/coding_agent/session_state.py \
  travis/coding_agent/session_controllers.py \
  travis/tui/interactive_state.py \
  travis/tui/interactive_services.py \
  travis/tui/interactive_controllers.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/session_ports.py \
  travis/coding_agent/session_state.py \
  travis/coding_agent/session_controllers.py \
  travis/tui/interactive_state.py \
  travis/tui/interactive_services.py \
  travis/tui/interactive_controllers.py \
  tests/coding_agent/test_session_controller_composition.py \
  tests/tui/test_interactive_controller_composition.py \
  tests/architecture/test_refactor_contracts.py \
  tests/architecture/test_facade_boundaries.py
git commit -m "refactor(runtime): add explicit collaborator composition"
```

---

## Task 2.2: Extract TUI view, motion, and static command classification

**Files:**

- Modify: `travis/tui/interactive_view.py`
- Modify: `travis/tui/interactive_motion.py`
- Modify: `travis/tui/interactive_command_dispatcher.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_controllers.py`
- Modify: `travis/tui/interactive_state.py`
- Modify: `tests/tui/test_interactive_dispatch_characterization.py`
- Modify: `tests/test_tui_motion.py`
- Modify: `tests/test_tui_rendering_and_components.py`
- Modify: `tests/tui/test_interactive_controller_composition.py`

- [ ] **Step 1: Complete command and render characterization before the move**

Add table-driven expectations for every built-in parser/classifier and verify exact
precedence when a prompt resembles multiple commands. Capture status/history render
updates, motion enabled/disabled behavior, theme context, and prompt label behavior.

- [ ] **Step 2: Convert view and motion classes to owned collaborators**

Construct them with `InteractiveState` plus narrow render/theme ports. Replace implicit
`self.tui`, `self.history`, `self.status`, and shutdown reads with explicit fields. Keep
the current method names on the collaborators.

- [ ] **Step 3: Make command classification a pure ordered registry**

Define an immutable sequence of command bindings containing name, classifier/parser, and
handler key. Preserve current order exactly. Extension command and prompt-level skill
handling remain at their current precedence boundaries; do not route them through a
generic plugin system.

`_InteractiveRuntime` explicitly delegates supported view/motion/dispatch methods to
`self.controllers`. It still inherits the remaining, not-yet-extracted mixins.

- [ ] **Step 4: Focused verification**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/tui/test_interactive_dispatch_characterization.py \
  tests/test_tui_dispatcher.py \
  tests/test_tui_motion.py \
  tests/test_tui_rendering_and_components.py \
  tests/tui/test_interactive_controller_composition.py
```

- [ ] **Step 5: Installed-wheel scenario**

Run help, an ordinary faux-model prompt, `/motion status` or the current motion command,
theme/status rendering, and an unknown slash command. Record exact PASS/FAIL.

- [ ] **Step 6: Commit**

```bash
git add travis/tui/interactive_view.py travis/tui/interactive_motion.py \
  travis/tui/interactive_command_dispatcher.py travis/tui/interactive_mode.py \
  travis/tui/interactive_controllers.py travis/tui/interactive_state.py \
  tests/tui/test_interactive_dispatch_characterization.py \
  tests/test_tui_motion.py tests/test_tui_rendering_and_components.py \
  tests/tui/test_interactive_controller_composition.py
git commit -m "refactor(tui): compose view motion and command routing"
```

---

## Task 2.3: Extract TUI model, process, inspection, and session controllers

**Files:**

- Modify: `travis/tui/interactive_model_auth.py`
- Modify: `travis/tui/interactive_params.py`
- Modify: `travis/tui/interactive_process_commands.py`
- Modify: `travis/tui/interactive_lsp.py`
- Modify: `travis/tui/interactive_memory.py`
- Modify: `travis/tui/interactive_operations.py`
- Modify: `travis/tui/interactive_subagents.py`
- Modify: `travis/tui/interactive_session_commands.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_services.py`
- Modify: `travis/tui/interactive_controllers.py`
- Modify: `tests/tui/test_interactive_lsp.py`
- Modify: `tests/tui/test_interactive_memory.py`
- Modify: `tests/tui/test_interactive_operations.py`
- Modify: `tests/tui/test_interactive_subagents.py`
- Modify: `tests/test_tui_runtime_compaction_and_models.py`
- Modify: `tests/test_process_tools.py`
- Modify: `tests/test_session_commands.py`
- Modify: `tests/test_session_parity.py`

- [ ] **Step 1: Characterize each command family**

Before moving code, cover:

- `/login`, `/logout`, provider picker, model picker, failed authentication, and model
  switch;
- generation parameter display/set/reset and warning text;
- process list/output/write/cancel/clear with bounded output;
- `/lsp status`, `/memory status`, and `/operations [id]` read-only inspection;
- `/agents status|inspect|steer|cancel` and bounded result display;
- session new/resume/fork/clone/name/tree/export/import and busy-session rejection.

Use fake terminals/providers/process transports and isolated state. Never load `.env`.

- [ ] **Step 2: Convert one controller at a time**

For each module:

1. replace mixin-style zero-argument construction with explicit state/service ports;
2. replace cross-controller calls with an injected callable or named port;
3. register its command handler in the ordered dispatcher;
4. add explicit runtime delegates for supported members;
5. remove that base class from `_InteractiveRuntime`;
6. run that module's focused tests before proceeding.

Do not share a generic dictionary of controllers or services.

- [ ] **Step 3: Preserve session rebind behavior**

All controllers that cache a session-facing service implement a typed
`rebind_session(port)` method. Session replacement builds the new bindings before the
active reference swaps. A failed rebind retains all old bindings. Add a test that injects
failure in the third controller and proves earlier controllers roll back.

- [ ] **Step 4: Focused verification**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/tui/test_interactive_lsp.py \
  tests/tui/test_interactive_memory.py \
  tests/tui/test_interactive_operations.py \
  tests/tui/test_interactive_subagents.py \
  tests/test_tui_runtime_compaction_and_models.py \
  tests/test_tui_commands_and_extensions.py \
  tests/test_process_tools.py \
  tests/test_session_commands.py \
  tests/test_session_parity.py
```

- [ ] **Step 5: Installed-wheel scenario**

Run offline/fake normal-user flows for `/model`, `/params`, `/processes`, `/lsp status`,
`/memory status`, `/operations`, `/agents status`, new/resume/fork, and shutdown. Report
each command separately.

- [ ] **Step 6: Commit one domain group at a time**

Use at least these commits:

```text
refactor(tui): compose model and parameter controllers
refactor(tui): compose process and inspection controllers
refactor(tui): compose subagent and session controllers
```

---

## Task 2.4: Extract TUI extensions, turns, and shutdown; remove TUI mixins

**Files:**

- Modify: `travis/tui/interactive_extensions.py`
- Modify: `travis/tui/interactive_turn_controller.py`
- Modify: `travis/tui/interactive_shutdown.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_services.py`
- Modify: `travis/tui/interactive_controllers.py`
- Modify: `tests/test_tui_commands_and_extensions.py`
- Modify: `tests/test_extension_host_runtime.py`
- Modify: `tests/tui/test_interactive_shutdown_characterization.py`
- Modify: `tests/tui/test_interactive_owner_boundaries.py`

- [ ] **Step 1: Characterize extension and turn ordering**

Capture pre/post input, command override, before-agent-start, message streaming, status,
steering/follow-up display, tool approval, cancellation, session-settled, and shutdown
event order. These tests must pass before the move.

- [ ] **Step 2: Extract extensions and turn handling**

Inject extension host, event/render ports, active session port, approval broker, and
lifecycle state. The turn controller calls the existing `AgentSession` public contract;
it never imports or reaches `Agent`/`agent_loop` directly.

- [ ] **Step 3: Extract shutdown last**

Preserve deadlines, repeated Ctrl-C escalation, owner-thread dispatch, worker joins,
session shutdown, process cleanup, and terminal restoration. Make shutdown idempotence
an explicit test.

- [ ] **Step 4: Remove all `_InteractiveRuntime` behavior bases**

The class declaration becomes:

```python
class _InteractiveRuntime:
    """Explicitly composed TUI runtime."""
```

`InteractiveMode(RuntimeFacade)` remains unchanged as the public compatibility façade.
Update owner tests to assert controller instances under `runtime.controllers` rather
than `isinstance(runtime, Mixin)`.

- [ ] **Step 5: Verify full TUI behavior**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/tui tests/test_tui_*.py \
  tests/test_extension_host_runtime.py \
  tests/test_extension_event_parity.py \
  tests/test_session_commands.py
```

- [ ] **Step 6: Installed-wheel scenario and commit**

Run one full offline TUI session covering prompt streaming, extension command, steering,
follow-up, cancellation, repeated interrupt, and shutdown. Then commit:

Stage only `interactive_extensions.py`, `interactive_turn_controller.py`,
`interactive_shutdown.py`, `interactive_mode.py`, their composition modules, and the
four focused tests named in this task. Inspect `git diff --cached --name-only`, then
commit `refactor(tui): replace mixin runtime with collaborators`.

---

## Task 2.5: Extract low-coupling session controllers

**Files:**

- Modify: `travis/coding_agent/session_events.py`
- Modify: `travis/coding_agent/session_models.py`
- Modify: `travis/coding_agent/session_generation_params.py`
- Modify: `travis/coding_agent/session_bash.py`
- Modify: `travis/coding_agent/session_policy_controller.py`
- Modify: `travis/coding_agent/session_operations.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/session_controllers.py`
- Modify: `travis/coding_agent/session_ports.py`
- Modify: focused tests for each owner

- [ ] **Step 1: Characterize owner behavior**

Add or confirm tests for event subscription/unsubscription/order, model switching and
role routing, thinking/generation parameters, bash shell context, tool policy audit and
enforcement hooks, operation observe-only state, and idempotent owner shutdown.

- [ ] **Step 2: Convert owners one at a time**

Construct each with only its domain state and named ports. Add explicit delegates on
`_SessionRuntime`. Remove its corresponding base after focused tests pass. Keep event
emission and reducer order byte-for-byte equivalent at the characterization boundary.

- [ ] **Step 3: Verify the group**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/coding_agent/test_agent_session_characterization.py \
  tests/test_model_role_session_integration.py \
  tests/test_ai_generation_params.py \
  tests/test_coding_policy_and_extensions.py \
  tests/test_tool_policy_integration.py \
  tests/test_operation_coordinator.py \
  tests/test_coding_exports_and_boundaries.py
```

- [ ] **Step 4: Installed-wheel scenario**

Run a faux prompt, model/parameter change, one read-only bash command, one approval
decision, operation inspection, and clean shutdown.

- [ ] **Step 5: Commit each owner or tightly coupled pair**

Use narrow messages such as `refactor(session): extract model and policy collaborators`
and `refactor(session): extract operation coordination collaborator`.

---

## Task 2.6: Extract persistence, tools, extensions, and subagents

**Files:**

- Modify: `travis/coding_agent/session_persistence.py`
- Modify: `travis/coding_agent/session_tooling.py`
- Modify: `travis/coding_agent/session_extensions.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `travis/coding_agent/subagent_trace.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: composition/port modules and focused tests

- [ ] **Step 1: Characterize persistence and extension boundaries**

Prove JSONL entries, resume/fork/clone, compaction transactions, tool registration and
ordering, extension argument mutation/event order, subagent limits/results/expansion,
artifact references, cancellation, and structured trace output.

- [ ] **Step 2: Extract persistence first**

Inject `SessionStore`, compaction adapter/coordinator, artifact registry, event port, and
session metadata state. The collaborator never owns `Agent.state`; it accesses it through
a narrow message-state port. Preserve JSONL bytes for pinned fixtures.

- [ ] **Step 3: Extract tooling and policy-facing hooks**

Tooling receives registries, extension/policy hooks, process context, and event ports.
It does not schedule tools; scheduling stays in the protected agent loop.

- [ ] **Step 4: Extract extensions and subagents**

Keep extension runner and supervisor as existing owners. Inject them and their bounded
configuration. Preserve subagent results and explicit expansion; do not remove or merge
existing subagent output capabilities.

- [ ] **Step 5: Focused verification**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/test_coding_persistence_and_compaction.py \
  tests/test_compaction_integration.py \
  tests/test_durable_artifact_session_lifecycle.py \
  tests/test_coding_policy_and_extensions.py \
  tests/test_extension_event_parity.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_subagent_controls.py \
  tests/test_subagent_artifact_results.py \
  tests/test_subagent_structured_results.py
```

- [ ] **Step 6: Installed-wheel scenario**

Run prompt/resume/fork, extension reload, subagent spawn/wait/result/expand, and artifact
read. Confirm existing expanded subagent results remain available and conflict-free.

- [ ] **Step 7: Commit by owner group**

Use separate persistence, tools/extensions, and subagent commits.

---

## Task 2.7: Extract turn orchestration last and remove session mixins

**Files:**

- Modify: `travis/coding_agent/session_turns.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/session_controllers.py`
- Modify: `travis/coding_agent/session_ports.py`
- Modify: `travis/coding_agent/session_state.py`
- Modify: `tests/coding_agent/test_agent_session_characterization.py`
- Modify: `tests/test_agent_loop.py` only to run existing assertions, not to change
  expected behavior
- Modify: `tests/test_coding_mailbox.py`
- Modify: `tests/test_abort_context.py`
- Modify: `tests/coding_agent/test_session_owner_boundaries.py`

- [ ] **Step 1: Expand turn characterization before moving code**

Pin user/assistant/tool message order, mailbox steering/follow-up selection, partial
stream recovery, retries, cancellation, agent-settled, compaction trigger boundaries,
and source-ordered parallel results. Run the tests against the remaining mixin runtime.

- [ ] **Step 2: Convert `SessionTurnController` to an owned collaborator**

Inject Agent public API, turn state, mailbox, persistence, event, tools, model role, and
compaction ports. Do not import or call `agent_loop` directly. Preserve callback order;
do not create a new queue, thread, or async bridge.

- [ ] **Step 3: Remove all `_SessionRuntime` behavior bases**

The class declaration becomes:

```python
class _SessionRuntime:
    """Explicitly composed coding-session runtime."""
```

Update owner tests to inspect `runtime.controllers`. Keep `AgentSession(RuntimeFacade)`
and its explicit shutdown/dispose methods.

- [ ] **Step 4: Run protected behavioral suites**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/coding_agent/test_agent_session_characterization.py \
  tests/coding_agent/test_session_owner_boundaries.py \
  tests/test_agent_loop.py \
  tests/test_agent_runtime_hardening.py \
  tests/test_abort_context.py \
  tests/test_coding_mailbox.py \
  tests/test_coding_persistence_and_compaction.py \
  tests/test_session_parity.py
shasum -a 256 travis/agent/agent_loop.py
```

Expected protected hash remains exactly the master-plan value.

- [ ] **Step 5: Installed-wheel scenario and commit**

Exercise prompt, tool continuation, steering, follow-up, cancellation, compaction, and
resume in one isolated faux-provider TUI session. Commit:

```bash
git add travis/coding_agent/agent_session.py \
  travis/coding_agent/session_turns.py \
  travis/coding_agent/session_controllers.py \
  travis/coding_agent/session_ports.py \
  travis/coding_agent/session_state.py \
  tests/coding_agent/test_agent_session_characterization.py \
  tests/coding_agent/test_session_owner_boundaries.py \
  tests/test_coding_mailbox.py tests/test_abort_context.py
git commit -m "refactor(session): replace mixin runtime with collaborators"
```

---

## Task 2.8: Remove star imports and clean only migrated owners

**Files:**

- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/component.py`
- Modify: migrated session/TUI owner modules
- Modify: `travis/tui/components/__init__.py`
- Modify: `tests/architecture/test_refactor_contracts.py`
- Modify: `tests/architecture/test_repository_hygiene.py`
- Modify: `ruff.toml`, `pyrightconfig.json`

- [ ] **Step 1: Add a failing no-star-import architecture test**

Reject `ImportFrom.names` containing `*` in production modules. Allow no production
exceptions. Add import-compatibility tests for every previously supported re-export.

- [ ] **Step 2: Replace star imports with explicit imports and `__all__`**

Do not rename symbols. Remove imports proven unused after extraction, one module at a
time, running its focused suite after each change.

- [ ] **Step 3: Expand static-analysis scope**

Add every migrated session/TUI module to Pyright and full Ruff selection. Fix findings
without blanket ignores or `Any` escape hatches.

- [ ] **Step 4: Verify**

```bash
uv run --locked --all-extras --dev ruff check travis/coding_agent/session_*.py \
  travis/coding_agent/agent_session.py travis/tui
uv run --locked --all-extras --dev pyright
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_refactor_contracts.py \
  tests/architecture/test_repository_hygiene.py \
  tests/coding_agent tests/tui
```

- [ ] **Step 5: Commit**

Stage the three façade/import modules, only the migrated owner modules whose imports
changed, the two architecture tests, and `ruff.toml`/`pyrightconfig.json`. Inspect
`git diff --cached --name-only`, then commit
`refactor(imports): make session and tui ownership explicit`.

---

## Task 2.9: Phase 2 qualification

- [ ] Run the master phase checkpoint and complete root coverage gate.
- [ ] Run the complete root suite, adapter suite, npm launcher, and package builds.
- [ ] Install the exact wheel and run a 21-scenario offline normal-user TUI matrix focused
  on all refactored session/TUI capabilities. Report PASS/FAIL immediately after each
  prompt in the retained verification record.
- [ ] Confirm `_SessionRuntime.__bases__ == (object,)` and
  `_InteractiveRuntime.__bases__ == (object,)`.
- [ ] Confirm no star imports remain in production code.
- [ ] Confirm expanded subagent results, coordination, and orchestration remain additive
  and unchanged.
- [ ] Record the protected hash and empty protected-file diff.
- [ ] Commit evidence as `docs: record phase 2 refactor qualification`.
- [ ] Report and stop for review before Phase 3.
