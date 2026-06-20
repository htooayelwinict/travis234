# Phase 005: Listener Signal and Idle Settlement

Status: complete

## Reference

- `pi/packages/agent/src/agent.ts`

## Appv22 Files

- `appV2.2/appv22/agent/agent.py`
- `appV2.2/tests/test_agent_loop.py`

## Result

Added regressions for `wait_for_idle()` waiting until an `agent_end` listener settles and for Pi-style two-argument listeners receiving the active abort signal. Implemented a per-run idle event and arity-aware listener dispatch while preserving existing one-argument appv22 subscribers.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
```

Result: passed, `13 passed`.
