# Phase 031: Pi Agent Loop Prepare Snapshot

## Scope

Close the remaining Phase 2 `agent-loop.ts` simplification item by porting Pi's `prepareNextTurn` snapshot behavior into appv22.

## Reference

- `pi/packages/agent/src/agent-loop.ts`

## Changes

- Added `test_prepare_next_turn_snapshot_updates_loop_without_mutating_config`.
- Added `test_should_stop_after_turn_receives_prepare_next_turn_context_snapshot`.
- Replaced in-place mutation of `AgentLoopConfig.model` and `reasoning` with a loop-local `dataclasses.replace()` copy.
- Rebuilt the `should_stop_after_turn` callback context after applying a `prepare_next_turn` context snapshot.

## Red/Green Evidence

Red:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_prepare_next_turn_snapshot_updates_loop_without_mutating_config -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_should_stop_after_turn_receives_prepare_next_turn_context_snapshot -q
```

Results:

- The first test failed because `cfg.model` was mutated to the snapshot model.
- The second test failed because `should_stop_after_turn` saw the original context instead of the snapshot context.

Green:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_prepare_next_turn_snapshot_updates_loop_without_mutating_config tests/test_agent_loop.py::test_should_stop_after_turn_receives_prepare_next_turn_context_snapshot -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q
```

Results:

- focused snapshot regressions: `2 passed`
- `tests/test_agent_loop.py`: `23 passed`

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Results:

- `tests`: `127 passed`
- `py_compile`: exit 0

## Remaining Work

- Phase 2 agent-loop checklist items are complete.
- Next planned area is Phase 3 coding-agent session parity.
