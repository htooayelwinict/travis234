# Phase 006: Failure Lifecycle

Status: complete

## Reference

- `pi/packages/agent/src/agent.ts`

## Appv22 Files

- `appV2.2/appv22/agent/agent.py`
- `appV2.2/tests/test_agent_loop.py`

## Result

Added a regression for provider/loop exceptions. Appv22 now catches unexpected run failures in `prompt()` and `continue_()`, emits a synthetic assistant failure message through `message_start`, `message_end`, `turn_end`, and `agent_end`, appends it to state, and returns it as the run result.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
```

Result: passed, `14 passed`.
