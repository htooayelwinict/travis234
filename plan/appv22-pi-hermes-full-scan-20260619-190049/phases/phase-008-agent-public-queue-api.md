# Phase 008: Public Queue API

Status: complete

## Reference

- `pi/packages/agent/src/agent.ts`

## Appv22 Files

- `appV2.2/appv22/agent/agent.py`
- `appV2.2/tests/test_agent_loop.py`

## Result

Added a regression for queue status, clearing, and mode properties. Appv22 now exposes `steering_mode`, `follow_up_mode`, `clear_steering_queue()`, `clear_follow_up_queue()`, `clear_all_queues()`, and `has_queued_messages()` matching the Pi queue surface in Python naming.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
```

Result: passed, `16 passed`.
