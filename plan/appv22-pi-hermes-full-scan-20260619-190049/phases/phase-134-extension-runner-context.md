# Phase 134: Extension Runner Pi Context

## Goal

Port the next concrete Pi extension-runner gap into appv22 without importing from `pi/`: handlers receive Pi-style `(event, ctx)` arguments, and the runner exposes `bindCore()`, `createContext()`, and `createCommandContext()` with lazy session/runtime context.

This phase does not change Hermes compaction code.

## Reference

- `pi/packages/coding-agent/src/core/extensions/runner.ts`
  - `bindCore()`
  - `createContext()`
  - `createCommandContext()`
  - event handler invocation with context
- `pi/packages/coding-agent/src/core/extensions/types.ts`
  - `ExtensionContext`
  - `ExtensionCommandContext`

## Red

Added `test_extension_runner_passes_pi_context_to_handlers_and_command_context` in `appV2.2/tests/test_coding_agent.py`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_extension_runner_passes_pi_context_to_handlers_and_command_context -q
```

Result: failed as expected because `ExtensionRunner.__init__()` did not accept `cwd` and the runner had no Pi context binding API.

## Implementation

- Added `ExtensionContextView` and `ExtensionCommandContextView` in `appV2.2/appv22/coding_agent/extensions.py`.
- Added `ExtensionRunner(cwd=..., session_manager=..., model_registry=...)` state.
- Added `bind_core()` / `bindCore()` for Pi-style context actions:
  - model, idle/trust state, signal, abort, pending messages, shutdown, context usage, compaction, system prompt, and system-prompt options.
- Added `create_context()` / `createContext()` and `create_command_context()` / `createCommandContext()`.
- Added `invalidate()` with Pi's stale-context error shape for captured contexts.
- Updated extension emit paths to pass context to two-argument handlers while preserving existing one-argument Python handlers.
- Bound live `AgentSession` context into the runner during session construction, including cwd, active tools, commands, model/thinking setters, abort/shutdown, context usage, and compaction callbacks.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_extension_runner_passes_pi_context_to_handlers_and_command_context -q
```

Result: `1 passed`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k '(extension_runner or passes_pi_context or bind_extensions or reload_emits_lifecycle or extension_command) and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `24 passed, 96 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'extension or shortcut or render or compact' -q
```

Result: `32 passed, 18 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `118 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q
```

Result: `36 passed`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `291 passed, 2 deselected`.

```bash
git diff --check -- appV2.2/appv22/coding_agent/extensions.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/tests/test_coding_agent.py plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md
```

Result: passed.

The deselected tests are the existing macOS environment limitation where this machine lacks a usable `python` executable for two shell/exec tests.

## Compaction Note

No compaction runtime code was changed in this phase. The protected Hermes-style compaction suites remained green (`36 passed`).

## Remaining Count

The full goal remains active. This phase closes the extension-runner context surface; remaining likely slices include richer extension API/runtime actions, full Pi export template parity, and live TUI behavior checks while preserving the current Hermes compaction layers.
