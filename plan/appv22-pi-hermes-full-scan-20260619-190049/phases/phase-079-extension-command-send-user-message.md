# Phase 079: Extension Command Send User Message

## Status

Complete.

## Reality Check

Pi's `ExtensionContext` exposes `sendUserMessage()` so command handlers can trigger a normal user turn. Appv22's `ExtensionCommandContext` had just gained `sendMessage()` and `appendEntry()`, but still lacked the user-message action.

## Changes

- Added a regression proving a registered slash command can call `ctx.sendUserMessage(...)`.
- Added `send_user_message()` / `sendUserMessage()` to `ExtensionCommandContext`.
- Wired default command-context user-message sending to `AgentSession.prompt()`, with `deliverAs="steer"` and `deliverAs="followUp"` routed to the existing queue APIs.
- Kept slash-command prompt return semantics Pi-like: the command action is handled locally and the outer slash-command call returns no direct provider transcript.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_can_send_user_message -q`
  - Failed with missing `sendUserMessage`.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_can_send_user_message -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"`
  - `6 passed, 79 deselected`
