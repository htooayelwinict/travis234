# Phase 030: Pi Agent Loop Emit Settlement and Termination

## Scope

Port the next Phase 2 agent-loop ordering slice from Pi into appv22, covering normal event sink settlement in sequential and parallel tool execution plus terminate-batch coverage.

## Reference

- `pi/packages/agent/src/agent-loop.ts`

## Changes

- Added a local multi-tool assistant response helper for agent-loop regressions.
- Added `test_tool_execution_start_emit_settles_before_tool_runs` for both sequential and parallel execution modes.
- Added `test_all_terminating_parallel_tool_results_stop_without_next_assistant_turn` for the Pi terminate-batch rule.
- Added `_emit_event()` and `_settle_emit_result()` in `appv22/agent/agent_loop.py` so normal loop emissions settle future-like or event-like sink results before the loop advances.
- Kept `tool_execution_update` on the existing batch-drain path, matching Pi's `Promise.all(updateEvents)` behavior before `tool_execution_end`.

## Red/Green Evidence

Red:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_tool_execution_start_emit_settles_before_tool_runs -q
```

Result: failed before the port for both `sequential` and `parallel`; the first tool ran before the unresolved `tool_execution_start` future settled.

Green:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_tool_execution_start_emit_settles_before_tool_runs -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_all_terminating_parallel_tool_results_stop_without_next_assistant_turn -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
```

Results:

- start-settlement regression: `2 passed`
- terminate coverage: `1 passed`
- `tests/test_agent_loop.py`: `21 passed`

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Results:

- `tests`: `125 passed`
- `py_compile`: exit 0

## Remaining Phase 2 Work

- Replace remaining appv22 simplifications that still diverge from Pi `agent-loop.ts`.
