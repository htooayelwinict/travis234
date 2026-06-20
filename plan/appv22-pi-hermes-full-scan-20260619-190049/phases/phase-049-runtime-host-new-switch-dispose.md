# Phase 049: Runtime Host New Switch Dispose

Status: complete

## Goal

Port the next verified Pi runtime-host slice: owning the current `AgentSession`, replacing it through a factory, honoring `session_before_switch` cancellation, emitting `session_shutdown`, emitting replacement `session_start` metadata, and invalidating/disposal hooks.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session-runtime.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`

## Changes

- Added `appv22.coding_agent.agent_session_runtime`.
- Added `CreateAgentSessionRuntimeResult` and `AgentSessionRuntime`.
- Implemented `set_rebind_session()` / `setRebindSession()`.
- Implemented `set_before_session_invalidate()` / `setBeforeSessionInvalidate()`.
- Implemented `new_session()` / `newSession()` with before-switch cancellation, target session file creation, shutdown emission, runtime factory application, and replacement `session_start` reason `new`.
- Implemented `switch_session()` / `switchSession()` with before-switch cancellation, shutdown emission, runtime factory application, and replacement `session_start` reason `resume`.
- Implemented `dispose()` with `session_shutdown` reason `quit`, before-invalidate callback, and session disposal.
- Added `AgentSession.dispose()` plus `session_file` / `sessionFile` aliases.
- Exported runtime host types from `appv22.coding_agent`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "runtime_replaces_sessions"
```

Result: `1 passed, 49 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `50 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `171 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/agent_session_runtime.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/appv22/coding_agent/__init__.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps are fork/import/tree runtime flows, package-manager-backed resource discovery, full skill/prompt-template/theme loading, labels/custom entries, and branch summary generation. The full plan checklist still has 0 unchecked items.
