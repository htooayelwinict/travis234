# Phase 137: Pi Extension Runner Action Surface

## Goal

Bring appv22's `ExtensionRunner.bind_core()` action surface in line with Pi's `ExtensionActions` runtime contract.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts`
  - `ExtensionActions`
  - `ExtensionContextActions`
  - `ExtensionCommandContextActions`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
  - `bindCore(...)`
  - `bindCommandContext(...)`
- `pi/packages/coding-agent/src/core/agent-session.ts`
  - session-to-runner `bindCore(...)` action wiring

## Red Tests

Added `test_extension_runner_bind_core_exposes_pi_action_surface`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_extension_runner_bind_core_exposes_pi_action_surface -q
```

Result: failed as expected with `AttributeError: 'ExtensionRunner' object has no attribute 'sendMessage'`.

Added `test_agent_session_binds_pi_extension_runner_action_surface`.

Initial run with the session action wiring removed:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_binds_pi_extension_runner_action_surface -q
```

Result: failed as expected because `runner.setSessionName("Runner Session")` remained a no-op and `session.session_name` stayed `None`.

## Implementation

- Added Pi-style action defaults to `ExtensionRunner` for:
  - `sendMessage` / `send_message`;
  - `sendUserMessage` / `send_user_message`;
  - `appendEntry` / `append_entry`;
  - `setSessionName` / `set_session_name`;
  - `getSessionName` / `get_session_name`;
  - `setLabel` / `set_label`;
  - `getActiveTools` / `get_active_tools`;
  - `getAllTools` / `get_all_tools`;
  - `setActiveTools` / `set_active_tools`;
  - `refreshTools` / `refresh_tools`;
  - `getCommands` / `get_commands`;
  - `setModel` / `set_model`;
  - `getThinkingLevel` / `get_thinking_level`;
  - `setThinkingLevel` / `set_thinking_level`.
- Updated `ExtensionRunner.bind_core()` to copy those actions from the supplied Pi-style action map instead of discarding the `actions` argument.
- Bound the live `AgentSession` callbacks for message, user-message, custom-entry, session-name, label, tool, command, model, and thinking-level actions.
- Removed the stray `waitForIdle` key from the core action map; Pi keeps wait/session replacement actions under `bindCommandContext(...)`, not `bindCore(...)`.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_extension_runner_bind_core_exposes_pi_action_surface appV2.2/tests/test_coding_agent.py::test_agent_session_binds_pi_extension_runner_action_surface -q` -> `2 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k '(extension_runner or binds_pi_extension_runner_action_surface or action_surface or passes_pi_context or bind_extensions or reload_emits_lifecycle or extension_command) and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `26 passed, 99 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'extension or shortcut or render or compact' -q` -> `32 passed, 18 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `123 passed, 2 deselected`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `296 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Full Pi export template parity remains incomplete.
- Live TUI ergonomics still need dedicated parity slices.
- The full appv22 Pi/Hermes objective remains active and unproven.
