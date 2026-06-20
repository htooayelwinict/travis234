# Phase 029: Pi Agent Loop Update Drain

## Scope

Port the remaining narrow Phase 2 event-ordering slice from Pi agent-loop/runtime semantics into appv22 without importing from `pi/`.

## Reference

- `pi/packages/agent/src/agent.ts`
- `pi/packages/agent/src/agent-loop.ts`

## Changes

- Added wrapper-level `Agent(..., prepare_next_turn=...)` support that calls the user callback with the active run `AbortSignal`.
- Changed `AgentEventSink` to allow a return value so event sinks can expose async-equivalent settlement handles.
- Stored return values from `tool_execution_update` emissions and settled future-like or event-like handles before `tool_execution_end`.
- Added regression coverage for update-drain ordering and active signal delivery to `prepare_next_turn`.

## Red/Green Evidence

Red:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_tool_execution_update_emit_settles_before_tool_execution_end -q
```

Result: failed before the drain because `tool_execution_end` was observed while the update future was still unresolved.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_agent_prepare_next_turn_receives_active_abort_signal -q
```

Result: failed before the wrapper hook because `Agent.__init__()` did not accept `prepare_next_turn`.

Green:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_tool_execution_update_emit_settles_before_tool_execution_end -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_agent_prepare_next_turn_receives_active_abort_signal -q
```

Result: both focused regressions passed after the port.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Results:

- `tests/test_agent_loop.py`: `18 passed`
- `tests`: `122 passed`
- `py_compile`: exit 0

## Remaining Phase 2 Work

- Sequential vs parallel tool ordering and terminate behavior.
- Replace appv22 loop simplifications that still diverge from Pi `agent-loop.ts`.
