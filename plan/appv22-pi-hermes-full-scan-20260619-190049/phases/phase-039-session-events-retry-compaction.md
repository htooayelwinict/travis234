# Phase 039: Coding-Agent Session Events, Retry, and Compaction

## Goal

Port the remaining Pi-facing `AgentSession` event surface into appv22 without importing reference modules: compaction events, retry events, thinking-level/session-info events, model state changes, and `agent_end.willRetry` decoration.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/agent-session-compaction.test.ts`
- `pi/packages/coding-agent/test/agent-session-retry.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-retry-events.test.ts`
- `pi/packages/coding-agent/test/suite/regressions/3686-session-name-event.test.ts`

## Changes

- Added regressions for `session_info_changed` and `thinking_level_changed` events.
- Added a model state regression that proves appv22 does not invent a non-Pi session-listener model event.
- Added manual compaction event coverage for `compaction_start` and `compaction_end`.
- Added auto-retry coverage for transient provider errors and exhausted retry attempts.
- Added Pi-shaped event dataclasses with camelCase aliases: `followUp`, `willRetry`, `errorMessage`, `maxAttempts`, `delayMs`, and `finalError`.
- Added `set_session_name()`, `set_thinking_level()`, `set_model()`, `retry_attempt`, and manual `compact()`.
- Added bounded auto-retry behavior around real `AgentSession.prompt()` runs and retryable assistant `stop_reason="error"` messages.
- Decorated forwarded `agent_end` events with `willRetry`.
- Switched default tool wrapping to definition-first dynamic contexts so built-in tools can see the current session model.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "session_info_and_thinking or set_model_updates_state or manual_compaction_emits or auto_retry_events or auto_retry_exhaustion"
```

Result: `5 passed, 36 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `41 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `151 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this phase, 4 plan checklist items remain open.
