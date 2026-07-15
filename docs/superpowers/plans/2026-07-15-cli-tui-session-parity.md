# CLI, TUI, and Session Behavioral Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Pi-equivalent automation, input, execution-control, and session workflows without creating alternate agent loops.

**Status (2026-07-15):** Complete. The full Phase 4 compatibility gate passed 230 tests.

**Architecture:** All modes construct the same `CodingApp` and `AgentSession`. Print, JSON, and RPC are output/input transports around session events. Session commands mutate the existing JSONL tree and SQLite index through current owners.

**Tech Stack:** Python 3.13, argparse, JSON Lines, stdin/stdout RPC framing, existing TUI components, pytest golden streams.

## Global Constraints

- Non-interactive modes are fail-closed for project trust.
- JSON and RPC stdout contains machine frames only; diagnostics go to stderr.
- Tool allow/deny changes the active tool set before system prompt and envelope estimation.
- Existing session files load without migration.
- Preserve bounded tool execution and run leases.
- Do not invoke Git at all, including read-only status or diff commands.

---

### Task 1: Print and JSON automation modes

**Files:**
- Create: `travis/coding_agent/automation.py`
- Modify: `travis/cli.py`
- Modify: `travis/coding_agent/session_events.py`
- Create: `tests/test_automation_modes.py`

**Interfaces:**
- Produces: `run_print_mode(app: CodingApp, prompt: str, output: TextIO) -> int`
- Produces: `run_json_mode(app: CodingApp, prompt: str, output: TextIO) -> int`

- [x] **Step 1: Write failing print-mode tests**

```python
def test_print_mode_outputs_only_final_text(faux_app, io_pair) -> None:
    code = run_print_mode(faux_app, "hello", io_pair.stdout)
    assert code == 0
    assert io_pair.stdout.getvalue() == "final answer\n"
```

- [x] **Step 2: Write JSON golden-stream tests**

```python
def test_json_mode_emits_ordered_machine_events(faux_app, io_pair) -> None:
    code = run_json_mode(faux_app, "hello", io_pair.stdout)
    frames = [json.loads(line) for line in io_pair.stdout.getvalue().splitlines()]
    assert code == 0
    assert [frame["type"] for frame in frames] == ["session", "message_start", "message_end", "result"]
    assert all("\x1b" not in line for line in io_pair.stdout.getvalue().splitlines())
```

- [x] **Step 3: Implement mode-neutral event serialization**

Add a serializer that converts dataclasses and typed message blocks to stable camelCase JSON without credentials, raw provider headers, or unserializable objects. Include schema version `1` on the first frame.

- [x] **Step 4: Add mutually exclusive mode arguments**

Support `--mode interactive|print|json|rpc`, keep `--plain` as a documented interactive compatibility alias during one release, and make a positional prompt select print mode only when the user explicitly requests non-interactive behavior. Preserve current CLI tests through an intentional compatibility mapping.

- [x] **Step 5: Run automation tests**

```bash
.venv/bin/python -m pytest -q tests/test_automation_modes.py tests/test_cli.py -k 'mode or prompt or json or print'
```

Expected: all selected tests pass.

### Task 2: RPC transport

**Files:**
- Create: `travis/coding_agent/rpc.py`
- Modify: `travis/cli.py`
- Create: `tests/test_rpc_mode.py`

**Interfaces:**
- Produces: `RpcServer(app, input: TextIO, output: TextIO).run() -> int`
- Supports: `prompt`, `continue`, `abort`, `get_state`, `set_model`, `set_thinking`, `compact`, `close`

- [x] **Step 1: Write framed request/response tests**

```python
def test_rpc_prompt_correlates_events_and_result(faux_app, io_pair) -> None:
    io_pair.stdin.write('{"id":"1","method":"prompt","params":{"text":"hello"}}\n')
    io_pair.stdin.seek(0)
    assert RpcServer(faux_app, io_pair.stdin, io_pair.stdout).run() == 0
    frames = [json.loads(line) for line in io_pair.stdout.getvalue().splitlines()]
    assert frames[-1] == {"id": "1", "result": {"stopReason": "stop"}}
```

- [x] **Step 2: Define deterministic RPC errors**

Use JSON objects with `id`, `error.code`, and `error.message`. Define codes for parse error, invalid request, unknown method, invalid params, busy session, and internal error. Never include stack traces or secrets on stdout.

- [x] **Step 3: Implement sequential command dispatch with abort support**

One request may own the active turn. `abort` may arrive while it runs and routes to the existing run lease/abort controller. All other mutating requests return `busy_session` until the turn settles.

- [x] **Step 4: Run RPC tests**

```bash
.venv/bin/python -m pytest -q tests/test_rpc_mode.py tests/test_abort_context.py
```

Expected: all tests pass, including abort and malformed-frame cases.

### Task 3: Tool controls, offline startup, and explicit resources

**Files:**
- Modify: `travis/cli.py`
- Modify: `travis/coding_agent/session_tooling.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Create: `tests/test_cli_runtime_controls.py`

**Interfaces:**
- Produces: repeatable `--tools`, `--exclude-tools`, `--extension`, `--skill`, `--prompt-template`, `--theme`; boolean `--no-tools`
- Produces: `--offline`

- [x] **Step 1: Write tool-set precedence tests**

Test default tools, an allowlist, denylist subtraction, unknown names, and explicit extension tools. Define precedence as: construct defaults and extensions, apply allowlist if present, then apply denylist.

- [x] **Step 2: Apply controls before prompt construction**

Pass selected tool names into session creation so system prompt, tool schemas, extension context, and canonical envelope all see the same set. Unknown names are CLI errors before a provider call.

- [x] **Step 3: Implement offline startup**

`--offline` disables live model-catalog refresh, package missing-source installation, update checks, and OAuth refresh requiring network. Cached models, stored credentials, local resources, and faux providers remain usable.

- [x] **Step 4: Add explicit resource paths**

Explicit CLI paths are operator-authorized temporary resources and may load before project trust. Preserve source metadata as `scope=temporary`, validate existence, and report diagnostics without searching alternative paths.

- [x] **Step 5: Run runtime-control tests**

```bash
.venv/bin/python -m pytest -q tests/test_cli_runtime_controls.py tests/test_cli.py -k 'tool or offline or extension or skill or theme'
```

Expected: all selected tests pass.

### Task 4: `@file` and image input expansion

**Files:**
- Create: `travis/coding_agent/input_expansion.py`
- Modify: `travis/coding_agent/session_turns.py`
- Modify: `travis/cli.py`
- Modify: `travis/tui/interactive_turn_controller.py`
- Create: `tests/test_input_expansion.py`

**Interfaces:**
- Produces: `expand_user_input(text: str, *, cwd: str, images: Sequence[str]) -> ExpandedInput`
- Produces: `ExpandedInput(text: str, content: tuple[TextContent | ImageContent, ...], referenced_paths: tuple[str, ...])`

- [x] **Step 1: Write quoted-path and escape tests**

Test `@README.md`, `@"path with spaces.txt"`, escaped `\@literal`, nonexistent paths, directories, binary files, and paths outside cwd. Explicit absolute paths are allowed only when supplied directly by the operator.

- [x] **Step 2: Implement bounded file inclusion**

Use the existing read-tool truncation limits and path rendering. Include a header naming the resolved file, text content, and truncation notice. Do not recursively include references found inside files.

- [x] **Step 3: Implement image arguments**

Accept common image MIME types, create `ImageContent` blocks, preserve filenames for display, and reject unsupported or oversized files before provider submission. Respect model image capability.

- [x] **Step 4: Run input tests**

```bash
.venv/bin/python -m pytest -q tests/test_input_expansion.py tests/test_tui_terminal_and_input.py -k 'file or image'
```

Expected: all selected tests pass.

### Task 5: Session name, fork, clone, tree, and switch parity

**Files:**
- Modify: `travis/coding_agent/session_store.py`
- Modify: `travis/coding_agent/session_catalog.py`
- Modify: `travis/coding_agent/session_commands.py`
- Modify: `travis/coding_agent/agent_session_runtime.py`
- Modify: `travis/tui/interactive_session_commands.py`
- Create: `tests/test_session_parity.py`

**Interfaces:**
- Produces: `rename_session`, `fork_session`, `clone_session`, `session_tree`, `switch_session`
- Preserves: parent IDs, compaction boundaries, labels, model/thinking changes, SQLite index consistency

- [x] **Step 1: Write session tree fixtures**

Create a branched JSONL session with messages, compaction, labels, model/thinking changes, and custom entries. Assert tree ordering, active leaf, and summary metadata.

- [x] **Step 2: Implement durable naming**

Reuse `append_session_info()`, update the SQLite index transactionally, emit `session_info_changed`, and reflect the name in list/resume selectors.

- [x] **Step 3: Implement fork and clone semantics**

- Fork creates a new session file rooted at a selected branch point, preserves relevant typed entries, records parent session/path metadata, and switches the active runtime.
- Clone is Pi's current-leaf convenience operation: it invokes the same fork primitive with `position="at"`, preserves the selected branch's typed entry IDs, records `parentSession`, and switches to the new file.

Neither operation mutates the source session.

- [x] **Step 4: Implement tree and switch commands**

Tree renders entry type, label/name, branch ancestry, and active leaf. Switch runs before-switch extension hooks, checks cwd/trust for the target session, releases the old session lock, and binds the new session through `AgentSessionRuntime`.

- [x] **Step 5: Implement import/export/copy/share boundary**

Import validates JSONL v3 before placement. Export preserves existing HTML and adds raw JSONL copy. Clipboard and share operations are optional platform adapters with clear unavailable diagnostics; they never upload without an explicit command.

- [x] **Step 6: Run session tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_session_parity.py \
  tests/test_session_commands.py \
  tests/test_session_index.py \
  tests/test_coding_persistence_and_compaction.py -k session
```

Expected: all selected tests pass and source sessions remain byte-identical after fork/clone tests.

### Task 6: TUI commands and key behavior

**Files:**
- Modify: `travis/tui/interactive_command_dispatcher.py`
- Modify: `travis/tui/interactive_session_commands.py`
- Modify: `travis/tui/interactive_extensions.py`
- Modify: `travis/tui/interactive_view.py`
- Modify: `travis/tui/keybindings.py`
- Test: `tests/test_tui_commands_and_extensions.py`
- Test: `tests/test_tui_terminal_and_input.py`

**Interfaces:**
- Consumes: trust, package, resource, and session owners
- Produces: discoverable `/trust`, `/name`, `/fork`, `/clone`, `/tree`, `/theme`, and package/resource commands

- [x] **Step 1: Add command-registration characterization tests**

Assert each command appears once with its actual handler and help text. Validate busy-turn and busy-compaction behavior.

- [x] **Step 2: Wire commands to owners**

The TUI does not edit session or trust files directly. It collects a selection, calls the corresponding owner, and renders structured success/error results.

- [x] **Step 3: Add supported Pi hotkeys**

Map only behaviors supported reliably by the current terminal framework. Preserve existing Travis process and interrupt bindings. Detect conflicts at startup and show them in keybinding help rather than silently replacing commands.

- [x] **Step 4: Run TUI tests**

```bash
.venv/bin/python -m pytest -q tests/test_tui_commands_and_extensions.py tests/test_tui_terminal_and_input.py
```

Expected: all selected tests pass.

### Task 7: Full Phase 4 verification

**Files:**
- Modify: `README.md`
- Modify: `docs/verification/acceptance-matrix.md`
- Test: `tests/test_automation_modes.py`

**Interfaces:**
- Consumes: complete Phase 4 behavior
- Produces: mode and session acceptance evidence

- [x] **Step 1: Add golden automation scenarios**

Run the same faux-provider task in print, JSON, RPC, and TUI modes. Assert identical final session messages and mode-appropriate output framing.

- [x] **Step 2: Document commands and compatibility aliases**

Document automation modes, trust behavior, tools, resources, input expansion, and session controls. Mark the planned removal version for any retained alias.

- [x] **Step 3: Run the Phase 4 gate**

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli.py \
  tests/test_automation_modes.py \
  tests/test_rpc_mode.py \
  tests/test_cli_runtime_controls.py \
  tests/test_input_expansion.py \
  tests/test_session_parity.py \
  tests/test_tui_commands_and_extensions.py \
  tests/test_tui_terminal_and_input.py
```

Expected: all selected tests pass.

Recorded result: 230 passed in 16.10 seconds.

- [x] **Step 4: Review checkpoint without Git operations**

Inspect the touched files directly, run whitespace/syntax checks that do not invoke Git, and record test/build evidence. Do not invoke Git, stage, or commit.
