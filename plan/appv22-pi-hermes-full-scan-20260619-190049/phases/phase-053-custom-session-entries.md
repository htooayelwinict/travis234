# Phase 053: Custom Session Entries

Status: complete

## Goal

Port Pi custom session entry support into appv22 without importing reference modules: opaque extension-state entries, context-participating custom messages, custom message persistence/reload, provider conversion, and next-turn injection.

## Reference Files

- `pi/packages/coding-agent/src/core/session-manager.ts`
- `pi/packages/coding-agent/src/core/messages.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/session-manager/tree-traversal.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-queue.test.ts`

## Changes

- Added regressions for opaque `custom` entries, persisted/reloaded `custom_message` entries, provider conversion, and `deliverAs="nextTurn"` injection.
- Added `CustomMessage` to the appv22 session-message layer with Pi-compatible `role="custom"` and `customType`.
- Added `SessionStore.append_custom_entry()` / `appendCustomEntry()`.
- Added `SessionStore.append_custom_message_entry()` / `appendCustomMessageEntry()`.
- `SessionStore.build_context()` now reconstructs `custom_message` entries as custom agent messages.
- `AgentSession.send_custom_message()` / `sendCustomMessage()` now supports direct append, trigger-turn, streaming steer/follow-up, and `deliverAs="nextTurn"`.
- `AgentSession` now persists `message_end` events with `role="custom"` as `custom_message` JSONL entries.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "custom_entries or custom_message_next_turn"
```

Result: `2 passed, 54 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `56 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `177 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/agent_session.py appV2.2/appv22/coding_agent/session_store.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps are package-manager-backed resource discovery, full skill/prompt-template/theme loading, and default model-generated branch summary generation. The full plan checklist still has 0 unchecked items.
