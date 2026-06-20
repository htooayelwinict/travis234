# Phase 078: Extension Command Context Actions

## Status

Complete.

## Reality Check

Pi's `ExtensionContext` exposes action methods such as `sendMessage()` and `appendEntry()` to registered command handlers. Appv22 had the underlying custom-message and custom-entry session APIs, but `ExtensionCommandContext` only exposed system-prompt helpers. That meant a Pi-style slash command could not persist extension state or inject a custom message through its command context.

## Changes

- Added a regression proving registered command handlers can call:
  - `ctx.appendEntry(customType, data)`
  - `ctx.sendMessage(message, options)`
- Extended `ExtensionCommandContext` with snake_case and camelCase methods:
  - `append_entry()` / `appendEntry()`
  - `send_message()` / `sendMessage()`
- Wired the context methods to `AgentSession.append_custom_entry()` and `AgentSession.send_custom_message()`.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_can_append_custom_entries_and_messages -q`
  - Failed with `AttributeError: 'ExtensionCommandContext' object has no attribute 'appendEntry'`.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_can_append_custom_entries_and_messages -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"`
  - `5 passed, 79 deselected`
