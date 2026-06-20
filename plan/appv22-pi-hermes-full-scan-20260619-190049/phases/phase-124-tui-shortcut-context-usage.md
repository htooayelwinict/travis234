# Phase 124 - TUI Shortcut Context Usage

## Goal

Remove the appv22-only TUI shortcut context-usage shape and route extension shortcuts through the Pi-compatible `AgentSession.getContextUsage()` facade.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/tui/interactive-mode.ts`
- Phase 123 session stats/context-usage facade in appv22

Key Pi behavior covered in this slice:

- Extension shortcut context reads context usage from the session-level API.
- Context usage exposes the session facade shape: `tokens`, `contextWindow`, and `percent`.
- The TUI no longer exposes its own shortcut-only `threshold` context-usage shape.

## Protected Compaction Note

No compaction implementation, threshold, timing, manual compression, or automatic compression logic changed in this phase. The shortcut context now reads the existing session facade; Hermes dual-pass/timing compaction behavior remains protected.

## Regression

Extended:

- `test_interactive_mode_dispatches_extension_shortcut_without_model_turn`

The test first failed with the intended mismatch:

```text
AssertionError: assert {'tokens': 0, 'threshold': 16000} == {'tokens': 0, 'contextWindow': 1000, 'percent': 0.0}
```

## Implementation

- Changed `InteractiveMode._extension_shortcut_context()["getContextUsage"]` to call `self.app.session.get_context_usage`.
- Kept existing TUI run-loop token checks and compaction compressor behavior unchanged.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'dispatches_extension_shortcut_without_model_turn' -q
```

Result after implementation: `1 passed, 49 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'shortcut or context_usage or compact' -q
```

Result: `19 passed, 31 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'session_stats or context_usage' -q
```

Result: `2 passed, 105 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `277 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes the TUI shortcut context-usage mismatch. Remaining likely slices include more Pi `AgentSession` facade gaps, runtime/extension parity, and live TUI usability/rendering confidence checks while preserving the current Hermes compaction behavior.
