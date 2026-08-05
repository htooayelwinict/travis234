# Main Coding OffSec Runtime Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port every reusable OffSec runtime, TUI, managed-process, tmux, subagent, and npm-launcher improvement into the main Travis234 coding profile and release it as main version `2.4.0` after complete automated and ten-prompt TUI qualification.

**Architecture:** Apply independent, regression-first forward ports against current `main`; do not merge the OffSec branch. Keep the existing agent loop and compaction control planes untouched. Adapt only product-facing names and guidance from OffSec to the coding profile.

**Tech Stack:** Python 3.13, pytest, POSIX PTYs, tmux, SQLite, Node 20, npm, uv, Docker, GitHub Container Registry, PyPI.

## Global Constraints

- Work in `/Users/htooayelwin/lewis/travis234` on `main`, as explicitly authorized by the operator.
- Preserve the two pre-existing untracked user documents; do not stage or modify them unless they overlap this plan.
- Do not modify `travis/agent/agent_loop.py`, `travis/compaction/`, provider streaming, model catalogs, iteration budgeting, or bounded parallel execution.
- Preserve `~/.travis234` as the only application state root.
- Add and run a failing regression before each production behavior change.
- Keep credentials out of tracked files, patches, command output, Docker layers, and release metadata.
- Do not commit, push, tag, or publish until all focused tests, repository tests, builds, container smoke checks, and the ten-prompt TUI qualification pass.
- Publish only the main identities: `travis234`, `@htooayelwinict/travis234`, and `ghcr.io/htooayelwinict/travis234`.
- Do not use subagents during implementation; execution is inline.

---

### Task 1: Recover narrowly malformed process arguments

**Files:**
- Modify: `tests/test_process_tools.py`
- Modify: `travis/coding_agent/tools/process.py`

**Interfaces:**
- Consumes: `prepare_process_arguments(raw_args: Mapping[str, object])`.
- Produces: canonical arguments accepted by the existing strict action schema.

- [ ] **Step 1: Add production-payload and conflict regressions**

Add tests whose literal expectations include:

```python
assert prepare_process_arguments({
    "action": "wait",
    "cursor": 17,
    "sessionid": "proc8e355b88e4fad64f5a9bd8c1e9cbc284",
    "waittimems": 120_000,
}) == {
    "action": "wait",
    "cursor": 17,
    "session_id": "proc_8e355b88e4fad64f5a9bd8c1e9cbc284",
    "wait_time_ms": 60_000,
}
```

Cover camelCase aliases, identical canonical-plus-alias values, conflicting values, and an unknown `sessionHandle` that remains invalid.

- [ ] **Step 2: Run the new tests and record the expected schema failure**

Run:

```bash
uv run python -m pytest -q tests/test_process_tools.py -k 'collapsed_wait_fields or process_argument_preparation_rejects_conflicting_aliases or compatibility_arguments'
```

Expected: the production payload fails because `sessionid` and the collapsed process id are not recovered.

- [ ] **Step 3: Implement closed-vocabulary normalization**

Add `MAX_PROCESS_WAIT_MS = 60_000`, a collapsed-token alias map, exact collapsed process-id regex, conflict detection after integer coercion, and wait clamping. Keep `_validate_args` strict and do not globally normalize arbitrary fields.

- [ ] **Step 4: Run the focused process-tool suite**

```bash
uv run python -m pytest -q tests/test_process_tools.py
```

Expected: all process-tool tests pass.

### Task 2: Harden process update cadence, PTY EOF, and final drain

**Files:**
- Modify: `tests/test_process_service.py`
- Modify: `tests/test_process_local.py`
- Modify: `tests/test_process_tools.py`
- Modify: `travis/coding_agent/processes/service.py`
- Modify: `travis/coding_agent/tools/process.py`
- Modify: `travis/coding_agent/tools/bash.py`

**Interfaces:**
- Consumes: `ProcessSessionService.start`, `write`, reader threads, and process-tool schemas.
- Produces: bounded foreground callbacks, non-destructive PTY EOF rejection, and complete terminal output.

- [ ] **Step 1: Add an output-coalescing regression**

Start a fake process with one megabyte of output and a 60-second foreground interval. Assert the terminal snapshot contains every byte and the foreground listener runs once rather than once per 4 KiB reader chunk.

- [ ] **Step 2: Run the coalescing test and observe callback amplification**

```bash
uv run python -m pytest -q tests/test_process_service.py -k foreground_output_is_coalesced
```

- [ ] **Step 3: Add a shared service-owned update cadence**

Add `foreground_update_interval_seconds: float = 0.1`, reject negative values, store one next-update timestamp per managed process, and choose the listener under the condition lock while constructing and delivering snapshots after releasing it.

- [ ] **Step 4: Add PTY EOF regressions**

Assert that `eof=True` on a running PTY raises `ProcessStateError`, writes no EOF sentinel, and leaves the PTY writable. Preserve real pipe half-close coverage and add a real local PTY Ctrl-D test through raw input.

- [ ] **Step 5: Run the PTY tests and observe the current destructive or unsupported behavior**

```bash
uv run python -m pytest -q tests/test_process_service.py tests/test_process_local.py tests/test_process_tools.py -k 'pty and eof'
```

- [ ] **Step 6: Enforce transport-aware EOF and update guidance**

Reject PTY `eof=True` before queuing input or changing input state. Keep pipe EOF unchanged. Update `process` and `bash` descriptions to distinguish command timeout, `process.wait`, and explicit PTY Ctrl-D through `write_raw`.

- [ ] **Step 7: Add a continuously growing final-output regression**

Use a real or controlled reader that emits more data while final draining is active. Assert the complete one-megabyte payload reaches the terminal snapshot and the output size is exact.

- [ ] **Step 8: Run the drain regression and observe the absolute deadline truncate output**

```bash
uv run python -m pytest -q tests/test_process_service.py tests/test_process_local.py -k 'drain or large_output'
```

- [ ] **Step 9: Convert final drain to an inactivity deadline**

Track observed output size and reset the drain deadline whenever it grows. Preserve the existing maximum inactivity interval and thread lifecycle.

- [ ] **Step 10: Run the focused lifecycle suites**

```bash
uv run python -m pytest -q tests/test_process_service.py tests/test_process_local.py tests/test_process_tools.py
```

### Task 3: Optimize durable completion publication

**Files:**
- Modify: `tests/test_process_completions.py`
- Modify: `travis/coding_agent/processes/types.py`
- Modify: `travis/coding_agent/processes/completions.py`
- Modify: `travis/coding_agent/processes/service.py`

**Interfaces:**
- Consumes: terminal `ProcessCompletionRecord` values and private spool files.
- Produces: exact persisted output metadata with atomic hard-link or copy promotion.

- [ ] **Step 1: Add hard-link, fallback, and exact-line-count regressions**

Assert same-filesystem publication shares inode identity, final mode is `0600`, the source can disappear without losing the destination, `EXDEV` uses atomic copy, non-fallback errors propagate, and stored size and total lines match literal fixtures.

- [ ] **Step 2: Run completion tests and observe the copy-only behavior**

```bash
uv run python -m pytest -q tests/test_process_completions.py
```

- [ ] **Step 3: Carry total line count and implement hard-link promotion**

Add `total_lines` to the process completion record, populate it from the output spool snapshot, validate stored size and line count, create the destination through a private same-directory temporary link, chmod to `0600`, replace atomically, and fsync the directory. Fall back only for cross-device, permission, and unsupported-link errors.

- [ ] **Step 4: Run completion and process suites**

```bash
uv run python -m pytest -q tests/test_process_completions.py tests/test_process_service.py tests/test_process_tools.py
```

### Task 4: Add native-scrollback-safe TUI input rendering

**Files:**
- Modify: `tests/test_tui_terminal_and_input.py`
- Modify: `tests/test_tui_commands_and_extensions.py`
- Modify: `travis/tui/tui.py`
- Modify: `travis/tui/interactive_view.py`

**Interfaces:**
- Produces: `TUI.set_input_tail_components(components: list[Component]) -> None` and a private stable-tail render attempt.

- [ ] **Step 1: Add retained-history and wrap-fallback regressions**

Use counting history components to assert a stable one-line editor keystroke renders zero retained-history components while keeping the complete `previous_lines`. At narrow width, assert a wrapping edit falls back to a full history render.

- [ ] **Step 2: Run the new tests and observe full-transcript rendering per keypress**

```bash
uv run python -m pytest -q tests/test_tui_terminal_and_input.py tests/test_tui_commands_and_extensions.py -k 'input_fast_path or keeps_complete_history'
```

- [ ] **Step 3: Implement validated tail snapshots and the safe input fast path**

Store registered tail components plus start and line-count snapshots. Capture only when rendered tail lines exactly match the full render suffix. On keyboard input, patch the cached prefix only when dimensions, focus, overlays, images, and visual line count are stable; otherwise call the unchanged full renderer.

- [ ] **Step 4: Register the InteractiveView live suffix**

Register `editor_container`, `widget_container_below`, `status`, and `footer_container` in their existing root order. Do not include history, header, or above-editor widgets.

- [ ] **Step 5: Run all TUI-focused tests**

```bash
uv run python -m pytest -q tests/test_tui_terminal_and_input.py tests/test_tui_commands_and_extensions.py
```

### Task 5: Add the workspace-scoped tmux tool

**Files:**
- Create: `travis/coding_agent/tools/tmux.py`
- Modify: `travis/coding_agent/tools/__init__.py`
- Modify: `travis/coding_agent/session_tooling.py`
- Modify: `travis/coding_agent/session_types.py`
- Modify: `tests/test_tmux_tool.py`
- Modify: `tests/test_coding_tools_and_subagents.py`

**Interfaces:**
- Produces: `create_tmux_tool_definition(cwd, operations=None, workspace=None)` and the `tmux` actions `start`, `send`, `capture`, `list`, `stop`.

- [ ] **Step 1: Add schema, workspace isolation, fast-exit, and real-roundtrip regressions**

Assert logical names map to `travis234-<12 hex>-<logical>`, another workspace's resolved name is rejected, `start` preserves output from a command that exits immediately, a failed setup removes its partial session, and a real installed tmux supports start/send/capture/list/stop.

- [ ] **Step 2: Run tmux tests and observe the missing tool**

```bash
uv run python -m pytest -q tests/test_tmux_tool.py
```

- [ ] **Step 3: Implement the tmux tool through direct argv operations**

Validate logical names and bounded command/input sizes, derive the workspace namespace from the canonical cwd, bootstrap an empty session, enable `remain-on-exit`, respawn the requested command, and return both logical and resolved names. Never build shell command strings for tmux control operations.

- [ ] **Step 4: Register and activate tmux for coding sessions**

Add tmux to the built-in definition/tool factories, session construction options, and `_DEFAULT_ACTIVE_TOOL_NAMES`. Add coding-neutral descriptions for servers, watchers, REPLs, test loops, and long builds.

- [ ] **Step 5: Run tmux and tool-registration tests**

```bash
uv run python -m pytest -q tests/test_tmux_tool.py tests/test_coding_tools_and_subagents.py -k 'tmux or active_tool'
```

### Task 6: Enable tool-capable workspace-writing coding subagents

**Files:**
- Modify: `tests/test_subagents.py`
- Modify: `tests/test_coding_tools_and_subagents.py`
- Modify: `tests/test_coding_resources_and_services.py`
- Modify: `travis/coding_agent/subagents.py`
- Modify: `travis/coding_agent/session_types.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `travis/coding_agent/subagent_trace.py`

**Interfaces:**
- Produces: fixed `CODING_SUBAGENT_TOOLS`, workspace-write internal child tasks, shared process control with child ownership, and changed-file evidence.

- [ ] **Step 1: Add workspace-write task and child-tool regressions**

Assert default child tasks use `workspace_write` with exactly `read`, `grep`, `find`, `ls`, `bash`, `process`, `tmux`, `edit`, and `write`; arbitrary tools and child-selected cwd/sandbox remain rejected; child prompts prohibit recursive delegation and require execution plus verification.

- [ ] **Step 2: Run the child-policy tests and observe read-only behavior**

```bash
uv run python -m pytest -q tests/test_subagents.py tests/test_coding_resources_and_services.py -k 'workspace_write or allowed_tools or file_mutation'
```

- [ ] **Step 3: Replace the read-only defaults with the fixed coding catalog**

Rename the product-neutral constant to `CODING_SUBAGENT_TOOLS`, default internal children to `workspace_write`, remove the natural-language file-mutation blocker, retain fixed workspace and catalog validation, and add coding-oriented execution/evidence instructions.

- [ ] **Step 4: Add managed-process sharing and cleanup regressions**

Run a real internal child that starts a managed process, follows it through the process tool, and completes. Assert the child receives the parent's service under a distinct owner and that remaining child-owned managed processes are killed on completion while parent and sibling processes survive.

- [ ] **Step 5: Run the process-sharing tests and observe missing child access or cleanup**

```bash
uv run python -m pytest -q tests/test_coding_tools_and_subagents.py tests/test_process_tools.py -k 'subagent and process'
```

- [ ] **Step 6: Share the process service with child-specific ownership**

Derive a child owner from the parent owner and task id, inject the shared service and owner into the child session, and best-effort kill only active snapshots owned by that child in `finally` before shutdown.

- [ ] **Step 7: Add changed-file evidence regressions**

Exercise real child edit/write tool traces and assert only successful in-workspace file paths appear once in `files_changed`; failed tools and outside-workspace paths are excluded.

- [ ] **Step 8: Capture edit/write paths and report changed files**

Record the `path` argument at tool start, resolve it against the child cwd after successful completion, filter it to the workspace, and place the deduplicated relative paths into `SubagentResult.files_changed`.

- [ ] **Step 9: Run all subagent-focused suites**

```bash
uv run python -m pytest -q tests/test_subagents.py tests/test_coding_tools_and_subagents.py tests/test_coding_resources_and_services.py tests/test_process_tools.py
```

### Task 7: Add coding orchestration and minimal delegation availability

**Files:**
- Modify: `tests/test_coding_tools_and_subagents.py`
- Modify: `travis/coding_agent/session_types.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `travis/coding_agent/system_prompt.py`

**Interfaces:**
- Consumes: the active coding tool catalog and ordinary user prompt text before a turn.
- Produces: a senior-SE tool-routing policy, always-visible core spawn/wait primitives, on-demand advanced management, and a deterministic explicit opt-out.

- [ ] **Step 1: Add core availability, advanced activation, and opt-out regressions**

Assert an ordinary coding session exposes only `spawn_subagent` and `wait_subagent`, advanced management remains absent, parallel/multiple/split-work language activates the complete lifecycle catalog for that turn, and `do not use subagents` hides every subagent tool before restoring the normal catalog.

- [ ] **Step 2: Run activation tests and observe missing autonomous delegation**

```bash
uv run python -m pytest -q tests/test_coding_tools_and_subagents.py -k 'on_demand or parallel_delegation or opt_out'
```

- [ ] **Step 3: Add the senior software-engineering orchestration policy**

Teach the coding profile to route finite commands to `bash`, interactive work to PTY plus `process`, durable work to `tmux`, and independent bounded workstreams to concurrent children. Require exact child scope, parent-owned integration, collection of every result, independent verification of material child claims, and evidence-backed final reporting. Include delegation instructions only when the core primitives are active.

- [ ] **Step 4: Expose the minimum delegation gateway**

Add only `spawn_subagent` and `wait_subagent` to the default coding catalog. Keep list/get/expand/cancel on demand. Hide all subagent tools for an explicit opt-out turn without changing agent-loop ordering, iteration budgeting, compaction, or bounded concurrency.

- [ ] **Step 5: Run the complete subagent suite and installed TUI routing scenarios**

```bash
uv run python -m pytest -q tests/test_subagents.py tests/test_coding_tools_and_subagents.py tests/test_coding_resources_and_services.py
```

In an installed-wheel TUI, use natural prompts that do not name internal tools to prove concurrent delegation plus parent verification, finite bash, PTY/process follow-up, durable tmux cleanup, and explicit parent-only opt-out.

### Task 8: Forward explicit npm dotenv files and package tmux

**Files:**
- Modify: `packages/travis234-cli/test/travis234-cli.test.js`
- Modify: `packages/travis234-cli/bin/travis234.js`
- Modify: `packages/travis234-cli/README.md`
- Modify: `Dockerfile`
- Modify: `Dockerfile.release`

**Interfaces:**
- Consumes: npm launcher `--dotenv <path>` or `--dotenv=<path>`.
- Produces: a validated Docker `--env-file` argument without a dotenv mount or Python CLI path.

- [ ] **Step 1: Add launcher dotenv regressions**

Create a temporary workspace dotenv file and assert the Docker argv contains `--env-file <absolute path>`, contains no dotenv mount, and ends with the main image plus `--cwd /workspace` and only the real app prompt. Assert a missing dotenv file fails before Docker invocation.

- [ ] **Step 2: Run npm tests and observe that the launcher strips dotenv**

```bash
npm --prefix packages/travis234-cli test
```

- [ ] **Step 3: Implement explicit dotenv parsing and Docker forwarding**

Resolve the dotenv after resolving cwd, validate `isFile()`, remove only launcher-owned cwd from app arguments, and insert `--env-file` before the image. Do not read or print dotenv contents.

- [ ] **Step 4: Add tmux to both main Docker package lists**

Keep the Python 3.13 slim base and main identity unchanged. Add only the `tmux` runtime package required by the new coding tool.

- [ ] **Step 5: Run npm and distribution contract tests**

```bash
npm --prefix packages/travis234-cli test
uv run python -m pytest -q tests/test_distribution_contract.py tests/test_release_workflow.py
```

### Task 9: Set main stable version 2.4.0 and document behavior

**Files:**
- Modify: `pyproject.toml`
- Modify: `package.json`
- Modify: `packages/travis234-cli/package.json`
- Modify: `README.md`
- Modify: `packages/travis234-cli/README.md`
- Create: `docs/verification/main-2.4.0-ten-prompt-tui.md`
- Modify: version/distribution contract tests that assert the current release.

**Interfaces:**
- Produces: aligned main Python/npm version `2.4.0` and a reproducible ten-prompt acceptance protocol.

- [ ] **Step 1: Add/update release contract expectations for 2.4.0**

Assert all three manifests agree on `2.4.0`, the main npm package points to `ghcr.io/htooayelwinict/travis234`, and no OffSec identity appears in main release metadata.

- [ ] **Step 2: Run release-contract tests and observe the current 2.3.5 expectation**

```bash
uv run python -m pytest -q tests/test_distribution_contract.py tests/test_installed_metadata.py tests/test_brand_contract.py tests/test_release_workflow.py
```

- [ ] **Step 3: Update manifests and user documentation**

Set `2.4.0` consistently, update the README badge, document tmux, workspace-writing on-demand subagents, managed-process alias recovery, and npm `--dotenv` without copying OffSec language.

- [ ] **Step 4: Write the ten-prompt coding TUI protocol**

Use the real wheel-installed `travis234` console entry point, isolated `HOME`/agent state, a temporary git coding fixture, an attached PTY, and one continuous session. Define these prompts with observable evidence:

1. Produce 120 numbered lines ending `CODING-HISTORY-END` and verify responsive typing at the live bottom.
2. Read the fixture README and report literal project metadata through `read`.
3. Run a finite Python test through `bash` and report the exact exit result.
4. Start an interactive Python PTY, send a follow-up expression through `process`, then send raw Ctrl-D and collect exit.
5. Start a fast-exiting tmux command and capture its retained marker.
6. Start a durable tmux development server, capture readiness, then stop it.
7. Spawn one coding child to create and verify one exclusively owned file; report changed-file evidence.
8. Spawn two independent children in parallel with disjoint files, collect both, and synthesize their results.
9. Explicitly say not to use subagents and perform a parent-only inspection.
10. After the accumulated transcript, type and submit a multiline final audit that verifies created files and reports no active managed process or tmux leak.

- [ ] **Step 5: Run release-contract tests again**

```bash
uv run python -m pytest -q tests/test_distribution_contract.py tests/test_installed_metadata.py tests/test_brand_contract.py tests/test_release_workflow.py
```

### Task 10: Automated repository qualification

**Files:**
- No production changes unless a failing gate exposes a regression; any fix must restart its own red-green cycle.

- [ ] **Step 1: Run formatting/static checks configured by the repository**

Inspect `pyproject.toml` and package scripts, then run the exact configured checks rather than inventing new ones.

- [ ] **Step 2: Run the complete Python suite**

```bash
uv run python -m pytest -q
```

- [ ] **Step 3: Run npm launcher tests**

```bash
npm --prefix packages/travis234-cli test
```

- [ ] **Step 4: Build Python and npm packages**

```bash
uv build
npm pack --dry-run
npm --prefix packages/travis234-cli pack --dry-run
```

- [ ] **Step 5: Build and smoke-test the main release container**

Build `Dockerfile.release`, verify `travis234 --help`, `python --version`, `node --version`, `tmux -V`, npm launcher identity, and a finite print-mode prompt with a controlled local/faux provider where supported.

### Task 11: Ten-prompt installed TUI qualification

**Files:**
- Modify: `docs/verification/main-2.4.0-ten-prompt-tui.md` with non-secret observed results.

- [ ] **Step 1: Build and install the wheel into an isolated environment**

Create a temporary virtual environment, install the freshly built `travis234-2.4.0` wheel, and confirm `command -v travis234` resolves to that environment rather than the source tree.

- [ ] **Step 2: Prepare an isolated coding fixture and state root**

Create a temporary git project containing a README, a small Python module, tests, and disjoint child-owned paths. Copy no credentials; load the operator-selected dotenv only by path at runtime.

- [ ] **Step 3: Run all ten prompts in one attached TUI PTY**

Use the real console entry point and selected SOTA-capable configured model. Wait for each prompt to finish. Record tool calls, completion marker, expected file/process/tmux evidence, and any retry.

- [ ] **Step 4: Verify post-session state outside the TUI**

Run tests in the fixture, inspect child-created files and git diff, assert no managed process owned by the session remains, assert no workspace tmux session remains, and confirm terminal exit restoration.

- [ ] **Step 5: Record the ten-scenario result matrix**

Record PASS/FAIL with concise evidence and no secrets. Any agent/model defect may be retried, but a runtime, tool, state, ownership, or TUI failure blocks release and receives a new regression-first fix.

### Task 12: GitOps and main release publication

**Files:**
- All intended plan, source, test, documentation, and version files.

- [ ] **Step 1: Audit the final diff and exclude unrelated files**

Use `git status`, `git diff --check`, and `git diff --stat`. Confirm the two pre-existing untracked user documents remain unstaged unless intentionally incorporated.

- [ ] **Step 2: Re-run the full verification gate on the exact release tree**

Repeat the complete Python suite, npm tests, package builds, container smoke tests, and inspect the ten-prompt evidence before making any success claim.

- [ ] **Step 3: Commit and push main**

Stage only intended files, create a main `2.4.0` feature/release commit, verify its tree, pull/rebase only if the remote moved and the update is non-destructive, then push `main` without force.

- [ ] **Step 4: Publish GHCR**

Trigger or run the existing main release-image workflow first. Verify the `2.4.0` and production tags by pulling and running the published image. If the workflow fails and cannot be repaired immediately, build/tag/login/push the exact verified release tree directly with Docker and re-run the remote smoke check.

- [ ] **Step 5: Publish npm**

Run package preflight, `npm whoami`, and publish `@htooayelwinict/travis234@2.4.0`. If interactive browser authentication is required, open the npm authorization page for the operator and resume only after authorization. Verify through `npm view` and a clean `npx` smoke run.

- [ ] **Step 6: Publish PyPI**

Validate artifacts with Twine, publish `travis234==2.4.0` using the configured credential environment without printing it, verify PyPI metadata, install into a fresh environment, and run `travis234 --help`.

- [ ] **Step 7: Complete the requirement-by-requirement release audit**

Match every design capability and explicit release deliverable to fresh source, test, runtime, registry, or package-index evidence. Mark the goal complete only if every item is proven.
