# Agent Session, TUI, and Provider Facade Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace four god objects with small public facades composed from focused collaborators while preserving every characterized runtime, TUI, provider, and persistence behavior.

**Architecture:** Extract pure leaves first, then controllers with explicitly injected callables/protocols, and leave `AgentSession`/`InteractiveMode` as composition facades. Collaborators never import the facade they serve, preventing circular ownership and structural-only file splits.

**Tech Stack:** Python 3.13, dataclasses, Protocols, pytest characterization/golden tests, AST architecture gates.

## Global Constraints

- `travis/coding_agent/agent_session.py` is at most 900 physical lines and `AgentSession` defines at most 50 methods.
- `travis/tui/interactive_mode.py` is at most 500 lines and `InteractiveMode` defines at most 20 methods.
- `travis/tui/component.py` is at most 120 lines and defines no classes/functions.
- `travis/ai/providers/travis_env.py` is at most 320 lines.
- Every new owner module is at most 750 lines.
- Collaborators do not import `AgentSession`, `CodingApp`, `InteractiveMode`, or the facade module above them.
- Public behavior, error strings, event order, command precedence, provider event tuples, terminal cleanup order, JSONL entries, and tool order are preserved.
- Agent-loop and compaction algorithms remain unchanged.

---

### Task 1: Add characterization and architecture gates

**Files:**
- Create: `tests/coding_agent/test_agent_session_characterization.py`
- Create: `tests/tui/test_interactive_dispatch_characterization.py`
- Create: `tests/tui/test_interactive_shutdown_characterization.py`
- Create: `tests/ai/providers/test_provider_characterization.py`
- Create: `tests/architecture/test_facade_boundaries.py`

**Interfaces:**
- Produces normalized traces for session events/tool calls/JSONL, interactive dispatch precedence/cleanup, and provider streams/requests/errors.
- Produces hard line/method/import-direction gates enabled after each extraction.

- [ ] **Step 1: Characterize session behavior**

```python
@pytest.mark.parametrize("scenario", session_scenarios(), ids=lambda item: item.name)
def test_agent_session_trace_is_stable(scenario: SessionScenario) -> None:
    observed = run_session_scenario(scenario)
    assert observed.messages == scenario.expected_messages
    assert observed.events == scenario.expected_events
    assert observed.tool_invocations == scenario.expected_tool_invocations
    assert observed.persisted_entries == scenario.expected_persisted_entries
```

`session_scenarios()` contains text-only, sequential tools, parallel tools,
mailbox steer/follow-up, extension hooks, retry success/exhaustion,
partial/malformed stream, guardrail halt, iteration limit, abort, subagent
success/failure/cancel, bash record, model change, and branch/export cases.

- [ ] **Step 2: Characterize interactive command precedence**

```python
@pytest.mark.parametrize(
    ("prompt", "active_turn", "expected"),
    [
        ("/extension-shortcut", False, "extension-shortcut"),
        ("/exit", False, "exit"),
        ("", False, "empty"),
        ("/help", False, "help"),
        ("/resume", False, "session"),
        ("/processes", False, "processes"),
        ("!pwd", False, "bash"),
        ("/compact", False, "compact"),
        ("/login", False, "auth"),
        ("/model", False, "model"),
        ("/params", False, "params"),
        ("/allow", False, "allow"),
        ("/extension", False, "extension-command"),
        ("/unknown", False, "unknown-slash"),
        ("steer", True, "steer"),
        ("prompt", False, "agent-prompt"),
    ],
)
def test_dispatch_precedence(prompt: str, active_turn: bool, expected: str) -> None:
    assert dispatch_observation(prompt, active_turn=active_turn) == expected
```

- [ ] **Step 3: Characterize provider boundaries**

```python
@pytest.mark.parametrize("fixture_name", ["chat", "responses", "anthropic"])
def test_stream_event_tuple_parity(fixture_name: str) -> None:
    assert decode_fixture(fixture_name) == EXPECTED_EVENT_TUPLES[fixture_name]


@pytest.mark.parametrize("case", provider_request_cases(), ids=lambda item: item.name)
def test_provider_request_parity(case: ProviderRequestCase) -> None:
    assert prepare_current_request(case.input) == case.expected_request
```

Fixtures cover partial/malformed tool arguments, protocol leakage, usage,
finish reasons, idle timeout, auth/header merge, profile selection, payload hook,
HTTP body errors, and exact truncation/error text.

- [ ] **Step 4: Add initially failing architecture gates**

```python
LIMITS = {
    "travis/coding_agent/agent_session.py": (900, 50),
    "travis/tui/interactive_mode.py": (500, 20),
    "travis/tui/component.py": (120, 0),
    "travis/ai/providers/travis_env.py": (320, 12),
}


def test_facades_stay_below_size_and_method_limits() -> None:
    failures = []
    for relative, (line_limit, method_limit) in LIMITS.items():
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lines = len(path.read_text(encoding="utf-8").splitlines())
        methods = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
        if lines > line_limit or methods > method_limit:
            failures.append((relative, lines, methods))
    assert failures == []
```

- [ ] **Step 5: Run characterization green and architecture red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent/test_agent_session_characterization.py tests/tui/test_interactive_dispatch_characterization.py tests/tui/test_interactive_shutdown_characterization.py tests/ai/providers/test_provider_characterization.py -q`

Expected: PASS against the baseline.

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_facade_boundaries.py -q`

Expected: FAIL reporting the four oversized owners.

- [ ] **Step 6: Commit characterization and red gates**

```bash
git add tests/coding_agent tests/tui tests/ai/providers tests/architecture/test_facade_boundaries.py
git commit -m "test: characterize facade behavior and ownership"
```

### Task 2: Split provider request, translation, decoder, and error leaves

**Files:**
- Create: `travis/ai/providers/message_translation.py`
- Create: `travis/ai/providers/provider_errors.py`
- Create: `travis/ai/providers/streaming_json.py`
- Create: `travis/ai/providers/sse_common.py`
- Create: `travis/ai/providers/chat_stream.py`
- Create: `travis/ai/providers/responses_stream.py`
- Create: `travis/ai/providers/anthropic_stream.py`
- Create: `travis/ai/providers/provider_request.py`
- Create: `travis/ai/providers/runtime_auth.py`
- Reduce: `travis/ai/providers/travis_env.py`
- Split: `tests/test_ai_travis_env_provider.py` into `tests/ai/providers/test_*.py`.

**Interfaces:**
- `translate_messages(messages, model) -> list[dict[str, object]]`.
- `format_provider_exception(error, *, body_limit=2000) -> str`.
- `decode_chat_stream`, `decode_responses_stream`, `decode_anthropic_stream` yield normalized provider events.
- `prepare_provider_request(model, context, options, runtime) -> PreparedProviderRequest`.

- [ ] **Step 1: Create pure-module tests from characterization cases**

```python
def test_each_decoder_matches_facade_characterization() -> None:
    assert tuple(decode_chat_stream(chat_fixture())) == EXPECTED_EVENT_TUPLES["chat"]
    assert tuple(decode_responses_stream(responses_fixture())) == EXPECTED_EVENT_TUPLES["responses"]
    assert tuple(decode_anthropic_stream(anthropic_fixture())) == EXPECTED_EVENT_TUPLES["anthropic"]


def test_prepared_request_is_immutable() -> None:
    request = prepare_provider_request(model(), context(), options(), runtime())
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.url = "changed"
```

- [ ] **Step 2: Run pure-module tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/providers -q`

Expected: FAIL because owner modules/functions do not exist.

- [ ] **Step 3: Move pure implementations without semantic edits**

Move message conversion, provider error formatting/HTTP-body parsing,
partial-JSON repair, SSE framing, and each protocol decoder into its named owner.
Define:

```python
@dataclass(frozen=True)
class PreparedProviderRequest:
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, object]
    timeout_seconds: float
    decoder: Callable[[Iterable[bytes]], Iterator[ProviderEvent]]
```

`TravisProvider._run()` only prepares the immutable request, performs streaming
HTTP, selects the prepared decoder, and maps exceptions. `RuntimeAuthProvider`
owns runtime-key selection; `NullProvider` represents absent auth.

- [ ] **Step 4: Run provider characterization and split suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/ai/providers tests/test_ai_provider_catalog.py tests/test_ai_provider_capabilities.py -q`

Expected: PASS with exact event and error parity.

- [ ] **Step 5: Run provider facade size gate green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_facade_boundaries.py -k travis_env -q`

Expected: `travis_env.py` is at most 320 lines and contains at most 12 functions/methods.

- [ ] **Step 6: Commit provider decomposition**

```bash
git add travis/ai/providers tests/ai/providers tests/test_ai_travis_env_provider.py tests/architecture/test_facade_boundaries.py
git commit -m "refactor: decompose provider translation and streaming"
```

### Task 3: Split TUI component owners

**Files:**
- Create: `travis/tui/components/base.py`
- Create: `travis/tui/components/loaders.py`
- Create: `travis/tui/components/image.py`
- Create: `travis/tui/components/markdown.py`
- Create: `travis/tui/components/autocomplete.py`
- Create: `travis/tui/components/editor.py`
- Create: `travis/tui/components/pickers.py`
- Create: `travis/tui/components/footer.py`
- Create: `travis/tui/components/__init__.py`
- Reduce: `travis/tui/component.py` to imports/`__all__` only.
- Create/split: `tests/tui/components/test_*.py`.

**Interfaces:**
- Base owns `Component`, `Container`, `Text`, `TruncatedText`, `Spacer`, `Box`.
- Editor owns grapheme/edit/history/kill/undo `Input` behavior.
- Autocomplete owns completion providers/helpers; editor may import it.
- No owner imports `travis.tui.component`.

- [ ] **Step 1: Add key-stream/render golden tests**

```python
@pytest.mark.parametrize("case", editor_cases(), ids=lambda item: item.name)
def test_editor_key_stream_parity(case: EditorCase) -> None:
    editor = Input()
    for key in case.keys:
        editor.handle_input(key)
    assert (editor.get_value(), editor.cursor, editor.history_index, editor.render(80)) == case.expected


def test_picker_and_footer_render_parity() -> None:
    assert render_picker_fixture() == EXPECTED_PICKER_LINES
    assert render_footer_fixture() == EXPECTED_FOOTER_LINES
```

- [ ] **Step 2: Run component tests green before movement**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/tui/components tests/test_tui.py -k 'editor or input or select or settings or footer or markdown or loader' -q`

Expected: PASS.

- [ ] **Step 3: Move definitions in dependency order**

Move base first; then autocomplete; editor; loaders/image/markdown/pickers/footer.
Internal callers import owner modules directly. `component.py` contains only:

```python
from .components import *
from .components import __all__
```

No compatibility aliases are introduced.

- [ ] **Step 4: Run component/golden suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/tui/components tests/test_tui.py tests/test_tui_dispatcher.py -q`

Expected: PASS.

- [ ] **Step 5: Run component architecture gate green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_facade_boundaries.py -k component -q`

Expected: facade is at most 120 lines with zero definitions; owner modules are at most 750 lines and do not import the facade.

- [ ] **Step 6: Commit component decomposition**

```bash
git add travis/tui/component.py travis/tui/components tests/tui/components tests/test_tui.py tests/test_tui_dispatcher.py
git commit -m "refactor: split TUI component owners"
```

### Task 4: Extract session types, models, bash, tooling, and persistence

**Files:**
- Create: `travis/coding_agent/session_types.py`
- Create: `travis/coding_agent/session_models.py`
- Create: `travis/coding_agent/session_bash.py`
- Create: `travis/coding_agent/session_tooling.py`
- Create: `travis/coding_agent/session_persistence.py`
- Reduce: `travis/coding_agent/agent_session.py`
- Create: `tests/coding_agent/test_session_models.py`
- Create: `tests/coding_agent/test_session_bash.py`
- Create: `tests/coding_agent/test_session_tooling.py`
- Create: `tests/coding_agent/test_session_persistence.py`

**Interfaces:**
- `SessionModelController.set_model/cycle_model/set_thinking_level/cycle_thinking_level`.
- `SessionBashController.execute/abort/record/flush_pending`.
- `SessionToolController.refresh/active_names/set_active/definition/describe_all/build_system_prompt`.
- `SessionPersistence.entries/branch/navigate/export_html/export_json/stats/context_usage/record_initial_state`.

- [ ] **Step 1: Write collaborator behavior tests using explicit fakes**

```python
def test_model_controller_persists_model_change(fake_agent, fake_store) -> None:
    controller = SessionModelController(agent=fake_agent, store=fake_store, emit=lambda event: None)
    controller.set_model(make_model("provider", "next"))
    assert fake_agent.state.model.id == "next"
    assert fake_store.entries[-1]["type"] == "model_change"


def test_bash_controller_records_completed_command(fake_runner, fake_store) -> None:
    controller = SessionBashController(run=fake_runner, store=fake_store, emit=lambda event: None)
    result = controller.execute("printf hi")
    assert result.output == "hi"
    assert fake_store.entries[-1]["type"] == "custom_message"


def test_persistence_navigation_restores_context(fake_store, fake_state) -> None:
    persistence = SessionPersistence(store=fake_store, state=fake_state)
    persistence.navigate("entry-2")
    assert fake_state.messages == fake_store.context_at("entry-2").messages
```

- [ ] **Step 2: Run new tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent/test_session_models.py tests/coding_agent/test_session_bash.py tests/coding_agent/test_session_tooling.py tests/coding_agent/test_session_persistence.py -q`

Expected: FAIL because collaborators do not exist.

- [ ] **Step 3: Move types and controller groups**

Move event/result dataclasses into `session_types.py`. Move model/thinking methods,
bash methods, tool/resource methods, and persistence/tree/export/stats methods
into the named controllers. Constructors receive explicit `agent`, `store`,
`emit`, resource/policy, and runner dependencies; no controller imports
`AgentSession`.

`AgentSession` exposes public read-only properties needed by callers:

```python
@property
def artifacts(self) -> SessionArtifacts:
    return self._artifacts

@property
def shell_command_prefix(self) -> str:
    return self._runtime.shell_command_prefix

@property
def shell_path(self) -> str:
    return self._runtime.shell_path

def record_initial_state(self) -> None:
    self.persistence.record_initial_state()
```

- [ ] **Step 4: Delegate public facade calls**

Facade methods contain one-line delegation, for example:

```python
def set_model(self, model: Model) -> None:
    self.models.set_model(model)

def execute_bash(self, command: str, on_chunk=None, options: dict | None = None) -> BashResult:
    return self.bash.execute(command, on_chunk=on_chunk, options=options)
```

Update internal/app/TUI consumers to use collaborators/public properties, never
new cross-file private access.

- [ ] **Step 5: Run collaborator and session characterization green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent tests/test_coding_agent.py tests/test_app_integration.py -q`

Expected: PASS.

- [ ] **Step 6: Commit first session extraction**

```bash
git add travis/coding_agent/session_types.py travis/coding_agent/session_models.py travis/coding_agent/session_bash.py travis/coding_agent/session_tooling.py travis/coding_agent/session_persistence.py travis/coding_agent/agent_session.py tests/coding_agent tests/test_coding_agent.py tests/test_app_integration.py
git commit -m "refactor: extract session model tool bash and persistence owners"
```

### Task 5: Extract extensions and subagent ownership

**Files:**
- Create: `travis/coding_agent/session_extensions.py`
- Create: `travis/coding_agent/session_subagents.py`
- Create: `travis/coding_agent/subagent_trace.py`
- Reduce: `travis/coding_agent/agent_session.py`
- Create: `tests/coding_agent/test_session_extensions.py`
- Create: `tests/coding_agent/test_session_subagents.py`
- Modify: `tests/test_subagents.py`

**Interfaces:**
- `SessionExtensionController.bind/reload/try_command/has_command/context/dispose`.
- `SessionSubagentController.tool_definitions/spawn/wait/list/result/cancel/shutdown`.
- `subagent_trace` owns trace/result serialization and formatting.

- [ ] **Step 1: Add explicit lifecycle tests**

```python
def test_extension_controller_disposes_registrations_in_reverse_order(harness) -> None:
    controller = harness.controller()
    controller.bind(harness.extension("one"))
    controller.bind(harness.extension("two"))
    controller.dispose()
    assert harness.close_trace == ["two", "one"]


def test_subagent_controller_integrates_result_once(harness) -> None:
    child = harness.controller.spawn(harness.request())
    result = harness.controller.wait(child.task_id)
    assert result.status == "completed"
    assert harness.persisted_results == [result]
```

- [ ] **Step 2: Run lifecycle tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent/test_session_extensions.py tests/coding_agent/test_session_subagents.py -q`

Expected: FAIL because controllers do not exist.

- [ ] **Step 3: Move extension/subagent groups and delegates**

Move extension resource lifecycle, provider registration, command/context hooks,
subagent commands/tools/runtime/trace, and result formatting into the named
owners. Inject provider control plane, runtime, store, tools, emit, and context
callbacks explicitly. Remove the old `run` subagent tool alias; canonical tool
names are defined in the cleanup plan.

- [ ] **Step 4: Run extension/subagent characterization green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent/test_session_extensions.py tests/coding_agent/test_session_subagents.py tests/test_subagents.py tests/coding_agent/test_agent_session_characterization.py -q`

Expected: PASS.

- [ ] **Step 5: Commit extension/subagent extraction**

```bash
git add travis/coding_agent/session_extensions.py travis/coding_agent/session_subagents.py travis/coding_agent/subagent_trace.py travis/coding_agent/agent_session.py tests/coding_agent tests/test_subagents.py
git commit -m "refactor: extract session extension and subagent owners"
```

### Task 6: Extract turn, policy, and event ownership

**Files:**
- Create: `travis/coding_agent/session_turns.py`
- Create: `travis/coding_agent/session_policy_controller.py`
- Create: `travis/coding_agent/session_events.py`
- Reduce: `travis/coding_agent/agent_session.py`
- Create: `tests/coding_agent/test_session_turns.py`
- Create: `tests/coding_agent/test_session_policy_controller.py`
- Create: `tests/coding_agent/test_session_events.py`

**Interfaces:**
- `SessionTurnController.prompt/continue_/steer/follow_up/send_custom/prepare_next_turn/run_prompt`.
- `SessionPolicyController.before_tool_call/after_tool_call/should_stop_after_turn`.
- `SessionEventController.handle/subscribe/emit`.

- [ ] **Step 1: Write turn/policy/event tests**

```python
def test_turn_controller_preserves_mailbox_order(harness) -> None:
    harness.mailbox.steer("first")
    harness.mailbox.follow_up("second")
    harness.controller.prepare_next_turn()
    assert harness.agent_prompts == ["first", "second"]


def test_policy_controller_emits_guardrail_halt_once(harness) -> None:
    decision = harness.controller.after_tool_call(harness.repeated_failure())
    assert decision.stop is True
    assert harness.events.count("guardrail_halt") == 1


def test_event_controller_persists_before_notifying(harness) -> None:
    harness.controller.handle(harness.message_event())
    assert harness.trace == ["persist", "notify"]
```

- [ ] **Step 2: Run tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent/test_session_turns.py tests/coding_agent/test_session_policy_controller.py tests/coding_agent/test_session_events.py -q`

Expected: FAIL because controllers do not exist.

- [ ] **Step 3: Move controller groups and preserve callback order**

Move prompt/mailbox/stream recovery, agent prompt/retry, policy callbacks, and
event persistence/fan-out into the named modules. Inject agent, mailbox, store,
policy pipeline, emit/persist callbacks, model/extension context, and turn state.
No controller imports the session facade.

- [ ] **Step 4: Run session characterization and red-zone loop suites**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent tests/test_agent_loop.py tests/test_agent_runtime_hardening.py tests/test_app_integration.py -q`

Expected: PASS with exact event/tool ordering.

- [ ] **Step 5: Enable AgentSession size/dependency gate**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_facade_boundaries.py -k agent_session -q`

Expected: `agent_session.py` at most 900 lines, `AgentSession` at most 50 methods,
all owner modules at most 750 lines, and no collaborator imports the facade.

- [ ] **Step 6: Commit final session facade**

```bash
git add travis/coding_agent/session_turns.py travis/coding_agent/session_policy_controller.py travis/coding_agent/session_events.py travis/coding_agent/agent_session.py tests/coding_agent tests/architecture/test_facade_boundaries.py
git commit -m "refactor: reduce AgentSession to a composition facade"
```

### Task 7: Extract interactive controllers and bounded shutdown owner

**Files:**
- Create: `travis/tui/interactive_turn_controller.py`
- Create: `travis/tui/interactive_command_dispatcher.py`
- Create: `travis/tui/interactive_session_commands.py`
- Create: `travis/tui/interactive_model_auth.py`
- Create: `travis/tui/interactive_process_commands.py`
- Create: `travis/tui/interactive_view.py`
- Create: `travis/tui/interactive_extensions.py`
- Create: `travis/tui/footer_data.py`
- Consolidate: `travis/tui/interactive_shutdown.py`
- Reduce: `travis/tui/interactive_mode.py`
- Create/split: `tests/tui/test_interactive_*.py`.

**Interfaces:**
- `InteractiveCommandDispatcher.dispatch(prompt) -> CommandDisposition`.
- `InteractiveTurnController.start/is_active/steer/abort/finish`.
- `InteractiveShutdown.close() -> ShutdownResult` is idempotent and bounded.
- `InteractiveMode` retains `__init__`, `init`, and `run` as composition/input-loop facade.

- [ ] **Step 1: Add dispatcher unit tests from the precedence table**

```python
def test_dispatcher_routes_agent_prompt_only_after_all_commands(harness) -> None:
    disposition = harness.dispatcher.dispatch("implement feature")
    assert disposition is CommandDisposition.AGENT_PROMPT
    assert harness.trace == ["extension-shortcut", "slash-commands", "active-turn", "agent-prompt"]
```

- [ ] **Step 2: Add exact shutdown-order characterization**

```python
def test_interactive_shutdown_order_is_stable(harness) -> None:
    result = harness.shutdown.close()
    assert result.completed is True
    assert harness.trace == [
        "user-commands.close", "dispatcher.drain", "turn.stop", "dispatcher.drain",
        "session-executor.close", "model-loader.close", "unsubscribe",
        "footer.dispose", "trace.close", "tui.stop", "signal.restore",
    ]
```

- [ ] **Step 3: Run interactive tests green before movement**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/tui/test_interactive_dispatch_characterization.py tests/tui/test_interactive_shutdown_characterization.py tests/test_tui.py -k 'interactive or ctrl_c or sigint or model or auth or process or session' -q`

Expected: PASS.

- [ ] **Step 4: Move command groups in precedence order**

Extract turn/SIGINT, dispatcher/parsers, session commands, model/auth/params,
process/user bash, view/rendering, extensions/widgets, footer/git, and shutdown.
The facade wires controllers and loops on input. Dispatcher precedence remains:

```text
extension shortcut -> exit -> empty -> help -> session -> processes -> bash ->
compact -> auth -> model -> params -> allow -> extension command -> unknown slash ->
active-turn steering -> agent prompt
```

- [ ] **Step 5: Run complete TUI suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/tui tests/test_tui.py tests/test_tui_dispatcher.py tests/test_tui_user_commands.py tests/test_session_commands.py -q`

Expected: PASS.

- [ ] **Step 6: Enable InteractiveMode size/dependency gate**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_facade_boundaries.py -k interactive -q`

Expected: `interactive_mode.py` at most 500 lines, `InteractiveMode` at most 20
methods, each controller at most 750 lines, and no controller imports the facade.

- [ ] **Step 7: Commit interactive facade**

```bash
git add travis/tui tests/tui tests/test_tui.py tests/test_tui_dispatcher.py tests/test_tui_user_commands.py tests/test_session_commands.py tests/architecture/test_facade_boundaries.py
git commit -m "refactor: reduce InteractiveMode to a composition facade"
```

### Task 8: Run complete decomposition parity

**Files:**
- Modify: `docs/verification/facade-decomposition.md`

**Interfaces:**
- Produces current full-suite and architecture evidence.

- [ ] **Step 1: Run all characterization and architecture gates**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent tests/tui tests/ai/providers tests/compaction tests/architecture -q`

Expected: PASS.

- [ ] **Step 2: Run full Python suite**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests -q`

Expected: PASS with no reduction in collected test count from the pre-extraction baseline.

- [ ] **Step 3: Record evidence and commit**

Record exact line/method counts, collected/passed counts, and command output
summaries in `docs/verification/facade-decomposition.md`.

```bash
git add docs/verification/facade-decomposition.md
git commit -m "docs: record facade decomposition verification"
```
