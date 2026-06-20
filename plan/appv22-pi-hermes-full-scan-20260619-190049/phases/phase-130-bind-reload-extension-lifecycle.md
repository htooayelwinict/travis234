# Phase 130 - Bind Reload Extension Lifecycle

## Goal

Port the next verified Pi `AgentSession` lifecycle gap into appv22 without importing from `pi/`: public `bindExtensions()` / `bind_extensions()` and `reload()` facades that route extension error listeners, emit reload lifecycle events, rediscover extension resources with the correct reason, and preserve extension flag values.

Follow-up in this phase closes the rest of Pi's binding surface: UI context/mode, command context actions, abort handler, and shutdown handler.

This phase does not change Hermes compaction code.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
  - `bindExtensions()`
  - `reload()`
  - `extendResourcesFromExtensions()`

## Red

Added focused regressions in `appV2.2/tests/test_coding_agent.py`:

- `test_agent_session_bind_extensions_applies_error_listener_before_session_start`
- `test_agent_session_bind_extensions_applies_ui_command_abort_and_shutdown_bindings`
- `test_agent_session_reload_emits_lifecycle_and_rediscover_resources`

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'bind_extensions_applies_error_listener or reload_emits_lifecycle' -q
```

Result: failed as expected with missing `AgentSession.bind_extensions`.

## Implementation

- Added `AgentSession.bind_extensions()` / `bindExtensions`.
- Added `AgentSession.reload()`.
- Added `ExtensionRunner.set_ui_context()` / `setUIContext()`, `get_ui_context()` / `getUIContext()`, `has_ui()` / `hasUI()`, and mode storage.
- Added `ExtensionRunner.bind_command_context()` / `bindCommandContext()` plus callable command-context handlers for `waitForIdle`, `newSession`, `fork`, `navigateTree`, `switchSession`, and `reload`.
- Added runner abort/shutdown handler storage and `abort()` / `shutdown()` dispatch.
- `AgentSession.bind_extensions()` now stores and reapplies Pi-style `uiContext`, `mode`, `commandContextActions`, `abortHandler`, `shutdownHandler`, and `onError` fields.
- `AgentSession.reload()` reapplies extension bindings before emitting reload startup events.
- Bound Pi-style extension error listeners through `ExtensionRunner.on_error()`.
- Re-emitted the session start lifecycle when bindings are applied, matching the public Pi lifecycle point.
- On reload:
  - emits `session_shutdown` with reason `reload`,
  - reloads the resource loader,
  - preserves extension flag values,
  - refreshes tool registry with extension tools,
  - emits `session_start` with reason `reload` when extension bindings are active,
  - rediscover extension resources with reason `reload`.
- Unsubscribes the bound extension error listener during `dispose()`.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_bind_extensions_applies_ui_command_abort_and_shutdown_bindings -q
```

Result: `1 passed`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'bind_extensions or reload_emits_lifecycle' -q
```

Result: `3 passed, 113 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'bind_extensions_applies_error_listener or reload_emits_lifecycle' -q
```

Result: `2 passed, 113 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k '(extension_runner or bind_extensions or reload_emits_lifecycle or extension_command or session_runtime) and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `26 passed, 91 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k '(resource_loader or reload or session_start or extension_runner_lifecycle or dynamic or registered_tool or extension_command) and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `22 passed, 93 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q
```

Result: `36 passed`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'bind_extensions_applies_error_listener or reload_emits_lifecycle or create_replaced_session_context or retry or export_to_html or export_to_jsonl' -q
```

Result: `9 passed, 106 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'compact or shortcut or extension' -q
```

Result: `22 passed, 28 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `113 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `115 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `285 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `287 passed, 2 deselected`.

The deselected tests are the existing macOS environment limitation where this machine lacks a usable `python` executable for two shell/exec tests.

## Remaining Count

The full goal remains active. This phase closes a public Pi extension lifecycle facade gap. Remaining likely slices include richer HTML export tree/template parity, deeper model/settings registry parity, and live TUI rendering confidence checks while preserving the current Hermes compaction behavior.
