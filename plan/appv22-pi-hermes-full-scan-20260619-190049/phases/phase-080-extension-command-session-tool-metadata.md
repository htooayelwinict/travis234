# Phase 080: Extension Command Session and Tool Metadata

## Status

Complete.

## Reality Check

Pi's `ExtensionContext` exposes session metadata and tool registry methods to command handlers: `setSessionName()`, `getSessionName()`, `getActiveTools()`, `getAllTools()`, `setActiveTools()`, and `getCommands()`. Appv22 already had the underlying session and tool APIs, but they were not reachable from `ExtensionCommandContext`.

## Changes

- Added a regression proving a registered command can inspect and update session/tool metadata through its context.
- Added snake_case and camelCase context methods:
  - `set_session_name()` / `setSessionName()`
  - `get_session_name()` / `getSessionName()`
  - `get_active_tools()` / `getActiveTools()`
  - `get_all_tools()` / `getAllTools()`
  - `set_active_tools()` / `setActiveTools()`
  - `get_commands()` / `getCommands()`
- Wired methods to existing `AgentSession` APIs and the `ExtensionRunner` registered-command registry.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_exposes_session_and_tool_metadata -q`
  - Failed with missing `setSessionName`.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_exposes_session_and_tool_metadata -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"`
  - `7 passed, 79 deselected`
