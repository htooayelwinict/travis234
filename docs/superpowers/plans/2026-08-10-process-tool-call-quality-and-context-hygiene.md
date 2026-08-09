# Process Tool-Call Quality and Context Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan inline, task by task, with review checkpoints. Do not dispatch subagents unless the user separately and explicitly authorizes them.

**Goal:** Make unfamiliar models, especially OpenRouter Minimax M3, emit valid `process` calls on the first attempt while preserving Travis234's runtime loop, process lifecycle, autonomy, and context-envelope contracts.

**Architecture:** Replace the provider-facing nine-branch `oneOf` process schema with one conventional flat object, while retaining `_validate_args()` as the action-specific runtime authority. Move session-specific process examples out of the permanent system prompt and into ordinary Bash/process tool results containing the real process ID and cursor. Keep the generic loop, AgentHarness, iteration accounting, compaction, persistence, and process service untouched.

**Tech Stack:** Python 3.13, JSON Schema Draft 7, `jsonschema`, pytest, Travis234 managed Bash/PTY/process tools, the existing context estimator, Node 20/npm launcher checks, `uv`, Twine, Docker, and OpenRouter `minimax/minimax-m3`.

## Global Constraints

- Work only in the repository root `/Users/htooayelwin/lewis/travis234` on `main`.
- Preserve all pre-existing dirty changes. Never edit, stage, remove, or overwrite `docs/superpowers/plans/2026-07-27-red-zone-free-pi-reliability-parity.md` or `docs/travis234-future-agent-framework-brainstorm.md`.
- Never read or print credential values. The root `.env` may be passed only by path to the installed TUI process.
- Add a failing regression before each behavioral fix and observe the focused test fail for the intended reason before changing production code.
- Do not add invalid-schema containment, retry counters, retry walls, halt behavior, action inference, synthetic recovery turns, transcript rewriting, or persisted recovery state.
- Do not modify `travis/agent/agent_loop.py`, `travis/agent/agent.py`, `travis/coding_agent/agent_harness.py`, session iteration budgeting, parallel execution, compaction triggers, or context accounting.
- Keep `_ACTION_FIELDS` and `_validate_args()` authoritative for action-specific required and forbidden fields after compatibility normalization.
- Keep all existing process compatibility repairs: documented aliases, collapsed process IDs, numeric-string coercion, `write_line`, newline-to-`write_raw`, and wait/poll timing normalization.
- Never infer `action`, `session_id`, `cursor`, or input content.
- Do not change process ownership, output limits, timeout semantics, cursor recovery, PTY transport, shutdown, or cleanup behavior.
- No commit, push, tag, publication, account change, or external-state mutation is authorized by this plan. Checkpoint changes in the worktree only.
- Run the twelve-prompt installed-wheel Minimax M3 TUI qualification only after the user explicitly approves execution.

---

## File Responsibility Map

- `travis/coding_agent/tools/process.py`: provider-facing process schema, compatibility preparation, strict action validation, dynamic process call formatting, and process-result footers.
- `travis/coding_agent/tools/bash.py`: running-command handoff; selects PTY input-first guidance versus noninteractive wait-first guidance.
- `travis/coding_agent/system_prompt.py`: compact active-tool routing language only; no loop or session behavior.
- `tests/test_process_tools.py`: schema shape, validation errors, managed Bash/process handoff, PTY sequencing, and prompt-budget regressions.
- `tests/test_coding_resources_and_services.py`: exact built-in Bash/process prompt metadata after compaction.
- `tests/test_coding_tools_and_subagents.py`: active-tool routing wording and default tool-set regression coverage.
- `tests/test_reference_runtime_contract.py`: proof that no generic behavioral recovery policy enters the default prompt.
- `tests/test_context_estimate.py`: unchanged additive envelope-accounting contract.
- `README.md`: user-facing distinction among finite Bash, managed process sessions, PTY input, and durable tmux work.
- `docs/verification/main-process-tool-quality-twelve-prompt-tui.md`: non-secret installed-wheel Minimax M3 evidence created only during approved execution.

## Confirmed Baseline

- The current provider-facing `PROCESS_SCHEMA` is approximately 4,570 compact JSON characters, or 1,143 Travis234-estimated tokens, and uses nine root `oneOf` branches with `const` actions.
- An equivalent flat candidate is approximately 1,239 compact JSON characters, or 310 estimated tokens.
- The current process-specific system-prompt contribution is approximately 1,720 characters, or 430 estimated tokens.
- Prompt 5 started a valid interactive Python PTY and returned `READY`, then Minimax M3 emitted 29 distinct `process {}` calls. Each call had a unique tool ID and provider decision; the loop did not replay a call.
- The generic loop prepares arguments and validates the provider schema before executing the tool. Therefore the flat schema must reject universally invalid shapes, while `_validate_args()` must reject action-specific invalid combinations after schema validation.

## Combined Execution Order

When the user approves both this plan and the separate prompt-deduplication plan, use this order:

1. Execute Tasks 1-5 of this process-quality plan.
2. Run Task 6 Steps 1-2 as the focused process checkpoint.
3. Execute Tasks 1-4 of `2026-08-10-coding-system-prompt-deduplication.md`.
4. Run the full Python/npm/parity/build/Twine/exact-wheel/container gates from Task 5 of that plan, using the detailed commands in this plan's Task 6 Steps 3-8 for the combined worktree.
5. Run Task 7 of this plan once against the exact combined installed wheel.

This order avoids qualifying an intermediate wheel twice while keeping each plan independently executable if the user approves only one of them.

---

### Task 1: Replace the Branching Provider Schema With a Flat Contract

**Files:**
- Modify: `tests/test_process_tools.py`
- Modify: `travis/coding_agent/tools/process.py`

**Interfaces:**
- Consumes: `PROCESS_ACTIONS`, `_PROCESS_FIELDS`, `compile_tool_schema()`, and the existing `AgentTool.compiled_schema` path.
- Produces: `PROCESS_SCHEMA: dict[str, object]` with root `properties`, `required=["action"]`, an action `enum`, and `additionalProperties=False`.
- Preserves: `_ACTION_FIELDS`, `prepare_process_arguments(raw_args)`, and `_validate_args(raw_args)`.

- [ ] **Step 1: Add provider-shape regressions that fail against the current `oneOf` schema**

Add `PROCESS_ACTIONS`, `ToolCall`, `ToolValidationError`, and `validate_tool_arguments` imports, then add these tests near the existing schema test:

```python
def test_process_schema_is_flat_provider_friendly_and_compact() -> None:
    assert PROCESS_SCHEMA["type"] == "object"
    assert "oneOf" not in PROCESS_SCHEMA
    assert "anyOf" not in PROCESS_SCHEMA
    assert PROCESS_SCHEMA["required"] == ["action"]
    assert PROCESS_SCHEMA["additionalProperties"] is False
    assert PROCESS_SCHEMA["properties"]["action"]["enum"] == list(process_tool_module.PROCESS_ACTIONS)
    assert set(PROCESS_SCHEMA["properties"]) == {
        "action",
        "session_id",
        "cursor",
        "input",
        "eof",
        "yield_time_ms",
        "wait_time_ms",
        "max_bytes",
        "rows",
        "cols",
    }
    compact = json.dumps(PROCESS_SCHEMA, separators=(",", ":"), sort_keys=True)
    assert len(compact) <= 1_600


def test_process_flat_schema_rejects_only_universally_invalid_shapes() -> None:
    schema = compile_tool_schema(PROCESS_SCHEMA)

    assert schema.errors({})
    assert schema.errors({"action": "unknown"})
    assert schema.errors({"action": "list", "unknown": True})
    assert not schema.errors({"action": "list"})
    assert not schema.errors({"action": "wait"})
    assert not schema.errors({"action": "write", "session_id": "proc_x"})
```

The last two incomplete action shapes intentionally pass the provider schema so the action-aware runtime can return a focused error instead of exposing a provider-hostile union.

- [ ] **Step 2: Run the two tests and verify RED**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_process_tools.py::test_process_schema_is_flat_provider_friendly_and_compact \
  tests/test_process_tools.py::test_process_flat_schema_rejects_only_universally_invalid_shapes
```

Expected: failure because `PROCESS_SCHEMA` still contains `oneOf`, has no root `properties`, and exceeds the compact budget.

- [ ] **Step 3: Replace `_process_action_schema()` and the root union with one flat schema**

Delete `_process_action_schema()`. Rewrite `_PROCESS_FIELDS["input"]` so one description accurately covers both write modes:

```python
"input": {
    "type": "string",
    "description": (
        "Input for write or write_raw. write accepts one line without a newline and appends Enter; "
        "write_raw sends the text exactly, including control characters or newlines"
    ),
},
```

Use this schema shape:

```python
PROCESS_SCHEMA = {
    "type": "object",
    "description": (
        "Control one process session returned by bash. Choose one action and supply only fields valid for it; "
        "start commands with bash, not process."
    ),
    "properties": {
        "action": {
            "type": "string",
            "enum": list(PROCESS_ACTIONS),
            "description": "Process operation to perform",
        },
        **{name: dict(schema) for name, schema in _PROCESS_FIELDS.items()},
    },
    "required": ["action"],
    "additionalProperties": False,
}
```

Do not add conditional `if/then`, `dependentRequired`, `oneOf`, `anyOf`, or provider-specific schema branches. Runtime validation already owns those conditions.

- [ ] **Step 4: Update the old action-specific schema test without weakening runtime coverage**

Replace `test_process_schema_matches_action_specific_runtime_contracts` with a provider-surface test in which every documented complete action validates, while universally invalid calls do not:

```python
def test_process_schema_accepts_every_documented_complete_action() -> None:
    schema = compile_tool_schema(PROCESS_SCHEMA)
    valid = [
        {"action": "poll", "session_id": "proc_x", "cursor": 0, "yield_time_ms": 1_000},
        {"action": "wait", "session_id": "proc_x", "cursor": 4, "wait_time_ms": 60_000},
        {"action": "write", "session_id": "proc_x", "input": "yes", "eof": False},
        {"action": "write_raw", "session_id": "proc_x", "input": "yes\n", "eof": False},
        {"action": "resize", "session_id": "proc_x", "rows": 24, "cols": 80},
        {"action": "interrupt", "session_id": "proc_x", "yield_time_ms": 1_000},
        {"action": "terminate", "session_id": "proc_x", "yield_time_ms": 2_000},
        {"action": "kill", "session_id": "proc_x"},
        {"action": "list"},
    ]

    assert all(not schema.errors(arguments) for arguments in valid)
```

Update `test_process_write_explains_raw_input_and_line_submission` to read the one flat input description instead of traversing `parameters["oneOf"]`:

```python
input_description = definition.parameters["properties"]["input"]["description"]
assert "appends Enter" in input_description
assert "write_raw sends the text exactly" in input_description
```

- [ ] **Step 5: Run the schema and existing compatibility tests and verify GREEN**

```bash
uv run --no-sync python -m pytest -q tests/test_process_tools.py -k \
  'process_schema or argument_preparation or compatibility_fields or conflicting_aliases'
```

Expected: all selected tests pass; no preparation behavior changes.

- [ ] **Step 6: Checkpoint the task without committing**

```bash
git diff --check
git diff -- travis/coding_agent/tools/process.py tests/test_process_tools.py
git status --short
```

Confirm the two protected untracked documents are unchanged and unstaged.

---

### Task 2: Make Invalid Calls Explain the Exact Missing Action Shape

**Files:**
- Modify: `tests/test_process_tools.py`
- Modify: `travis/coding_agent/tools/process.py`

**Interfaces:**
- Consumes: the flat `PROCESS_SCHEMA`, `prepare_process_arguments()`, `_ACTION_FIELDS`, and `_validate_args()` from Task 1.
- Produces: `_PROCESS_ACTION_EXAMPLES: dict[str, str]` and focused `ValueError` messages for missing action-specific fields.
- Preserves: no inference of action, session ID, cursor, or input.

- [ ] **Step 1: Add failing validation-envelope and no-inference tests**

```python
def test_empty_process_call_reports_the_required_action_without_repair() -> None:
    tool = create_process_tool(None, None)
    call = ToolCall(id="empty-process", name="process", arguments={})

    assert tool.prepare_arguments({}) == {}
    with pytest.raises(ToolValidationError, match=r"process: missing required property 'action'"):
        validate_tool_arguments(tool, call)
    assert call.arguments == {}


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"action": "write", "session_id": "proc_x"}, r"write requires input.*\"action\":\"write\""),
        ({"action": "write_raw", "session_id": "proc_x"}, r"write_raw requires input.*\"action\":\"write_raw\""),
        ({"action": "resize", "session_id": "proc_x", "cols": 80}, r"resize requires rows.*\"rows\":24"),
        ({"action": "kill"}, r"kill requires session_id.*\"action\":\"kill\""),
    ],
)
def test_process_runtime_errors_include_the_relevant_action_shape(arguments, expected) -> None:
    definition = create_process_tool_definition(None, None)

    with pytest.raises(ValueError, match=expected):
        definition.execute("invalid-process", arguments)


def test_process_preparation_never_invents_identity_or_cursor() -> None:
    assert process_tool_module.prepare_process_arguments({"action": "write", "input": "yes"}) == {
        "action": "write",
        "input": "yes",
    }
    with pytest.raises(ValueError, match=r"cursor must be a nonnegative integer.*tool process"):
        process_tool_module.prepare_process_arguments(
            {"action": "wait", "session_id": "proc_x"}
        )
```

- [ ] **Step 2: Run the tests and verify RED**

```bash
uv run --no-sync python -m pytest -q tests/test_process_tools.py -k \
  'empty_process_call or relevant_action_shape or never_invents_identity_or_cursor'
```

Expected: the empty-call and no-inference controls pass once Task 1 is present, while the action-shape tests fail because current runtime errors omit complete examples.

- [ ] **Step 3: Add bounded action examples used only in error results**

Keep the existing generic wait/poll examples and add compact examples for the remaining required shapes:

```python
_PROCESS_ACTION_EXAMPLES = {
    "poll": PROCESS_POLL_EXAMPLE,
    "wait": PROCESS_WAIT_EXAMPLE,
    "write": '{"action":"write","session_id":"<id>","input":"<line>"}',
    "write_raw": '{"action":"write_raw","session_id":"<id>","input":"<exact-input>"}',
    "resize": '{"action":"resize","session_id":"<id>","rows":24,"cols":80}',
    "interrupt": '{"action":"interrupt","session_id":"<id>"}',
    "terminate": '{"action":"terminate","session_id":"<id>"}',
    "kill": '{"action":"kill","session_id":"<id>"}',
    "list": '{"action":"list"}',
}


def _missing_process_field(action: str, field: str) -> ValueError:
    return ValueError(
        f"{action} requires {field}; use tool process with {_PROCESS_ACTION_EXAMPLES[action]}"
    )
```

Use `_missing_process_field()` from `_require_string()` and the resize required-field branch. Retain the existing specific numeric/range and forbidden-field errors.

- [ ] **Step 4: Verify all strict runtime requirements remain enforced**

Extend `test_process_tool_validates_action_specific_arguments_and_hides_stdin` to cover:

```python
with pytest.raises(ValueError, match="wait requires cursor|cursor must be a nonnegative integer"):
    process.execute("p", {"action": "wait", "session_id": "proc_x"})
with pytest.raises(ValueError, match="list does not accept session_id"):
    process.execute("p", {"action": "list", "session_id": "proc_x"})
with pytest.raises(ValueError, match="write does not accept cursor"):
    process.execute(
        "p",
        {"action": "write", "session_id": "proc_x", "input": "secret", "cursor": 0},
    )
```

- [ ] **Step 5: Run the complete process-tool test module and verify GREEN**

```bash
uv run --no-sync python -m pytest -q tests/test_process_tools.py
```

- [ ] **Step 6: Checkpoint the task without committing**

```bash
git diff --check
git status --short
```

---

### Task 3: Give Running PTYs an Input-First Bash Handoff With Live Identifiers

**Files:**
- Modify: `tests/test_process_tools.py`
- Modify: `travis/coding_agent/tools/process.py`
- Modify: `travis/coding_agent/tools/bash.py`

**Interfaces:**
- Consumes: `ProcessSnapshot.session_id`, `.next_cursor`, `.tty`, and `.suggested_poll_delay_ms`.
- Produces: `format_process_poll_instruction(session_id, cursor, yield_time_ms=1000) -> str`, `format_process_write_instruction(session_id) -> str`, and `format_process_bash_handoff(snapshot, *, input_open: bool) -> str`.
- Preserves: `format_process_wait_instruction()` and all structured `AgentToolResult.details` fields.

- [ ] **Step 1: Add a failing single-line PTY handoff regression**

```python
def test_managed_bash_pty_handoff_leads_with_live_write_then_returned_wait(managed_tools) -> None:
    _service, _owner, bash, process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command(
                "import time; value=input('READY\\n'); time.sleep(.2); print('HANDOFF-OK:' + value, flush=True)"
            ),
            "stdin": "open",
            "tty": True,
            "yield_time_ms": 0,
        },
    )
    session_id = started.details["sessionId"]
    write_shape = json.dumps(
        {"action": "write", "session_id": session_id, "input": "<line>"},
        separators=(",", ":"),
    )

    assert started.details["status"] == "running"
    assert write_shape in text(started)
    assert "After that write, use the exact wait call returned by its result" in text(started)
    assert '"session_id":"<id>"' not in text(started)

    written = process.execute(
        "write",
        {
            "action": "write",
            "session_id": session_id,
            "input": "LIVE-ID",
            "yield_time_ms": 0,
        },
    )
    wait_shape = json.dumps(
        {
            "action": "wait",
            "session_id": session_id,
            "cursor": written.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
        separators=(",", ":"),
    )
    assert wait_shape in text(written)
```

- [ ] **Step 2: Add a failing non-PTY wait-first regression**

```python
def test_managed_bash_noninteractive_handoff_leads_with_live_wait(managed_tools) -> None:
    _service, _owner, bash, _process = managed_tools
    started = bash.execute(
        "bash",
        {"command": python_command("import time; time.sleep(3); print('DONE')"), "yield_time_ms": 0},
    )
    expected_wait = json.dumps(
        {
            "action": "wait",
            "session_id": started.details["sessionId"],
            "cursor": started.details["nextCursor"],
            "wait_time_ms": 60_000,
        },
        separators=(",", ":"),
    )

    assert expected_wait in text(started)
    assert '"action":"write"' not in text(started)
```

Add a second input-first control for an open pipe:

```python
def test_managed_bash_open_pipe_handoff_leads_with_live_write(managed_tools) -> None:
    _service, _owner, bash, _process = managed_tools
    started = bash.execute(
        "bash",
        {
            "command": python_command("import sys; print('READY', flush=True); print(sys.stdin.readline())"),
            "stdin": "open",
            "yield_time_ms": 0,
        },
    )
    write_shape = json.dumps(
        {
            "action": "write",
            "session_id": started.details["sessionId"],
            "input": "<line>",
        },
        separators=(",", ":"),
    )

    assert started.details["tty"] is False
    assert write_shape in text(started)
    assert "Pipe stdin is open" in text(started)
```

- [ ] **Step 3: Run both tests and verify RED**

```bash
uv run --no-sync python -m pytest -q tests/test_process_tools.py -k \
  'pty_handoff_leads_with_live_write or open_pipe_handoff_leads_with_live_write or noninteractive_handoff_leads_with_live_wait'
```

Expected: the PTY test fails because the current Bash footer leads with wait and provides no valid write call; the non-PTY test remains a control.

- [ ] **Step 4: Add shared compact call formatters in `process.py`**

```python
def _compact_process_call(arguments: dict[str, object]) -> str:
    return json.dumps(arguments, separators=(",", ":"))


def format_process_poll_instruction(session_id: str, cursor: int, yield_time_ms: int = 1_000) -> str:
    arguments = _compact_process_call(
        {
            "action": "poll",
            "session_id": session_id,
            "cursor": cursor,
            "yield_time_ms": yield_time_ms,
        }
    )
    return f"For a quick status or interaction check, call the process tool with {arguments}."


def format_process_write_instruction(session_id: str) -> str:
    arguments = _compact_process_call(
        {"action": "write", "session_id": session_id, "input": "<line>"}
    )
    return f"To submit one line, call the process tool with {arguments}."


def format_process_bash_handoff(snapshot: ProcessSnapshot, *, input_open: bool) -> str:
    if input_open:
        input_kind = "PTY input" if snapshot.tty else "Pipe stdin"
        return (
            f"{input_kind} is open. {format_process_write_instruction(snapshot.session_id)} "
            "After that write, use the exact wait call returned by its result and its nextCursor. "
            f"{format_process_poll_instruction(snapshot.session_id, snapshot.next_cursor, snapshot.suggested_poll_delay_ms)}"
        )
    return (
        f"{format_process_wait_instruction(snapshot.session_id, snapshot.next_cursor)} "
        f"{format_process_poll_instruction(snapshot.session_id, snapshot.next_cursor, snapshot.suggested_poll_delay_ms)}"
    )
```

Refactor `format_process_wait_instruction()` to use `_compact_process_call()` without changing its returned wording. Export the three new public helper names in `__all__` because Bash imports them across the tool-module boundary.

- [ ] **Step 5: Use the handoff helper from managed Bash**

Pass `input_open=tty or stdin_mode == "open"` from `_execute_managed_bash()` into `_managed_bash_result()`. Add an `input_open: bool` parameter to that private helper, then replace the unconditional wait-first footer with:

```python
footer = (
    f"Process {snapshot.session_id} is {snapshot.state.value}; command continues in the background. "
    f"{format_process_bash_handoff(snapshot, input_open=input_open)}"
)
```

Import `format_process_bash_handoff` instead of constructing process JSON in `bash.py`. Do not alter `details`, output truncation, terminal-state handling, timeouts, or exceptions.

- [ ] **Step 6: Put an exact live poll shape in running process results**

Update `_snapshot_footer()` so a running result always includes the existing exact wait call, and `include_poll_hint=True` adds `format_process_poll_instruction()` rather than only a prose delay. A result returned by the `wait` action must still omit the poll hint.

Update `test_running_process_results_expose_suggested_poll_delay` to assert the exact live poll JSON containing the result's `sessionId`, `nextCursor`, and `suggestedPollDelayMs`. Retain its assertion that a running result returned by `wait` contains no poll guidance.

- [ ] **Step 7: Run managed PTY, write, wait, poll, cursor, and subagent-process regressions**

```bash
uv run --no-sync python -m pytest -q tests/test_process_tools.py -k \
  'handoff or running_process_results or write_submits or write_raw or internal_child_can_send or cursor'
```

Expected: all selected tests pass; every dynamic JSON example contains the actual process ID and currently valid cursor.

- [ ] **Step 8: Checkpoint the task without committing**

```bash
git diff --check
git diff -- travis/coding_agent/tools/process.py travis/coding_agent/tools/bash.py tests/test_process_tools.py
```

---

### Task 4: Compact Static Process Guidance and Correct Bash/PTY/tmux Routing

**Files:**
- Modify: `tests/test_process_tools.py`
- Modify: `tests/test_coding_resources_and_services.py`
- Modify: `tests/test_coding_tools_and_subagents.py`
- Modify: `travis/coding_agent/tools/process.py`
- Modify: `travis/coding_agent/system_prompt.py`

**Interfaces:**
- Consumes: dynamic handoff formatters from Task 3 and `ToolDefinition.prompt_guidelines` assembly in `SessionToolController._build_system_prompt()`.
- Produces: compact, tool-dependent routing and at most seven process guidelines containing only durable invariants.
- Preserves: evidence discipline, tmux durability, active-tool filtering, and project/skill context injection.

- [ ] **Step 1: Replace prompt-content assertions with failing quality and budget regressions**

Add:

```python
def test_process_prompt_metadata_is_compact_and_contains_no_placeholder_calls(managed_tools) -> None:
    service, owner, _bash, _process = managed_tools
    definition = create_process_tool_definition(service, owner)
    metadata = "\n".join([definition.prompt_snippet or "", *definition.prompt_guidelines])

    assert len(definition.prompt_guidelines) <= 7
    assert len(metadata) <= 1_050
    assert "<nextCursor>" not in metadata
    assert '"session_id":"<id>"' not in metadata
    assert "exact nextCursor" in metadata
    assert "write_raw" in metadata
    assert "actual execution deadline" in metadata


def test_managed_process_routing_separates_background_work_from_pty_allocation(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / ".processes")
    owner = ProcessOwner("app", str(tmp_path.resolve()), "agent")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        process_service=service,
        process_owner=owner,
        agent_dir=str(tmp_path / "agent"),
    )
    try:
        assert "Use managed `bash` plus `process` for commands that remain running" in session.system_prompt
        assert "Set `tty=true` only for terminal interaction" in session.system_prompt
        assert "PTY plus `process` when a command requires interactive input or incremental output" not in session.system_prompt
        assert '"session_id":"<id>"' not in session.system_prompt
        assert "Use `tmux` for servers, watchers, REPLs" in session.system_prompt
    finally:
        session.shutdown()
        service.close()
```

- [ ] **Step 2: Run the new tests and verify RED**

```bash
uv run --no-sync python -m pytest -q tests/test_process_tools.py \
  -k 'prompt_metadata_is_compact or routing_separates_background_work'
```

Expected: failure because the current process definition has 13 guidelines, contains placeholder JSON, and conflates PTY allocation with incremental output.

- [ ] **Step 3: Consolidate the 13 process guidelines into durable invariants**

Use this bounded set in `create_process_tool_definition()`:

```python
prompt_guidelines=[
    "Use the exact nextCursor returned by bash/process so output is neither repeated nor skipped.",
    "Use wait when a command result is required; use poll only for interactive input, quick status, or intentionally incremental output. A wait observation never changes bash.timeout or kills the command.",
    "If wait returns running, wait again from that result's exact nextCursor.",
    "Use write to submit one line; use write_raw for exact bytes, control sequences, partial input, or PTY Ctrl-D. eof is valid only for pipe stdin.",
    "Continue independent work before waiting, but do not repeat unchanged file reads around process checks.",
    "Leave a process detached only for a requested server/watcher or when its result is not required.",
    "Set bash.timeout only when an actual execution deadline is intended.",
],
```

Keep exact JSON examples out of the permanent system prompt; Tasks 2 and 3 provide them only in validation errors or live tool results.

- [ ] **Step 4: Correct execution routing in `system_prompt.py`**

Replace the process routing sentence with:

```python
guidance.append(
    "Use managed `bash` plus `process` for commands that remain running. "
    "Set `tty=true` only for terminal interaction; ordinary long-running commands should remain non-PTY."
)
```

Retain finite Bash routing and tmux durability routing. Do not add more process policy to `_ENGINEERING_GUIDANCE`.

- [ ] **Step 5: Update existing exact prompt metadata tests**

Update only assertions made obsolete by the compact policy:

- `test_builtin_tool_definitions_match_travis234_prompt_metadata` must expect the seven process guidelines when process metadata is examined.
- `test_agent_session_prompt_keeps_required_managed_process_work_pending` must assert the durable wait/poll/cursor semantics and absence of placeholder JSON.
- `test_agent_session_exposes_only_core_subagent_workflow_by_default` must retain finite Bash, tmux, evidence, and non-fabrication assertions; its no-process session must still omit managed-process routing.
- `test_managed_bash_warns_models_not_to_infer_execution_deadlines` must keep the Bash timeout and interactive-launch assertions.

- [ ] **Step 6: Run all prompt and process tests and verify GREEN**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_process_tools.py \
  tests/test_coding_resources_and_services.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_reference_runtime_contract.py
```

- [ ] **Step 7: Prove context accounting remains additive**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_context_estimate.py \
  tests/test_reference_runtime_contract.py -k 'context_estimate or default_system_prompt'
```

Expected: the system/tool token estimates become smaller because their strings are smaller, while `tokens == system_tokens + tool_tokens + message_tokens` and provider-real/trailing accounting remain unchanged.

- [ ] **Step 8: Checkpoint the task without committing**

```bash
git diff --check
git status --short
```

---

### Task 5: Document the Process Handoff Without Exposing Internal Recovery Policy

**Files:**
- Modify: `README.md`
- Test: `tests/test_process_tools.py`

**Interfaces:**
- Consumes: final user-visible behavior from Tasks 1-4.
- Produces: concise user documentation for finite Bash, managed process, PTY input, and tmux durability.

- [ ] **Step 1: Add a failing documentation contract**

Add a repository README assertion near other process documentation tests:

```python
def test_readme_documents_action_based_process_handoff() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert '"action":"write"' in readme
    assert '"action":"wait"' in readme
    assert "real session ID" in readme
    assert "nextCursor returned by the write" in readme
    assert "PTY only for terminal interaction" in readme
```

- [ ] **Step 2: Run the documentation test and verify RED**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_process_tools.py::test_readme_documents_action_based_process_handoff
```

- [ ] **Step 3: Rewrite only the managed-process usage paragraph**

Document these points without promising containment or retries:

- Finite commands use Bash and need no process follow-up.
- A still-running noninteractive command returns a wait call containing the real session ID and cursor.
- A running PTY returns an input-first `{"action":"write",...}` example containing the real session ID; its write result returns the `{"action":"wait",...}` call with the write's exact `nextCursor`.
- PTY is only for terminal interaction; long duration alone does not require PTY.
- `process` uses one `action` field rather than namespaced tool names such as `process.wait`.
- tmux remains the durable across-turn choice for servers, watchers, REPLs, and long builds.

Keep the existing ownership, output budget, cursor recovery, and restart limitations.

- [ ] **Step 4: Run the focused documentation and process suites**

```bash
uv run --no-sync python -m pytest -q tests/test_process_tools.py tests/test_brand_contract.py
git diff --check
```

---

### Task 6: Run Automated Repository, Package, Parity, and Container Gates

**Files:**
- No planned production modifications.
- Any newly exposed defect requires a new failing regression and a focused red-green cycle before continuing.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: exact pass counts and artifact/container evidence for the current worktree without publishing.

- [ ] **Step 1: Audit scope before running broad gates**

```bash
git status --short
git diff --check
git diff --name-only
```

Confirm the process repair did not touch the generic loop, AgentHarness, session/compaction modules, protected documents, version files, or release metadata. Account separately for the pre-existing runtime-hardening dirty files recorded before this plan.

- [ ] **Step 2: Run the focused combined Python suite**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_process_tools.py \
  tests/test_process_service.py \
  tests/test_process_local.py \
  tests/test_process_completions.py \
  tests/test_coding_resources_and_services.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_reference_runtime_contract.py \
  tests/test_context_estimate.py \
  tests/test_agent_loop.py
```

- [ ] **Step 3: Run the complete root and adapter Python suites**

```bash
uv run --no-sync python -m pytest -q
uv run --project packages/travis234-mcp-adapter pytest \
  packages/travis234-mcp-adapter/tests -q
```

Record exact pass counts and elapsed times; do not reuse earlier release counts.

- [ ] **Step 4: Run npm launcher and package dry-run tests**

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

- [ ] **Step 5: Run Pi/Hermes acceptance parity**

```bash
uv run --no-sync python scripts/verify_acceptance.py --parity-json
```

Expected: all pinned Hermes behaviors and all intended Pi behaviors pass, with only the already documented Travis safety divergences.

- [ ] **Step 6: Build and validate root and adapter distributions in isolated output directories**

Create a dedicated temporary directory with `mktemp -d`, store its explicit path in `TRAVIS_PROCESS_BUILD_DIR`, and run:

```bash
uv build --out-dir "$TRAVIS_PROCESS_BUILD_DIR/root"
uv build --project packages/travis234-mcp-adapter \
  --out-dir "$TRAVIS_PROCESS_BUILD_DIR/adapter"
uvx --from twine twine check \
  "$TRAVIS_PROCESS_BUILD_DIR"/root/*.whl \
  "$TRAVIS_PROCESS_BUILD_DIR"/root/*.tar.gz \
  "$TRAVIS_PROCESS_BUILD_DIR"/adapter/*.whl \
  "$TRAVIS_PROCESS_BUILD_DIR"/adapter/*.tar.gz
```

Install the exact root wheel and adapter wheel together into a clean Python 3.13 virtual environment under the same temporary directory. Verify `import travis`, `import travis234_mcp_adapter`, `travis234 --version`, and `travis234 --help` without using the source tree through `PYTHONPATH`.

- [ ] **Step 7: Build and run the existing unprivileged release-container smoke**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:process-tool-quality .
python3 evals/container_smoke.py --image travis234:process-tool-quality
```

Expected: image builds, runs as `travis`, exposes the installed CLI, passes print/JSON/RPC/TUI faux turns, compaction and managed-process cleanup, and exits without provider credentials.

- [ ] **Step 8: Re-audit the worktree**

```bash
git diff --check
git status --short
```

Do not remove build outputs or temporary directories with a broad or unresolved path. Remove only the explicit temporary path after validating it, and report what was removed.

---

### Task 7: Run One Continuous Twelve-Prompt Minimax M3 Background TUI Qualification

**Files:**
- Create during approved execution: `docs/verification/main-process-tool-quality-twelve-prompt-tui.md`

**Interfaces:**
- Consumes: the exact root wheel from Task 6, the existing root `.env` by path, OpenRouter `minimax/minimax-m3`, and an isolated temporary fixture/state directory.
- Produces: a secret-redacted event trace, authorized conversation log, twelve result markers, per-turn context-envelope measurements, and external cleanup evidence.

- [ ] **Step 1: Build the isolated fixture**

Create a temporary git repository containing:

- `README.md` with literal `PROCESS-QUALITY-FIXTURE` and `Python`.
- `worker.py` with small deterministic modes for delayed output, single-line PTY input, two-step PTY input, pipe stdin, incremental output, terminal-size reporting, and signal handling.
- `test_worker.py` with deterministic tests for the fixture only.
- No credential files, symlinks outside the fixture, or alternate Travis state directory inside the repository.

Run the fixture tests externally before launching Travis234.

- [ ] **Step 2: Install and launch the exact wheel in one attached background PTY**

Use a clean Python 3.13 environment and isolated `TRAVIS234_CODING_AGENT_DIR`. Create the qualification root with `mktemp -d /tmp/travis234-process-quality.XXXXXX`, then define these paths from that resolved root:

```bash
TRAVIS_PROCESS_QUAL_DIR="$(mktemp -d /tmp/travis234-process-quality.XXXXXX)"
test -d "$TRAVIS_PROCESS_QUAL_DIR"
TRAVIS_PROCESS_FIXTURE="$TRAVIS_PROCESS_QUAL_DIR/fixture"
TRAVIS_PROCESS_AGENT_DIR="$TRAVIS_PROCESS_QUAL_DIR/agent"
TRAVIS_PROCESS_EVENTS="$TRAVIS_PROCESS_QUAL_DIR/events.jsonl"
TRAVIS_PROCESS_CONVERSATION="$TRAVIS_PROCESS_QUAL_DIR/conversation.jsonl"
TRAVIS_PROCESS_VENV="$TRAVIS_PROCESS_QUAL_DIR/venv"
```

Install the exact wheel produced in Task 6 into `TRAVIS_PROCESS_VENV`, then launch its console entry point—not `python -m travis.cli` and not the eval runner—with:

```bash
TRAVIS234_CODING_AGENT_DIR="$TRAVIS_PROCESS_AGENT_DIR" \
"$TRAVIS_PROCESS_VENV/bin/travis234" \
  --cwd "$TRAVIS_PROCESS_FIXTURE" \
  --dotenv /Users/htooayelwin/lewis/travis234/.env \
  --provider openrouter \
  --model minimax/minimax-m3 \
  --thinking medium \
  --approve \
  --no-session \
  --event-trace "$TRAVIS_PROCESS_EVENTS" \
  --conversation-log "$TRAVIS_PROCESS_CONVERSATION"
```

Never print the dotenv file or inherited provider values.

- [ ] **Step 3: Send these twelve prompts one at a time after the TUI returns to idle**

1. `Read README.md and return exactly PROCESS-QUALITY-1-PASS followed by the project name and language.`
2. `Run the fixture tests as a finite command, report the exact pass count and exit code, then end PROCESS-QUALITY-2-PASS.`
3. `Run worker.py in delayed-output mode so it remains active briefly, continue useful inspection while it runs, collect its terminal result, and end PROCESS-QUALITY-3-PASS.`
4. `Start worker.py in its interactive single-line terminal mode. It prints READY, accepts QUALITY-PTY, prints SINGLE:QUALITY-PTY, and exits. Complete that interaction and end PROCESS-QUALITY-4-PASS.`
5. `Repeat the interactive terminal check with the value PROMPT-FIVE-RECOVERY, collect its terminal exit, and end PROCESS-QUALITY-5-PASS.`
6. `Run worker.py in two-step terminal mode. Respond FIRST-A to its first prompt and SECOND-B to its second prompt, collect DOUBLE:FIRST-A:SECOND-B, and end PROCESS-QUALITY-6-PASS.`
7. `Run worker.py in pipe-input mode without allocating a terminal, send PIPE-QUALITY through its open stdin, collect PIPE:PIPE-QUALITY, and end PROCESS-QUALITY-7-PASS.`
8. `Run worker.py in incremental-output mode, inspect one incremental update without repeating earlier output, then wait for terminal state and end PROCESS-QUALITY-8-PASS.`
9. `Run worker.py in slow mode. Use a short observation that returns while it is still running, then continue from the returned cursor until it exits, ending PROCESS-QUALITY-9-PASS.`
10. `Run worker.py in terminal-size mode with a nondefault terminal size, verify the observed rows and columns, collect terminal state, and end PROCESS-QUALITY-10-PASS.`
11. `Start the signal-handling mode, interrupt it through the managed process control, report its terminal state, and end PROCESS-QUALITY-11-PASS.`
12. `Final audit: rerun the fixture tests, list managed processes and tmux sessions, clean up fixture-owned leftovers, reread README.md, and end exactly PROCESS-QUALITY-12-PASS.`

Prompts intentionally describe outcomes rather than JSON argument shapes. The runtime's schema, static policy, and dynamic handoff must guide the model.

- [ ] **Step 4: Enforce blocking process-call criteria**

From the event trace and conversation log, require:

- Twelve successful turn markers in order.
- Zero `process` calls with `{}`.
- Zero `process` calls missing `action`.
- Zero `invalid_arguments` outcomes for `process`.
- Every PTY input sequence is Bash with `tty=true`, then `process` write/write_raw, then wait using the exact `nextCursor` from the preceding result.
- Noninteractive long work does not allocate a PTY merely because it is long-running.
- No repeated output caused by cursor reuse.
- No provider/model loop, repeated identical failed call, artificial halt, or containment message.
- Prompt 6 or later still performs normal read/Bash work, proving no context poisoning after multiple process turns.

Any process invalid-argument call fails the qualification even if the model later recovers. A provider outage or unrelated environment failure may be rerun and must be classified separately.

- [ ] **Step 5: Record context-envelope hygiene for every prompt**

Record footer/provider evidence for:

- Prompt tokens or percentage after each turn.
- Whether the measurement is provider-real or estimated-trailing.
- Compaction count and trigger state.
- Static system-prompt and tool-schema estimates from the installed wheel.
- Confirmation that no containment message, counter, synthetic user turn, or transcript rewrite was added.

Expected: ordinary provider usage progression, no unexpected compaction at this small history, and lower static process schema/prompt estimates than the recorded baseline.

- [ ] **Step 6: Exit and independently verify cleanup**

After `/exit`, verify outside Travis234:

- Fixture tests still pass.
- No fixture-owned worker remains.
- No managed process remains active.
- No Travis234 tmux session for the fixture remains.
- Terminal cursor, bracketed paste, and mouse state are restored.
- Event/conversation logs contain no credential values.

- [ ] **Step 7: Write the verification record**

Create `docs/verification/main-process-tool-quality-twelve-prompt-tui.md` with:

- Date, exact local commit/worktree identity, wheel filename, Python version, model/provider, thinking level, and launch shape.
- Fixture contract and the twelve exact prompts.
- A PASS/FAIL matrix with concrete tool/result evidence.
- Counts of provider turns, process calls, invalid calls, tool errors, compactions, and cleanup events.
- Context-envelope measurements before prompt 5, after prompt 5, and after prompt 6, plus final values.
- Any model, provider, runtime, tool, or environment anomaly classified separately.
- No secret value, dotenv content, or credential-bearing request body.

- [ ] **Step 8: Perform the final no-release audit**

```bash
git diff --check
git status --short
git diff --stat
```

Do not commit, push, publish, or release. Report exact verification results and wait for separate user direction.

---

## Completion Criteria

- The provider-facing process schema is a flat root object with one required enum action and no unions or const branches.
- The flat compact schema is at most 1,600 compact JSON characters.
- `_validate_args()` still enforces every action-specific required/forbidden field and range after existing compatibility preparation.
- `{}` is not repaired; it fails with a direct missing-action error.
- Running PTYs receive an input-first live write example with the actual session ID, and the write result supplies an exact live wait call using its returned `nextCursor`.
- Running non-PTY commands receive a wait-first live example.
- Static process guidance contains no placeholder process JSON and no more than seven durable guidelines.
- PTY routing is limited to terminal interaction; long duration alone does not imply PTY.
- No generic-loop, containment, AgentHarness, session, compaction, accounting, persistence, or lifecycle change occurs.
- Focused/full Python, adapter, npm, parity, builds, Twine, exact-wheel, and container gates pass with fresh counts.
- The twelve-prompt Minimax M3 installed-wheel TUI run has zero invalid process calls and leaves no owned process behind.

## Execution Gate

This plan is ready for inline execution, but execution must not begin until the user explicitly approves it. The separately planned system-prompt deduplication may be executed after this plan passes its automated gates; it must not be mixed into a process regression fix without its own red-green cycle.
