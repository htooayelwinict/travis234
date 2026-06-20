# Phase 002: Per-Run Abort Signal

Status: complete

## Reference

- `pi/packages/agent/src/agent.ts`

## Appv22 Files

- `appV2.2/appv22/agent/agent.py`
- `appV2.2/tests/test_agent_loop.py`

## Result

Added a regression that aborts one active prompt, completes it, then starts a later prompt with a tool call. The tool now receives a fresh, non-aborted signal. Implemented the fix by creating a new `AbortSignal` at the start of each accepted run.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_agent_rejects_prompt_while_streaming tests/test_agent_loop.py::test_agent_abort_signal_is_fresh_for_next_prompt -q
```

Result: passed.
