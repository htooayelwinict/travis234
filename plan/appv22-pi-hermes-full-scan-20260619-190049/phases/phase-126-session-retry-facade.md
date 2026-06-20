# Phase 126 - Session Retry Facade

## Goal

Port Pi's public auto-retry session facade and make appv22's retry backoff abortable in the same location as Pi's `_prepareRetry` flow.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `appV2.2/appv22/coding_agent/agent_session.py`

Key Pi behaviors covered in this slice:

- `abortRetry()` cancels an in-progress retry delay.
- `isRetrying` reports whether an auto-retry wait is active.
- `autoRetryEnabled` reports the current retry setting.
- `setAutoRetryEnabled(enabled)` toggles the retry setting.
- The failed assistant message is removed from active agent state before the retry continuation.

## Protected Compaction Note

No compaction implementation, threshold, timing, manual compression, or automatic compression logic changed in this phase. Retry and compaction-adjacent regression subsets were run to check this did not disturb the protected Hermes compaction behavior.

## Regression

Added:

- `test_agent_session_auto_retry_facade_toggles_retry_setting`
- `test_agent_session_abort_retry_cancels_retry_delay`

The tests first failed with:

```text
AttributeError: 'AgentSession' object has no attribute 'auto_retry_enabled'
AttributeError: 'AgentSession' object has no attribute 'is_retrying'
```

## Implementation

- Added `auto_retry_enabled`/`autoRetryEnabled`.
- Added `set_auto_retry_enabled()`/`setAutoRetryEnabled()`.
- Added `is_retrying`/`isRetrying`.
- Added `abort_retry()`/`abortRetry()`.
- Added a retry abort signal owned by the auto-retry wait.
- Moved retry backoff waiting and failed-assistant removal into `_prepare_retry`, matching the Pi design.
- Emitted `auto_retry_end` with `final_error="Retry cancelled"` when the retry wait is aborted.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'auto_retry_facade or abort_retry_cancels_retry_delay' -q
```

Result after implementation: `2 passed, 108 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'auto_retry or retry_attempt or abort_retry' -q
```

Result: `4 passed, 106 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'compaction or retry' -q
```

Result: `7 passed, 103 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `108 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `280 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes Pi's retry facade and abortable retry-wait behavior. Remaining likely slices include export helpers, replaced-session context details, additional runtime/extension APIs, and live TUI rendering checks while preserving the current Hermes compaction behavior.
