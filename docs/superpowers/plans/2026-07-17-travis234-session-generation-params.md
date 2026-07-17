# Travis234 Session Generation Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/params` a direct, typed, durable editor for the active Travis234 session's thinking level and provider generation parameters without changing the Agent loop or compaction behavior.

**Architecture:** `AgentSession` owns a normalized override-only `GenerationParams` snapshot persisted as a non-message JSONL event. A focused TUI params controller merges startup values with the active session snapshot, handles direct command grammar, recomputes capability warnings, and wraps only the interactive main-turn provider stream so every call receives the same effective parameters while existing safety caps remain authoritative.

**Tech Stack:** Python 3, immutable dataclasses, append-only JSONL sessions, pytest, the existing Travis234 TUI/runtime facade, npm launcher packaging, Docker release smoke checks.

## Global Constraints

- Keep the product/CLI names `Travis234` and `travis234`, and the Python import package `travis`.
- Treat the repository root as the only application tree; do not touch the untracked `appv231/` clone.
- Preserve user data under `~/.travis234`; do not add state aliases or migrations.
- Preserve core Agent-loop ordering, iteration budgeting, bounded parallel execution, messages, context accounting, and every normal/deep/automatic compaction path.
- Limit mutation to interactive `/params`; compaction and auxiliary summarizer provider calls retain their current parameters.
- Write each regression test first and observe the expected failure before changing production code.
- Preserve unrelated dirty State Signals work in every overlapping TUI, test, and README file.
- Perform no git commits, resets, merges, pushes, or other git operations.
- Never persist sources, credentials, headers, prompt text, provider preferences, or unknown keys in `generation_params_change` events.
- Reject parameter writes during an active turn; permit read-only `/params` displays.
- Complete Python, npm launcher, package-build, clean-wheel, parity, PTY, and container verification before reporting completion.

---

## File responsibility map

- `travis/ai/providers/params.py`: public supported-field metadata plus strict normalize/serialize/replace/remove primitives; remains the sole type/range parser.
- `travis/coding_agent/session_store.py`: append and replay the complete override snapshot as branch-local, non-message state.
- `travis/coding_agent/session_generation_params.py`: focused `AgentSession` ownership and atomic set/reset operations.
- `travis/coding_agent/agent_session.py`: compose the new controller and initialize it from restored session context.
- `travis/coding_agent/session_persistence.py`: restore branch-local overrides when branching or navigating the session tree.
- `travis/tui/interactive_params.py`: command grammar, display, active-turn guard, effective merge, capability warnings, and main-turn stream adapter.
- `travis/tui/interactive_mode.py`: compose and initialize the focused params controller.
- `travis/tui/interactive_model_auth.py`: remove the old read-only params owner and notify the params controller after model switches.
- `travis/tui/interactive_process_commands.py`: refresh the active session override view after session rebound.
- `travis/tui/interactive_turn_controller.py`: inject the params stream adapter into interactive main turns only.
- `travis/tui/interactive_session_commands.py` and autocomplete command metadata: document the direct set/reset syntax.
- Tests: cover helpers, durable replay/branching, facade atomicity, command UX, runtime provider options, and isolation.
- `README.md`: document the user-facing command contract after behavior passes.

---

### Task 1: Strict generation-parameter snapshot primitives

**Files:**
- Modify: `tests/test_ai_generation_params.py`
- Modify: `travis/ai/providers/params.py`

**Interfaces:**
- Produces: `GENERATION_PARAM_FIELDS: tuple[str, ...]`
- Produces: `generation_params_to_mapping(params: GenerationParams) -> dict[str, object]`
- Produces: `generation_params_from_session_mapping(values: object) -> GenerationParams | None`
- Produces: `replace_generation_param(params: GenerationParams, name: str, raw_value: object, *, source: str = "session") -> GenerationParams`
- Produces: `remove_generation_param(params: GenerationParams, name: str, *, source: str = "session") -> GenerationParams`

- [x] **Step 1: Write failing normalization and mutation tests**

```python
def test_session_mapping_round_trip_is_normalized_and_source_labeled():
    params = generation_params_from_session_mapping({
        "temperature": 0.2,
        "parallel_tool_calls": True,
        "stop": ["END", "STOP"],
    })
    assert params is not None
    assert generation_params_to_mapping(params) == {
        "temperature": 0.2,
        "parallel_tool_calls": True,
        "stop": ["END", "STOP"],
    }
    assert dict(params.sources) == {
        "temperature": "session",
        "parallel_tool_calls": "session",
        "stop": "session",
    }

@pytest.mark.parametrize("values", [None, [], {"api_key": "sk-secret"}, {"temperature": None}, {"stop": []}])
def test_invalid_session_snapshot_is_rejected(values):
    assert generation_params_from_session_mapping(values) is None

def test_replace_and_remove_generation_param_keep_only_explicit_fields():
    params = replace_generation_param(GenerationParams(), "temperature", "0.2")
    params = replace_generation_param(params, "stop", '["END", "STOP"]')
    assert generation_params_to_mapping(remove_generation_param(params, "temperature")) == {
        "stop": ["END", "STOP"]
    }

@pytest.mark.parametrize("value", ["", "none", "null", None])
def test_replace_requires_explicit_reset_for_unset_values(value):
    with pytest.raises(ValueError, match="/params reset"):
        replace_generation_param(GenerationParams(), "temperature", value)
```

- [x] **Step 2: Run the new tests and confirm they fail because the public helpers do not exist**

Run: `pytest -q tests/test_ai_generation_params.py`

Expected: collection/import failure for the new helper names.

- [x] **Step 3: Export the supported fields and implement strict snapshot helpers through the existing parser**

```python
GENERATION_PARAM_FIELDS = _PARAM_FIELDS

def generation_params_to_mapping(params: GenerationParams) -> dict[str, object]:
    values: dict[str, object] = {}
    for field_name in GENERATION_PARAM_FIELDS:
        value = getattr(params, field_name)
        if value is None or (field_name == "stop" and not value):
            continue
        values[field_name] = list(value) if field_name == "stop" else value
    return values

def generation_params_from_session_mapping(values: object) -> GenerationParams | None:
    if not isinstance(values, dict) or any(key not in GENERATION_PARAM_FIELDS for key in values):
        return None
    if any(_is_unset(value) for value in values.values()):
        return None
    try:
        parsed = params_from_mapping(values, source="session")
    except (TypeError, ValueError):
        return None
    if set(generation_params_to_mapping(parsed)) != set(values):
        return None
    return parsed

def replace_generation_param(params, name, raw_value, *, source="session"):
    if name not in GENERATION_PARAM_FIELDS:
        raise ValueError(f"unsupported generation parameter: {name}")
    if _is_unset(raw_value):
        raise ValueError(f"{name} requires a value; use /params reset {name}")
    candidate = generation_params_to_mapping(params)
    parsed = params_from_mapping({name: raw_value}, source=source)
    candidate[name] = generation_params_to_mapping(parsed)[name]
    return params_from_mapping(candidate, source=source)

def remove_generation_param(params, name, *, source="session"):
    if name not in GENERATION_PARAM_FIELDS:
        raise ValueError(f"unsupported generation parameter: {name}")
    candidate = generation_params_to_mapping(params)
    candidate.pop(name, None)
    return params_from_mapping(candidate, source=source)
```

Export all five names in `__all__`. Empty `{}` is the only valid empty reset snapshot; non-empty snapshots must round-trip every input field.

- [x] **Step 4: Run the focused helper suite**

Run: `pytest -q tests/test_ai_generation_params.py`

Expected: all generation-parameter tests pass.

---

### Task 2: Durable branch-local session snapshots

**Files:**
- Modify: `tests/test_coding_persistence_and_compaction.py`
- Modify: `tests/test_session_parity.py`
- Modify: `travis/coding_agent/session_store.py`

**Interfaces:**
- Consumes: `generation_params_to_mapping()` and `generation_params_from_session_mapping()` from Task 1.
- Produces: `SessionContextSnapshot.generation_params: GenerationParams`
- Produces: `SessionStore.append_generation_params_change(params: GenerationParams) -> str`

- [x] **Step 1: Write failing append/replay/invalid-event tests**

```python
def test_generation_param_snapshots_restore_latest_valid_active_branch(tmp_path):
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    first = store.append_generation_params_change(
        params_from_mapping({"temperature": "0.2"}, source="session")
    )
    store.append_generation_params_change(
        params_from_mapping({"temperature": "0.4", "max_tokens": "4096"}, source="session")
    )
    assert generation_params_to_mapping(store.build_context().generation_params) == {
        "temperature": 0.4,
        "max_tokens": 4096,
    }
    store.branch(first)
    assert generation_params_to_mapping(store.build_context().generation_params) == {
        "temperature": 0.2
    }

def test_empty_snapshot_resets_and_invalid_snapshot_preserves_last_valid(tmp_path):
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    store.append_generation_params_change(
        params_from_mapping({"temperature": "0.2"}, source="session")
    )
    store._append_entry({"type": "generation_params_change", "params": {"api_key": "sk-secret"}}, durable=True)
    assert store.build_context().generation_params.temperature == 0.2
    store.append_generation_params_change(GenerationParams())
    assert generation_params_to_mapping(store.build_context().generation_params) == {}
```

Also assert that a `generation_params_change` event does not produce an Agent message, survive export/branched-session copy, and remains visible in the session parity entry-type list.

- [x] **Step 2: Run the focused persistence tests and confirm failure**

Run: `pytest -q tests/test_coding_persistence_and_compaction.py -k generation_param tests/test_session_parity.py`

Expected: failures for the missing snapshot field and append method.

- [x] **Step 3: Add the snapshot field, append method, and defensive replay**

```python
@dataclass
class SessionContextSnapshot:
    messages: list[AgentMessage]
    thinking_level: str
    model: dict[str, str] | None
    session_name: str | None
    generation_params: GenerationParams

def append_generation_params_change(self, params: GenerationParams) -> str:
    return self._append_entry(
        {"type": "generation_params_change", "params": generation_params_to_mapping(params)},
        durable=True,
    )
```

Initialize replay with `GenerationParams()`. During the existing full active-branch state scan, call `generation_params_from_session_mapping(entry.get("params"))`; replace the previous valid snapshot only when parsing returns a `GenerationParams`. Return that value from `build_context()`. Do not add this event to `_entry_to_message()`.

- [x] **Step 4: Run persistence, compaction, export, and parity tests**

Run: `pytest -q tests/test_coding_persistence_and_compaction.py tests/test_session_parity.py tests/test_coding_exports_and_boundaries.py`

Expected: all selected tests pass and compaction context remains unchanged.

---

### Task 3: Atomic AgentSession ownership and branch restoration

**Files:**
- Create: `travis/coding_agent/session_generation_params.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/session_persistence.py`
- Modify: `tests/test_coding_persistence_and_compaction.py`

**Interfaces:**
- Consumes: `SessionStore.append_generation_params_change()` and `SessionContextSnapshot.generation_params` from Task 2.
- Produces: `generation_param_overrides: GenerationParams`
- Produces: `set_generation_param_override(name: str, raw_value: object) -> GenerationParams`
- Produces: `reset_generation_param_override(name: str) -> GenerationParams`
- Produces: `reset_generation_param_overrides() -> GenerationParams`
- Produces: `_restore_generation_param_overrides(params: GenerationParams) -> None`

- [x] **Step 1: Write failing facade, resume, branch, idempotence, and atomicity tests**

```python
def test_session_generation_overrides_resume_and_follow_active_branch(session_factory):
    session = session_factory()
    before = session.get_session_leaf_id()
    session.set_generation_param_override("temperature", "0.2")
    after = session.get_session_leaf_id()
    assert session.generation_param_overrides.temperature == 0.2
    resumed = session_factory(session_path=session.session_path)
    assert resumed.generation_param_overrides.temperature == 0.2
    resumed.branch(before)
    assert resumed.generation_param_overrides.temperature is None
    resumed.branch(after)
    assert resumed.generation_param_overrides.temperature == 0.2

def test_persistence_failure_does_not_publish_candidate(session, monkeypatch):
    session.set_generation_param_override("temperature", "0.2")
    monkeypatch.setattr(session._session_store, "append_generation_params_change", lambda _params: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        session.set_generation_param_override("temperature", "0.4")
    assert session.generation_param_overrides.temperature == 0.2
```

Assert resetting a missing field and resetting an already empty map append no entry.

- [x] **Step 2: Run the new session-owner tests and confirm failure**

Run: `pytest -q tests/test_coding_persistence_and_compaction.py -k generation_param`

Expected: missing controller methods/property.

- [x] **Step 3: Implement the focused owner with append-before-publish semantics**

```python
class SessionGenerationParams:
    @property
    def generation_param_overrides(self) -> GenerationParams:
        return self._generation_param_overrides

    def set_generation_param_override(self, name: str, raw_value: object) -> GenerationParams:
        candidate = replace_generation_param(self._generation_param_overrides, name, raw_value)
        return self._publish_generation_param_overrides(candidate)

    def reset_generation_param_override(self, name: str) -> GenerationParams:
        candidate = remove_generation_param(self._generation_param_overrides, name)
        return self._publish_generation_param_overrides(candidate)

    def reset_generation_param_overrides(self) -> GenerationParams:
        return self._publish_generation_param_overrides(GenerationParams())

    def _publish_generation_param_overrides(self, candidate: GenerationParams) -> GenerationParams:
        if candidate == self._generation_param_overrides:
            return candidate
        if self._session_store is not None:
            self._session_store.append_generation_params_change(candidate)
        self._generation_param_overrides = candidate
        return candidate

    def _restore_generation_param_overrides(self, params: GenerationParams) -> None:
        self._generation_param_overrides = params
```

Compose `SessionGenerationParams` into `_SessionRuntime`. Initialize `_generation_param_overrides` from the restored snapshot before exposing the runtime. In both `branch()` and `navigate_tree()`, call `_restore_generation_param_overrides(snapshot.generation_params)` beside thinking/name restoration.

- [x] **Step 4: Run session facade, persistence, navigation, and catalog tests**

Run: `pytest -q tests/test_coding_persistence_and_compaction.py tests/test_coding_resources_and_services.py tests/test_session_catalog.py`

Expected: all selected tests pass.

---

### Task 4: Direct `/params` grammar, display, writes, resets, and warnings

**Files:**
- Create: `travis/tui/interactive_params.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_model_auth.py`
- Modify: `travis/tui/interactive_process_commands.py`
- Modify: `travis/tui/interactive_session_commands.py`
- Modify: command autocomplete metadata under `travis/tui/`
- Modify: `tests/test_tui_runtime_compaction_and_models.py`
- Modify: `tests/tui/test_interactive_dispatch_characterization.py`

**Interfaces:**
- Consumes: AgentSession methods from Task 3 and existing `build_generation_payload()` / `determine_api_mode()` policy.
- Produces: `_effective_generation_params() -> GenerationParams`
- Produces: `_refresh_generation_param_state() -> None`
- Produces: `_run_params_command(query: str | None = None) -> None`

- [x] **Step 1: Write failing command tests before the controller**

```python
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("temperature 0.2", ("temperature", "0.2")),
        ("stop END token,STOP token", ("stop", "END token,STOP token")),
    ],
)
def test_params_direct_set_preserves_full_value_remainder(mode, query, expected):
    mode._run_params_command(query)
    mode.app.session.set_generation_param_override.assert_called_once_with(*expected)

def test_params_thinking_uses_existing_session_owner(mode):
    mode._run_params_command("thinking high")
    mode.app.session.set_thinking_level.assert_called_once_with("high")

def test_params_reset_one_and_all_are_explicit_and_thinking_is_independent(mode):
    mode._run_params_command("reset temperature")
    mode.app.session.reset_generation_param_override.assert_called_once_with("temperature")
    mode._run_params_command("reset")
    mode.app.session.reset_generation_param_overrides.assert_called_once_with()

def test_params_writes_are_rejected_while_turn_active_but_reads_work(mode, monkeypatch):
    monkeypatch.setattr(mode, "_is_turn_active", lambda: True)
    mode._run_params_command("temperature 0.2")
    mode.app.session.set_generation_param_override.assert_not_called()
    mode._run_params_command("")
    assert mode._show_status.call_count == 2
```

Cover unknown fields, missing values, `none`/`null`/empty set values, `/params reset thinking`, filtered display, source labels, recomputed dropped warnings, next-turn success copy, model switch, and session rebound. Preserve `_parse_params_command("/params") == ""` and full raw remainder classification.

- [x] **Step 2: Run the focused TUI tests and confirm the old read-only behavior fails**

Run: `pytest -q tests/test_tui_runtime_compaction_and_models.py -k params tests/tui/test_interactive_dispatch_characterization.py -k params`

Expected: direct set/reset tests fail because the old handler only filters display.

- [x] **Step 3: Implement the focused controller and compose it into InteractiveMode**

```python
class InteractiveParams:
    def _effective_generation_params(self) -> GenerationParams:
        return merge_generation_params(
            self.startup_generation_params,
            self.app.session.generation_param_overrides,
        )

    def _refresh_generation_param_state(self) -> None:
        self.generation_params = self._effective_generation_params()
        model = self.app.session.model
        payload = build_generation_payload(
            provider=model.provider,
            api_mode=determine_api_mode(model.provider, model.base_url),
            params=self.generation_params,
            tools_enabled=bool(getattr(self.app.session, "tools", [])),
        )
        self.generation_param_warnings = list(payload.warnings)
```

`_run_params_command()` splits only once into `name` and full `value`. It treats `reset` as its own action, routes `thinking` to `set_thinking_level`, routes generation fields to the session methods, rejects writes while `_is_turn_active()`, then refreshes and reports the new effective value. It retains the existing provider/model-prefixed read display and filter behavior. Move the old handler and warning formatter out of `InteractiveModelAuth` into this file.

Initialize `startup_generation_params = generation_params or GenerationParams()` and refresh from the current session. Add `InteractiveParams` to `_InteractiveRuntime`. Call `_refresh_generation_param_state()` from `_show_model_switched()` and `_rebind_session_ui()` before footer/render refresh.

- [x] **Step 4: Update command help and autocomplete copy**

Use the exact concise help line:

```text
/params [name [value] | reset [name]] - Show or change session model parameters.
```

Expose supported names and `reset` as `/params` completions without introducing a picker.

- [x] **Step 5: Run command, model-switch, rebound, help, and autocomplete tests**

Run: `pytest -q tests/test_tui_runtime_compaction_and_models.py tests/tui/test_interactive_dispatch_characterization.py tests/test_tui_commands_and_extensions.py`

Expected: all selected tests pass with existing State Signals assertions unchanged.

---

### Task 5: Main-turn provider adapter with safety-cap preservation

**Files:**
- Modify: `travis/tui/interactive_params.py`
- Modify: `travis/tui/interactive_turn_controller.py`
- Modify: `tests/test_tui_runtime_compaction_and_models.py`
- Modify: `tests/test_ai_provider_request.py`

**Interfaces:**
- Consumes: `_effective_generation_params()` from Task 4.
- Produces: `_stream_with_session_generation_params(model: Model, context: Context, options: SimpleStreamOptions | None = None)`.

- [x] **Step 1: Write failing provider-call tests**

```python
def test_interactive_turn_applies_effective_params_to_every_provider_call(mode, faux_stream):
    mode.startup_generation_params = params_from_mapping({"top_p": "0.9", "max_tokens": "8192"}, source="cli")
    mode.app.session.set_generation_param_override("temperature", "0.2")
    mode._run_turn_thread("use a tool then finish", 0, 0)
    assert len(faux_stream.options) == 2
    for options in faux_stream.options:
        assert options.generation_params.temperature == 0.2
        assert options.generation_params.top_p == 0.9
        assert options.max_tokens == 4096
```

Add cases proving startup values work without overrides, reset reveals startup values, separate sessions do not leak, `max_tokens` can lower but never raise the existing runtime/model cap, retries keep the effective values, and no compaction/branch-summary stream uses this wrapper.

- [x] **Step 2: Run the new provider-adapter tests and confirm failure**

Run: `pytest -q tests/test_tui_runtime_compaction_and_models.py -k "params or generation" tests/test_ai_provider_request.py -k max_tokens`

Expected: captured options lack session generation parameters.

- [x] **Step 3: Implement the immutable options adapter**

```python
def _stream_with_session_generation_params(self, model, context, options=None):
    current = options or SimpleStreamOptions()
    effective = self._effective_generation_params()
    requested_max = effective.max_tokens
    runtime_max = current.max_tokens
    if requested_max is None:
        bounded_max = runtime_max
    elif runtime_max is None:
        bounded_max = requested_max
    else:
        bounded_max = min(requested_max, runtime_max)
    adapted = replace(
        current,
        generation_params=effective,
        max_tokens=bounded_max,
    )
    return self.app.session.model_registry.stream_simple(model, context, adapted)
```

Pass `stream_fn=self._stream_with_session_generation_params` only in `InteractiveTurnController._run_turn_thread()`'s `app.run_turn(...)`. Do not alter `agent_loop.py`, `CodingApp.run_turn()`, provider request preparation, compaction, or branch summarization.

- [x] **Step 4: Run provider, retry, tool continuation, overflow recovery, compaction, and TUI tests**

Run: `pytest -q tests/test_tui_runtime_compaction_and_models.py tests/test_ai_provider_request.py tests/test_app_integration.py tests/test_compaction_integration.py`

Expected: all selected tests pass; provider safety-clamp expectations remain unchanged.

---

### Task 6: Documentation and complete verification

**Files:**
- Modify: `README.md`
- Verify: all changed production and test files

**Interfaces:**
- Consumes: the completed command behavior from Tasks 1-5.
- Produces: user documentation and evidence for release readiness.

- [x] **Step 1: Update README command documentation after behavior passes**

Document these exact examples:

```text
/params
/params temperature
/params temperature 0.2
/params thinking high
/params stop END,STOP
/params reset temperature
/params reset
```

State that generation overrides are session-local and durable, resumed values win only for fields explicitly changed, `reset` reveals current startup/provider values, changes apply on the next turn, writes are rejected during an active turn, unsupported typed values remain saved but are marked `dropped`, and output/context safety caps can still lower `max_tokens`.

- [x] **Step 2: Run formatting/static checks defined by repository tooling**

Run the formatter/linter/type-check commands documented in `README.md`, `pyproject.toml`, and `package.json` if present.

Expected: zero failures; no unrelated file rewrites.

- [x] **Step 3: Run the complete Python suite**

Run: `pytest -q`

Expected: all tests pass.

- [x] **Step 4: Run npm launcher tests and both package builds**

Run the exact npm test and Python/npm build commands from the repository release guide.

Expected: launcher tests pass and both distributable artifact sets build successfully.

- [x] **Step 5: Test the clean built wheel and installed entrypoint**

Create an isolated temporary virtual environment, install only the newly built wheel, invoke `travis234 --help`, and run the README PTY protocol against that installed entrypoint with direct `/params` set/show/reset cases.

Expected: imports come from the wheel, the CLI starts, and the PTY transcript proves next-turn parameter application and durable resume without exposing credentials.

- [x] **Step 6: Run Pi/Hermes parity and release-container smoke checks**

Use the exact parity and container commands in the repository release guide.

Expected: parity checks pass and the release container starts the packaged `travis234` entrypoint successfully.

- [x] **Step 7: Review the final filesystem diff without performing git operations**

Inspect changed-file content and `git diff` read-only output only. Confirm no change to `agent_loop.py`, compaction algorithms, `appv231/`, credentials, state paths, budgets, or bounded parallel execution; confirm pre-existing State Signals edits remain intact.

Expected: only the approved `/params` implementation, its spec/plan/tests/docs, and prior user changes are present.
