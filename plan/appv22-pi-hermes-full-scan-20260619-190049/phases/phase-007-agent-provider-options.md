# Phase 007: Provider and Session Stream Options

Status: complete

## Reference

- `pi/packages/agent/src/agent.ts`
- `pi/packages/agent/src/agent-loop.ts`
- `pi/packages/ai/src/types.ts`

## Appv22 Files

- `appV2.2/appv22/agent/agent.py`
- `appV2.2/appv22/agent/agent_loop.py`
- `appV2.2/appv22/agent/types.py`
- `appV2.2/appv22/ai/types.py`
- `appV2.2/tests/test_agent_loop.py`

## Result

Added a regression for Pi-style provider/session runtime fields. Appv22 now accepts `session_id`, `transport`, `thinking_budgets`, `max_retry_delay_ms`, `on_payload`, and `on_response` on `Agent`, carries them through `AgentLoopConfig`, and forwards them to `SimpleStreamOptions`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
```

Result: passed, `15 passed`.
