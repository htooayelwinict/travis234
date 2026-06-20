# Phase 083: Extension Command Exec

## Status

Complete.

## Reality Check

Pi's `ExtensionContext` exposes `exec(command, args, options)` for extension command handlers. It is an argv-style utility that returns `{ stdout, stderr, code, killed }` and is distinct from user bash transcript recording. Appv22 had user-bash execution, but command contexts did not expose Pi's utility exec surface.

## Changes

- Added a regression proving a registered command can call `ctx.exec(...)`.
- Added `exec()` to `ExtensionCommandContext`.
- Implemented `AgentSession._extension_exec()` as an argv-style subprocess runner with cwd and timeout support.
- Kept the utility separate from `execute_bash()` so it does not append `bashExecution` messages to the session.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_exec_runs_without_session_message -q`
  - Failed with missing `exec`.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_exec_runs_without_session_message -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"`
  - `10 passed, 79 deselected`
