# Phase 081: Extension Command Thinking Level

## Status

Complete.

## Reality Check

Pi's `ExtensionContext` exposes `getThinkingLevel()` and `setThinkingLevel()` to extension command handlers. Appv22 already had session-level thinking-level state and events, but command contexts could not access them.

## Changes

- Added a regression proving a registered command can read and update thinking level through its context.
- Added snake_case and camelCase context methods:
  - `get_thinking_level()` / `getThinkingLevel()`
  - `set_thinking_level()` / `setThinkingLevel()`
- Wired the methods to existing `AgentSession.thinking_level` and `AgentSession.set_thinking_level()`.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_exposes_thinking_level -q`
  - Failed with missing `getThinkingLevel`.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_exposes_thinking_level -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"`
  - `8 passed, 79 deselected`
