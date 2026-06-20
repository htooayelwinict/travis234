# Phase 001: Agent Active Run Guard

Status: complete

## Reference

- `pi/packages/agent/src/agent.ts`

## Appv22 Files

- `appV2.2/appv22/agent/agent.py`
- `appV2.2/tests/test_agent_loop.py`

## Result

Added a regression that holds the first prompt open and verifies a second prompt is rejected with the Pi-style "already processing" error. Implemented `_begin_run()` and `_finish_run()` around `prompt()` and `continue_()`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_agent_rejects_prompt_while_streaming -q
```

Result: passed.
