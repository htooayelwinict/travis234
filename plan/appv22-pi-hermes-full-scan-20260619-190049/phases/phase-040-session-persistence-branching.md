# Phase 040: Coding-Agent Session Persistence and Branching

## Goal

Port Pi-style session persistence and branch hooks into appv22 after the core event/session API became stable: JSONL header, typed entries, reload from active branch, and branch-to-entry behavior.

## Reference Files

- `pi/packages/coding-agent/src/core/session-manager.ts`
- `pi/packages/coding-agent/test/session-manager/tree-traversal.test.ts`
- `pi/packages/coding-agent/test/agent-session-branching.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-runtime.test.ts`

## Changes

- Added regressions for typed JSONL persistence and reload of message/session-info/thinking/model entries.
- Added branch regression proving new messages become children of the selected branch point and reload follows the latest leaf branch.
- Added `appv22.coding_agent.session_store.SessionStore` with Pi-shaped `session` headers, `CURRENT_SESSION_VERSION = 3`, typed entries, `id`/`parentId`, active leaf tracking, branch traversal, and message serialization.
- Wired `AgentSession(session_path=...)` to restore messages, session name, and thinking level from the active branch.
- Persisted `message`, `session_info`, `thinking_level_change`, `model_change`, and `compaction` entries from existing session hooks.
- Added `AgentSession.session_entries`, `session_path`, and `branch(entry_id)`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "persists_and_reloads_typed_session_entries or branch_repoints_leaf"
```

Result: `2 passed, 41 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `43 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `153 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this phase, 3 plan checklist items remain open.
