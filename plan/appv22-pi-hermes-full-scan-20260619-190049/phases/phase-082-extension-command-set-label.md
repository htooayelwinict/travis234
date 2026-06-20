# Phase 082: Extension Command Set Label

## Status

Complete.

## Reality Check

Pi's `ExtensionContext` exposes `setLabel(entryId, label)` so command handlers can set or clear labels on session entries. Appv22 had JSONL label entries through `SessionStore.append_label_change()`, but the action was not exposed through `ExtensionCommandContext`.

## Changes

- Added a regression proving a registered command can label an existing session entry through `ctx.setLabel(...)`.
- Added `set_label()` / `setLabel()` to `ExtensionCommandContext`.
- Added `AgentSession.set_label()` / `setLabel()` as the local delegate to `SessionStore.append_label_change()`.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_sets_entry_label -q`
  - Failed with missing `setLabel`.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_sets_entry_label -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"`
  - `9 passed, 79 deselected`
