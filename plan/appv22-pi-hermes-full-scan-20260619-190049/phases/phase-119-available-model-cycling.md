# Phase 119 - Available Model Cycling Fallback

## Goal

Port Pi's non-scoped model cycling fallback into appv22 so `AgentSession.cycle_model()` cycles through registered available models when no `--models` scoped list is active.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`

Key Pi behaviors covered in this slice:

- `cycleModel()` uses scoped models when a scoped list exists.
- If no scoped list exists, `cycleModel()` falls back to the available model registry.
- Cycling preserves the current thinking level during a normal model switch.
- The result marks fallback cycling as `isScoped: false`.

## Protected Compaction Note

No compaction implementation was changed in this phase. The existing Hermes dual-pass/timing compaction layer remains protected; future compaction edits should be limited to direct Hermes/Pi parity fixes with regression tests.

## Regression

Added `test_agent_session_cycles_registered_models_without_scoped_models`.

The test first failed with:

```text
AssertionError: assert None is not None
```

because `cycle_model()` returned `None` when `_scoped_models` was empty.

## Implementation

- Split `AgentSession.cycle_model()` into scoped and available-model branches.
- Added `_cycle_available_model()` over appv22's registered provider/model registry.
- Preserved current thinking level across available-model switches.
- Returned `ModelCycleResult(..., is_scoped=False)` for fallback cycling.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_coding_agent.py::test_agent_session_cycles_registered_models_without_scoped_models tests/test_coding_agent.py::test_agent_session_cycles_scoped_models_with_thinking_levels -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_coding_agent.py -k 'cycle_model or cycles_registered_models or cycles_scoped_models or set_model_updates_state or emits_session_info' -q
```

Result: `4 passed, 96 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_coding_agent.py tests/test_cli.py tests/test_ai_model_resolver.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `111 passed, 2 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `268 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes available-model cycling fallback basics. Remaining likely slices include richer OAuth callback/manual redirect UX, remaining provider/extension parity, runtime-host details, and live TUI usability/rendering confidence checks.
