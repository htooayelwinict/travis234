# Phase 084 - Extension Command Wait/Compact

## Goal

Port the next verified Pi extension command-context gap into appv22 without importing Pi modules: command handlers must be able to call `waitForIdle()` and trigger session compaction through Pi-style `compact({ customInstructions, onComplete, onError })`.

## Reference

- `pi/packages/coding-agent/src/core/extensions/runner.ts` adds `waitForIdle()` to `createCommandContext()`.
- `pi/packages/coding-agent/src/core/agent-session.ts` binds runtime `compact(options)` to `this.compact(options?.customInstructions)` and invokes completion/error callbacks.
- `pi/packages/coding-agent/src/core/compaction/compaction.ts` defines `CompactionResult` with `summary`, `firstKeptEntryId`, `tokensBefore`, and optional `details`.

## Regression

Added `test_agent_session_extension_command_context_can_wait_and_compact`.

The test first failed because `ExtensionCommandContext` had no `waitForIdle()` method:

```text
AttributeError: 'ExtensionCommandContext' object has no attribute 'waitForIdle'
```

## Implementation

- Added `ExtensionCompactionResult` with Pi-style camelCase aliases.
- Added `ExtensionCommandContext.waitForIdle()` / `wait_for_idle()`.
- Added `ExtensionCommandContext.compact()`.
- Wired command context `waitForIdle()` through `Agent.wait_for_idle()`.
- Wired command context `compact()` through the existing `AgentSession.compact()` / Hermes `CompactionManager` path.
- Adapted the callback payload from appv22's manual compression status into Pi's `CompactionResult` shape.
- Added local summary extraction that strips Hermes/appv22 summary transport prefix and end marker before returning the callback `summary`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_can_wait_and_compact -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"
```

Result: `11 passed, 79 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `221 passed`.

```bash
git diff --check -- appV2.2/appv22/coding_agent/agent_session.py appV2.2/tests/test_coding_agent.py
```

Result: passed.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially extension `setModel` provider-auth behavior, `registerProvider` / `unregisterProvider`, extension UI hooks, and richer TUI/runtime surfaces.
