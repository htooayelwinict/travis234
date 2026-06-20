# Phase 050: Runtime Host Fork

Status: complete

## Goal

Port Pi runtime-host fork semantics into appv22 without importing reference modules: cancelable `session_before_fork`, selected user text handling, branch-only JSONL session creation, shutdown/start lifecycle events, and active-session replacement.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session-runtime.ts`
- `pi/packages/coding-agent/src/core/session-manager.ts`
- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`

## Changes

- Added a regression proving runtime fork cancellation happens before entry validation.
- Implemented `AgentSessionRuntime.fork(entry_id, options=None)`.
- Ported `position: "before"` behavior: selected entry must be a user message, the fork target is its parent, and `selectedText` returns the user message text.
- Ported `position: "at"` behavior for targeting the selected entry directly.
- Added `SessionStore.create_branched_session()` to write a new JSONL session containing only the root-to-leaf path and a `parentSession` pointer to the source file.
- Added `SessionStore.get_entry()`, `AgentSession.get_session_entry()` / `getSessionEntry()`, and `AgentSession.create_branched_session()` / `createBranchedSession()`.
- Runtime fork now emits `session_shutdown` reason `fork`, creates replacement sessions with `session_start` reason `fork`, and runs rebind callbacks through the existing runtime replacement path.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "runtime_fork"
```

Result: `1 passed, 50 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `51 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `172 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/agent_session_runtime.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/appv22/coding_agent/session_store.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps are import/tree runtime flows, package-manager-backed resource discovery, full skill/prompt-template/theme loading, labels/custom entries, and branch summary generation. The full plan checklist still has 0 unchecked items.
