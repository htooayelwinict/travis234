# Phase 004: Queued Continue

Status: complete

## Reference

- `pi/packages/agent/src/agent.ts`
- `pi/packages/agent/test/agent.test.ts`

## Appv22 Files

- `appV2.2/appv22/agent/agent.py`
- `appV2.2/tests/test_agent_loop.py`

## Result

Added regressions for queued follow-up after an assistant turn and one-at-a-time steering from an assistant tail. Ported Pi's `PendingMessageQueue` drain behavior and updated `continue_()` to consume queued steering first, then queued follow-up, before rejecting assistant-tail continuation.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
```

Result: passed, `11 passed`.
