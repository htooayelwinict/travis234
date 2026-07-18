# Production-Safe Extension Runtime Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make existing Travis234 Python extensions execute with production-safe Pi-equivalent host semantics across every supported mode and session replacement without changing agent, session, context, compaction, provider, or bounded-execution behavior.

**Architecture:** Add one narrow `ExtensionHostAdapter` that binds the active session to its real mode and synchronously rebinds replacements. Reuse existing extension contexts and tool wrappers, add thin source-scoped compatibility proxies, and correct only verified event/callback semantics.

**Tech Stack:** Python 3.13, pytest, asyncio, threaded TUI command executor, JSON Lines automation/RPC, uv, npm launcher, Docker release image.

## Global Constraints

- The repository root is the only active application tree.
- Preserve unrelated `appv231/` working-tree content.
- Product and CLI remain `Travis234` and `travis234`; Python imports remain `travis`.
- State remains under `~/.travis234`; no alternate paths or migration aliases.
- Preserve agent-loop ordering, iteration budgeting, source-ordered tool-result persistence, and bounded parallel execution.
- Preserve session JSONL, session branching, context construction, compaction, and provider request behavior.
- OpenRouter and subscription-provider model parameters remain unchanged.
- Add and run a failing regression before every production change.
- Do not weaken an expectation to make a regression pass.
- Do not add JavaScript/TypeScript execution, native provider objects, dynamic provider refresh, per-extension containers, advanced editor APIs, or RPC interactive UI.
- Do not perform Git operations until all automated and live verification gates pass. Even after passing, do not push or release without explicit user direction.
- `appv231/` is outside implementation and verification scope.

---

## File map

### New focused owner

- `travis/coding_agent/extension_host.py`: safe no-op UI, mode bindings, synchronous rebound binding, adapter disposal, and shared sync/async callback settlement.

### Existing owners to modify

- `travis/coding_agent/extensions.py`: explicit UI availability, source-scoped proxy, source-aware handler records, dynamic tool refresh, event semantics, async failure observation.
- `travis/coding_agent/resource_loader.py`: pass a source-scoped API proxy to each factory without changing discovery or trust.
- `travis/coding_agent/session_extensions.py`: canonical compatibility command context, exactly-once handler invocation, image-preserving extension user messages.
- `travis/coding_agent/session_tooling.py`: use the extension tool wrapper in the live registry.
- `travis/coding_agent/session_turns.py`: optional input source and extension-message template suppression only.
- `travis/coding_agent/session_models.py`: source-aware model selection and canonical thinking event aliases.
- `travis/coding_agent/automation.py`: scoped print/JSON host binding and stderr diagnostics.
- `travis/coding_agent/rpc.py`: scoped RPC host binding and stdout-safe diagnostics.
- `travis/tui/interactive_mode.py`: own the TUI host adapter and serialized extension-command lane.
- `travis/tui/interactive_view.py`: start TUI host binding only after terminal UI startup.
- `travis/tui/interactive_extensions.py`: all-command host dispatch, raw shortcut context, source-aware errors.
- `travis/tui/components/multiline_editor.py`: optional raw-input extension-shortcut callback.
- `travis/resources/docs/extensions.md`: exact supported runtime behavior and intentional divergences.
- `scripts/parity_contracts.py`: distinguish declaration evidence from behavioral evidence.

### Test owners

- Create `tests/test_extension_host_runtime.py`: adapter modes, replacement ordering, lifecycle exactly-once, no-op UI, and context-envelope neutrality.
- Modify `tests/test_extension_event_parity.py`: canonical payloads, input continuation, chained system prompt, and behavioral manifest evidence.
- Modify `tests/test_extension_loading_and_reload.py`: source proxy, stale API, source attribution, and pre-bind action failure.
- Modify `tests/test_coding_tools_and_subagents.py`: live extension wrapper and dynamic active-tool propagation.
- Modify `tests/test_tui_commands_and_extensions.py`: immediate async commands, exactly-once failure, raw shortcuts, and protected conflicts.
- Modify `tests/test_automation_modes.py`: print/JSON mode binding and stdout framing.
- Modify `tests/test_rpc_mode.py`: RPC mode binding, replacement binding, and stdout framing.

## Task 1: Add host adapter contract and no-op UI

**Files:**
- Create: `travis/coding_agent/extension_host.py`
- Create: `tests/test_extension_host_runtime.py`
- Modify: `travis/coding_agent/extensions.py`

**Interfaces:**
- Produces: `NoOpExtensionUI`, `ExtensionHostAdapter`, and `settle_extension_result(value)`.
- `ExtensionHostAdapter(app, mode, bindings_factory, on_rebound=None)` exposes `start()` and `dispose()`.
- `bindings_factory(session)` returns existing `AgentSession.bind_extensions()` binding keys.

- [ ] **Step 1: Write failing no-op UI and explicit availability tests**

```python
def test_noop_extension_ui_is_safe_but_not_available(tmp_path):
    runner = ExtensionRunner(cwd=str(tmp_path))
    ui = NoOpExtensionUI()
    runner.set_ui_context(ui, "json", has_ui=False)

    ctx = runner.create_context()
    assert ctx.ui is ui
    assert ctx.mode == "json"
    assert ctx.has_ui is False
    assert ui.select("title", ["one"]) is None
    assert ui.confirm("title", "message") is False
    assert ui.set_theme("night")["success"] is False
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest -q tests/test_extension_host_runtime.py::test_noop_extension_ui_is_safe_but_not_available
```

Expected: import/signature failure because the new host surface does not exist.

- [ ] **Step 3: Implement the minimal no-op UI and explicit `has_ui` state**

```python
class NoOpExtensionUI:
    def select(self, _title, _options, _dialog_options=None): return None
    def confirm(self, _title, _message, _options=None): return False
    def input(self, _title, _placeholder=None, _options=None): return None
    def notify(self, _message, _kind=None): return None
    def on_terminal_input(self, _handler): return lambda: None
    def set_theme(self, _theme): return {"success": False, "error": "UI not available"}


def set_ui_context(self, ui_context=None, mode="print", *, has_ui=None):
    self._ui_context = ui_context
    self._mode = mode
    self._has_ui = bool(ui_context is not None) if has_ui is None else bool(has_ui)
```

Implement the remaining currently documented no-op methods with neutral return values. Support Pi camelCase spellings through explicit aliases or a bounded alias map.

- [ ] **Step 4: Run the focused test and existing context tests**

```bash
uv run pytest -q \
  tests/test_extension_host_runtime.py::test_noop_extension_ui_is_safe_but_not_available \
  tests/test_coding_resources_and_services.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Write failing adapter initial/rebound lifecycle tests**

```python
def test_host_adapter_binds_initial_and_replacement_before_deferred_start(tmp_path):
    app, replacements, seen = create_extension_host_app(tmp_path)
    adapter = ExtensionHostAdapter(
        app,
        mode="tui",
        bindings_factory=lambda _session: {
            "uiContext": object(),
            "hasUI": True,
            "onError": lambda error: seen.append(("error", error)),
        },
    )
    adapter.start()
    app.new_session()

    assert seen == [
        ("session_start", "tui", True, "startup"),
        ("session_start", "tui", True, "new"),
    ]
    assert replacements[-1].deferred_start_emissions == 1
```

- [ ] **Step 6: Run the lifecycle test and verify RED**

```bash
uv run pytest -q tests/test_extension_host_runtime.py::test_host_adapter_binds_initial_and_replacement_before_deferred_start
```

Expected: failure because `ExtensionHostAdapter` has no implementation.

- [ ] **Step 7: Implement the minimal adapter**

```python
class ExtensionHostAdapter:
    def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self._app.subscribe_session_rebound(self._handle_rebound)
        self.bind(self._app.session)

    def bind(self, session) -> None:
        bindings = dict(self._bindings_factory(session) or {})
        bindings["mode"] = self._mode
        session.bind_extensions(bindings)

    def _handle_rebound(self, session) -> None:
        self.bind(session)
        if self._on_rebound is not None:
            self._on_rebound(session)
```

- [ ] **Step 8: Verify lifecycle tests and inspect the diff**

```bash
uv run pytest -q tests/test_extension_host_runtime.py
```

Expected: all host adapter tests pass; no files outside the declared extension-host scope changed.

## Task 2: Bind every runtime mode without protocol pollution

**Files:**
- Modify: `travis/coding_agent/automation.py`
- Modify: `travis/coding_agent/rpc.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_view.py`
- Modify: `travis/tui/interactive_process_commands.py`
- Test: `tests/test_extension_host_runtime.py`
- Test: `tests/test_automation_modes.py`
- Test: `tests/test_rpc_mode.py`
- Test: `tests/test_tui_runtime_compaction_and_models.py`

**Interfaces:**
- Consumes: `ExtensionHostAdapter`, `NoOpExtensionUI`.
- Produces: one adapter lifetime per mode and synchronous replacement binding.

- [ ] **Step 1: Write failing print/JSON/RPC mode tests**

```python
@pytest.mark.parametrize((mode, expected), [("print", "print"), ("json", "json")])
def test_automation_binds_real_extension_mode(mode, expected, app, output):
    seen = record_session_start_context(app)
    run_mode(mode, app, "hello", output)
    assert seen == [(expected, False)]


def test_rpc_binds_extension_mode_without_plaintext_stdout(app):
    seen = record_session_start_context(app)
    output = io.StringIO()
    RpcServer(app, io.StringIO('{"id":1,"method":"get_state"}\n'), output).run()
    assert seen == [("rpc", False)]
    for line in output.getvalue().splitlines():
        assert isinstance(json.loads(line), dict)
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q \
  tests/test_automation_modes.py -k extension_mode \
  tests/test_rpc_mode.py -k extension_mode
```

Expected: missing startup event or default `print` mode in JSON/RPC.

- [ ] **Step 3: Add a shared binding factory for non-interactive modes**

The binding factory must target the current session for `waitForIdle`, `navigateTree`, and `reload`, while replacement actions use `app.session_runtime`:

```python
def noninteractive_extension_bindings(app, session, *, error_handler):
    return {
        "uiContext": NoOpExtensionUI(),
        "hasUI": False,
        "onError": error_handler,
        "commandContextActions": {
            "waitForIdle": session.agent.wait_for_idle,
            "newSession": app.session_runtime.new_session,
            "fork": app.session_runtime.fork,
            "navigateTree": session.navigate_tree,
            "switchSession": app.session_runtime.switch_session,
            "reload": session.reload,
        },
    }
```

- [ ] **Step 4: Bind scoped adapters in print, JSON, and RPC**

Start before the first prompt/frame and dispose in `finally`. Send diagnostics to stderr or an injected diagnostic stream; never to JSON/RPC stdout.

- [ ] **Step 5: Run automation and RPC suites**

```bash
uv run pytest -q tests/test_automation_modes.py tests/test_rpc_mode.py
```

Expected: all selected tests pass and machine output remains JSON-decodable.

- [ ] **Step 6: Write failing TUI replacement-order test**

```python
def test_tui_replacement_is_bound_before_session_start(mode):
    seen = record_replacement_start(mode.app)
    mode.app.new_session()
    assert seen == [("new", "tui", True)]
```

- [ ] **Step 7: Run and verify RED**

```bash
uv run pytest -q tests/test_tui_runtime_compaction_and_models.py -k replacement_is_bound
```

Expected: replacement startup observes default print/no-UI state.

- [ ] **Step 8: Replace the redraw-only rebound subscription with adapter ownership**

Start the adapter after `tui.start()`. Its synchronous rebound handler binds first and then posts `_rebind_session_ui`. Remove the duplicate standalone application rebound subscription.

- [ ] **Step 9: Run focused TUI lifecycle tests**

```bash
uv run pytest -q \
  tests/test_tui_runtime_compaction_and_models.py \
  tests/test_extension_loading_and_reload.py
```

Expected: all selected tests pass with exactly-once startup/reload behavior.

## Task 3: Activate the canonical extension tool wrapper

**Files:**
- Modify: `travis/coding_agent/session_tooling.py`
- Modify: `travis/coding_agent/extensions.py`
- Test: `tests/test_coding_tools_and_subagents.py`
- Test: `tests/test_extension_loading_and_reload.py`

**Interfaces:**
- Consumes: existing `wrap_registered_tool(registered_tool, runner)`.
- Produces: full extension context at tool execution and automatic post-bind registry refresh.

- [ ] **Step 1: Write failing live registry context test**

```python
def test_live_extension_tool_receives_extension_context(session):
    seen = []
    session.extension_runner.register_tool(tool_definition(lambda _id, _args, ctx: seen.append(ctx)))
    session.refresh_tools(include_all_extension_tools=True)
    execute_tool(session, "probe")
    assert seen[0].mode == "tui"
    assert seen[0].model_registry is session.model_registry
    assert hasattr(seen[0], "get_context_usage")
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/test_coding_tools_and_subagents.py -k live_extension_tool_receives_extension_context
```

Expected: basic `ToolContext` lacks extension runtime properties.

- [ ] **Step 3: Use the extension wrapper only for registered extension tools**

```python
tool_by_name[registered.definition.name] = wrap_registered_tool(
    registered,
    self._extension_runner,
)
```

Leave `_base_tool_by_name` untouched.

- [ ] **Step 4: Run the live registry and existing wrapper tests**

```bash
uv run pytest -q tests/test_coding_tools_and_subagents.py -k 'registered_tool or extension_tool'
```

Expected: selected tests pass.

- [ ] **Step 5: Write failing dynamic refresh test**

```python
def test_post_bind_register_and_unregister_refresh_live_tools(session):
    session.extension_runner.register_tool(probe_definition())
    assert "probe" in session.get_all_tool_names()
    session.extension_runner.unregister_tool("probe")
    assert "probe" not in session.get_all_tool_names()
```

- [ ] **Step 6: Run and verify RED**

```bash
uv run pytest -q tests/test_extension_loading_and_reload.py -k post_bind_register
```

Expected: live registry remains stale.

- [ ] **Step 7: Request refresh after register/unregister**

Call the already bound `_refresh_tools()` after mutation. Initial factory loading remains safe because that action is a no-op until `bind_core()`.

- [ ] **Step 8: Run extension tool suites**

```bash
uv run pytest -q \
  tests/test_coding_tools_and_subagents.py \
  tests/test_extension_loading_and_reload.py
```

Expected: all selected tests pass; built-in tool context tests remain unchanged.

## Task 4: Execute extension commands exactly once outside model turns

**Files:**
- Modify: `travis/coding_agent/session_extensions.py`
- Modify: `travis/tui/interactive_extensions.py`
- Modify: `travis/tui/interactive_mode.py`
- Test: `tests/test_tui_commands_and_extensions.py`
- Test: `tests/test_extension_host_runtime.py`

**Interfaces:**
- Consumes: `settle_extension_result()` and canonical runner command context.
- Produces: `ExtensionCommandContextProxy` and a serialized host command lane.

- [ ] **Step 1: Write failing async/exactly-once command tests**

```python
def test_async_extension_command_is_awaited_once(session):
    calls = []
    async def handler(args, ctx):
        await asyncio.sleep(0)
        calls.append((args, ctx.mode))
    session.extension_runner.register_command("probe", {"handler": handler})
    session.execute_extension_command("/probe value")
    assert calls == [("value", "tui")]


def test_handler_typeerror_does_not_reinvoke_legacy_signature(session):
    calls = []
    def handler(args, ctx):
        calls.append(args)
        raise TypeError("inside handler")
    session.extension_runner.register_command("probe", {"handler": handler})
    with pytest.raises(TypeError, match="inside handler"):
        session.execute_extension_command("/probe value")
    assert calls == ["value"]
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/test_tui_commands_and_extensions.py -k 'async_extension_command or typeerror_does_not_reinvoke'
```

Expected: async handler is not awaited and/or the TypeError handler runs twice.

- [ ] **Step 3: Replace exception-based arity detection with signature inspection**

Use one invocation path:

```python
result = command.handler(args, command_context) if accepts_context else command.handler(args)
return settle_extension_result(result)
```

- [ ] **Step 4: Run exactly-once tests**

```bash
uv run pytest -q tests/test_tui_commands_and_extensions.py -k 'async_extension_command or typeerror_does_not_reinvoke'
```

Expected: both pass with no unawaited-coroutine warning.

- [ ] **Step 5: Write failing active-turn host-command test**

```python
def test_registered_command_during_active_turn_never_enters_agent_queue(mode):
    calls = []
    mode.app.session.extension_runner.register_command("probe", {"handler": lambda *_: calls.append("ran")})
    mark_turn_active(mode)
    assert mode._handle_active_turn_prompt("/probe") is True
    assert calls == ["ran"]
    assert mode.app.session.pending_message_count == 0
```

- [ ] **Step 6: Run and verify RED**

```bash
uv run pytest -q tests/test_tui_commands_and_extensions.py -k active_turn_never_enters_agent_queue
```

Expected: command is rejected by steering or not executed.

- [ ] **Step 7: Dispatch every registered command through the serialized command lane**

Remove `IMMEDIATE_EXTENSION_COMMANDS` gating. Reuse the existing TUI command executor with a dedicated extension-command key/lane. Post completion/error rendering back to the TUI dispatcher. Do not call `app.run_turn()`.

- [ ] **Step 8: Add compatibility command context delegation**

The proxy must expose canonical `ui`, `mode`, `has_ui`, `model`, trust, signal, abort, shutdown, and context usage while retaining current Travis command action methods.

- [ ] **Step 9: Run command and active-turn suites**

```bash
uv run pytest -q \
  tests/test_tui_commands_and_extensions.py \
  tests/test_coding_resources_and_services.py
```

Expected: all selected tests pass without model calls for host commands.

## Task 5: Wire raw extension shortcuts safely

**Files:**
- Modify: `travis/tui/components/multiline_editor.py`
- Modify: `travis/tui/interactive_extensions.py`
- Modify: `travis/coding_agent/extensions.py`
- Test: `tests/test_tui_commands_and_extensions.py`
- Test: `tests/test_tui_terminal_and_input.py`

**Interfaces:**
- Produces: `Editor.on_extension_shortcut: Callable[[str], bool] | None`.
- Consumes: `matches_key()` and resolved built-in keybindings.

- [ ] **Step 1: Write failing raw shortcut test**

```python
def test_extension_shortcut_receives_raw_key_without_submit(mode):
    seen = []
    mode.app.session.extension_runner.register_shortcut("ctrl+w", {"handler": lambda ctx: seen.append(ctx.mode)})
    editor = mode.active_editor
    editor.handle_input("\x17")
    assert seen == ["tui"]
    assert editor.get_value() == ""
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/test_tui_commands_and_extensions.py -k raw_key_without_submit
```

Expected: raw Ctrl-W edits input or does nothing; handler is not called.

- [ ] **Step 3: Add the optional editor callback**

At the start of `Editor.handle_input()`:

```python
if self.on_extension_shortcut is not None and self.on_extension_shortcut(data):
    return
```

Initialize the attribute to `None`; do not alter single-line inputs or other components.

- [ ] **Step 4: Resolve protected conflicts before attaching callbacks**

Pass `get_keybindings().get_resolved_bindings()` to `runner.get_shortcuts()`. Skip reserved conflicts and retain diagnostics containing key and extension path.

- [ ] **Step 5: Remove prompt-text shortcut dispatch**

Do not call `_dispatch_extension_shortcut(prompt)` after prompt submission. Retain a focused raw dispatch helper used by the editor callback.

- [ ] **Step 6: Run shortcut, editor, paste, and multiline tests**

```bash
uv run pytest -q \
  tests/test_tui_commands_and_extensions.py -k shortcut \
  tests/test_tui_terminal_and_input.py \
  tests/test_tui_multiline_editor.py
```

Expected: all selected tests pass; protected editor operations remain intact.

## Task 6: Add source-scoped APIs and correct verified event semantics

**Files:**
- Modify: `travis/coding_agent/extensions.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Modify: `travis/coding_agent/session_models.py`
- Modify: `travis/coding_agent/session_turns.py`
- Modify: `travis/coding_agent/session_extensions.py`
- Modify: `travis/coding_agent/event_bus.py`
- Test: `tests/test_extension_loading_and_reload.py`
- Test: `tests/test_extension_event_parity.py`

**Interfaces:**
- Produces: thin `SourceScopedExtensionAPI` with generation/path guard.
- Extends: `prompt(..., input_source="interactive")`.
- Extends: `set_model(model, *, source="set")`.

- [ ] **Step 1: Write failing stale/source tests**

```python
def test_captured_factory_api_is_stale_after_reload(loader):
    captured = []
    loader.add_factory(lambda api: captured.append(api))
    loader.reload()
    old_api = captured[-1]
    loader.reload()
    with pytest.raises(RuntimeError, match="stale"):
        old_api.register_command("late", {"handler": lambda *_: None})


def test_handler_error_reports_registering_extension_path(loader):
    errors = load_throwing_extension(loader, "broken.py")
    assert errors[0]["extensionPath"].endswith("broken.py")
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/test_extension_loading_and_reload.py -k 'captured_factory_api or registering_extension_path'
```

Expected: raw runner remains usable or error reports `<python-extension>`.

- [ ] **Step 3: Implement the thin source-scoped proxy**

Use a bounded delegation wrapper that asserts the captured generation and executes callable delegation inside the runner's source scope. Do not introduce per-extension registries or containers.

- [ ] **Step 4: Store handler source paths and report them**

Represent internal handlers as records containing `handler` and `extension_path`, while preserving public registration signatures and unsubscribe behavior.

- [ ] **Step 5: Run reload/source tests**

```bash
uv run pytest -q tests/test_extension_loading_and_reload.py
```

Expected: selected suite passes with source-aware diagnostics.

- [ ] **Step 6: Write failing tests for the four approved event corrections**

```python
def test_thinking_event_has_pi_fields_and_legacy_aliases(session): ...
def test_model_select_reports_set_cycle_and_restore_sources(session): ...
def test_input_returns_continue_when_unchanged(runner): ...
def test_before_agent_start_context_sees_chained_system_prompt(runner): ...
```

- [ ] **Step 7: Run and verify RED**

```bash
uv run pytest -q tests/test_extension_event_parity.py -k 'pi_fields or reports_set_cycle or continue_when_unchanged or chained_system_prompt'
```

Expected: each test fails for its documented mismatch.

- [ ] **Step 8: Implement only the approved event changes**

Keep legacy thinking aliases. Add optional source parameters rather than alternate model/session paths. Use a chained context getter only inside `before_agent_start` emission.

- [ ] **Step 9: Write failing extension user-message test**

```python
def test_extension_user_message_preserves_images_and_skips_command_expansion(session, image):
    seen = record_input_events(session.extension_runner)
    session._extension_send_user_message([TextContent(text="/probe"), image])
    assert seen[0]["source"] == "extension"
    assert seen[0]["images"] == [image]
    assert extension_command_calls(session) == []
```

- [ ] **Step 10: Run and verify RED**

```bash
uv run pytest -q tests/test_extension_event_parity.py -k extension_user_message
```

Expected: image is dropped, source is interactive, or command expansion runs.

- [ ] **Step 11: Preserve blocks and pass explicit prompt metadata**

Add `input_source` to `prompt()` with default `interactive`. The extension path uses `expand_prompt_templates=False`, `input_source="extension"`, and passes images through steering/follow-up/prompt APIs.

- [ ] **Step 12: Observe async event-bus failures**

Attach a task completion callback when an event bus must schedule an awaitable on an already-running loop. Report failures without raising into unrelated emitters.

Preserve Pi's explicit exception: `tool_call` handler failures propagate to the tool runtime and block execution; do not apply general event-error isolation to that boundary.

- [ ] **Step 13: Run event and turn suites**

```bash
uv run pytest -q \
  tests/test_extension_event_parity.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_coding_turns_and_tools.py
```

Expected: all selected tests pass.

## Task 7: Make parity evidence and extension documentation truthful

**Files:**
- Modify: `scripts/parity_contracts.py`
- Modify: `travis/resources/docs/extensions.md`
- Modify: `README.md`
- Test: `tests/test_pi_behavioral_parity.py`
- Test: `tests/test_extension_event_parity.py`

**Interfaces:**
- Produces: declaration and behavioral evidence for extension events.

- [ ] **Step 1: Write failing manifest-evidence test**

```python
def test_extension_event_contracts_do_not_all_share_declaration_only_evidence():
    contracts = extension_event_contracts()
    assert any("behavior" in contract.evidence for contract in contracts)
    assert all("declares_all" not in contract.evidence for contract in contracts if contract.status == "parity")
```

- [ ] **Step 2: Run and verify RED**

```bash
uv run pytest -q tests/test_pi_behavioral_parity.py -k declaration_only
```

Expected: all 33 contracts point to the declaration test.

- [ ] **Step 3: Map behavioral evidence without overstating unsupported surfaces**

Use focused lifecycle/payload tests for events they cover. Mark known unsupported mode/UI/provider surfaces as documented divergence rather than parity.

- [ ] **Step 4: Expand the extension guide**

Document:

- all four host modes and `has_ui` behavior;
- replacement/reload lifecycle and stale APIs;
- async command and raw shortcut semantics;
- extension tool context and dynamic refresh;
- extension user-message source/template behavior;
- intentional Pi divergences and Python-only boundary.

- [ ] **Step 5: Update README extension summary and acceptance notes**

Keep the README concise and link to the extension guide. Do not claim JS/TS, native providers, RPC UI, or full event semantics without evidence.

- [ ] **Step 6: Run documentation/parity tests**

```bash
uv run pytest -q \
  tests/test_pi_behavioral_parity.py \
  tests/test_extension_event_parity.py \
  tests/test_docs_examples.py
uv run python scripts/verify_acceptance.py --parity-json
```

Expected: zero failures and a valid parity report.

## Task 8: Automated completion gates

**Files:**
- Review only: all modified files outside `appv231/`
- Build artifacts remain untracked and removable.

- [ ] **Step 1: Run the complete focused extension gate**

```bash
uv run pytest -q \
  tests/test_extension_host_runtime.py \
  tests/test_extension_event_parity.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_tui_commands_and_extensions.py \
  tests/test_automation_modes.py \
  tests/test_rpc_mode.py
```

Expected: zero failures, warnings, or unawaited coroutines.

- [ ] **Step 2: Prove the context-envelope invariant**

Run the dedicated exact-equality test with no extensions and inspect that system prompt, messages, active tools, tool schemas, provider context, and component counts are unchanged.

```bash
uv run pytest -q tests/test_extension_host_runtime.py -k context_envelope
```

Expected: exact equality assertions pass.

- [ ] **Step 3: Run the full Python suite**

```bash
uv run pytest -q
```

Expected: zero failures.

- [ ] **Step 4: Run npm launcher tests and dry-run packaging**

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: both commands exit zero.

- [ ] **Step 5: Build Python distributions**

```bash
uv run python -m build
```

Expected: wheel and source distribution build successfully.

- [ ] **Step 6: Run parity/acceptance verification**

```bash
uv run python scripts/verify_acceptance.py --parity-json
```

Expected: valid report with no invalid Pi or Hermes evidence.

- [ ] **Step 7: Run relevant container smoke checks**

Use the repository's existing container test commands and verify the release image starts as the unprivileged user, the npm launcher path is intact, and JSON/RPC output remains machine-readable.

- [ ] **Step 8: Audit scope mechanically**

Inspect the working diff and confirm no changes to agent-loop, session-store, compaction, provider, credential, or package-launcher source. Confirm `appv231/` remains untouched.

Do not commit yet; the live 21-conversation gate remains.

## Task 9: MiMo Pro 21-conversation live TUI stress gate

**Files:**
- Read: `README.md`
- Read: `evals/scenarios.json`
- Write only acceptance evidence under `/tmp/travis234-extension-acceptance/` unless an existing repository evidence format explicitly requires a tracked update.

**Interfaces:**
- Uses the real installed console entry point through `uv run travis234` in an attached PTY.
- Uses `.env` without printing credentials.
- Selects `openrouter/xiaomi/mimo-v2.5-pro` using `/model mimo`.

- [ ] **Step 1: Prepare isolated acceptance state**

```bash
rm -rf /tmp/travis234-extension-acceptance
mkdir -p /tmp/travis234-extension-acceptance/workspace
cp README.md /tmp/travis234-extension-acceptance/workspace/README.md
```

- [ ] **Step 2: Create acceptance-only extension fixtures**

Under the isolated workspace, create trusted Python extensions that exercise:

- async command execution and internal `TypeError` exactly once;
- a non-conflicting raw shortcut;
- extension tool context and dynamic tool registration;
- lifecycle logging for startup, reload, replacement, compact, and shutdown;
- extension-sent text/image handling where the scenario supports it.

Fixtures must not modify the Travis234 repository.

- [ ] **Step 3: Launch the real TUI**

```bash
TRAVIS234_CODING_AGENT_DIR=/tmp/travis234-extension-acceptance/agent \
uv run travis234 \
  --cwd /tmp/travis234-extension-acceptance/workspace \
  --dotenv .env \
  --temperature 0.2 \
  --thinking high \
  --event-trace /tmp/travis234-extension-acceptance/events.jsonl \
  --conversation-log /tmp/travis234-extension-acceptance/conversation.jsonl
```

- [ ] **Step 4: Select MiMo Pro**

Run `/model mimo` and select `openrouter/xiaomi/mimo-v2.5-pro`. Confirm the footer shows the selected route before scenarios begin.

- [ ] **Step 5: Run all 21 scenarios one at a time**

For every scenario in `evals/scenarios.json`:

1. Enter each turn in order.
2. Wait for idle after each turn.
3. Run the scenario verifier outside the TUI.
4. Record footer tokens, context percentage, and compaction count.
5. Classify weak answers as model quality and continue.
6. Stop for provider, runtime/tooling, context, environment, or protocol failures.

- [ ] **Step 6: Interleave extension-specific stress**

In the same campaign, verify:

- async extension command while idle;
- async extension command during an active provider turn;
- raw shortcut without Enter;
- extension tool invocation and dynamic tool refresh;
- `/reload` and stale captured API rejection;
- `/new`, resume, fork/clone, and replacement lifecycle exactly once;
- `/compact` plus dependent follow-up continuity;
- automatic compaction scenario continuity where naturally exercised;
- `/session` context accounting does not change merely from host binding.

- [ ] **Step 7: Exit and inspect evidence**

Use `/exit`, confirm terminal restoration and no owned process remains. Inspect lifecycle logs for duplicates, errors, and wrong modes. Inspect event/conversation logs without printing credentials.

- [ ] **Step 8: Re-run automated gates after the live campaign**

```bash
uv run pytest -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
uv run python -m build
uv run python scripts/verify_acceptance.py --parity-json
```

Expected: every command exits zero.

- [ ] **Step 9: Completion audit before any Git operation**

Check every design requirement against current files and fresh command/live evidence. Treat indirect or missing evidence as incomplete. Only after all requirements are proven may Git operations be considered, and no push, tag, publish, or release occurs without explicit user direction.

## Plan self-review

- Spec coverage: every approved lifecycle, tool, command, shortcut, source, event, message, context, documentation, packaging, container, and live-TUI requirement maps to a task.
- Placeholders: no task contains `TBD`, `TODO`, or an unspecified implementation step.
- Type consistency: the adapter, no-op UI, callback settler, source proxy, command proxy, optional input source, and model source names are consistent across tasks.
- Scope: no production task requires changes to agent loop, session persistence, compaction, providers, credentials, or package launcher.
- Git: all intermediate commit checkpoints are intentionally removed because the user prohibited Git operations before verification passes.
