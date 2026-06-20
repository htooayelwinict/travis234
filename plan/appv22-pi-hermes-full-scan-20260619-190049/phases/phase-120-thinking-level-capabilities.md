# Phase 120 - Thinking Level Capabilities

## Goal

Port Pi's model-aware thinking level capability helpers into appv22 so thinking levels are exposed, cycled, and clamped according to the active model's `reasoning` and `thinkingLevelMap` support.

## Reference

- `pi/packages/ai/src/models.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`

Key Pi behaviors covered in this slice:

- Non-reasoning models only support `off`.
- Reasoning models support `off`, `minimal`, `low`, `medium`, and `high` by default.
- `xhigh` is only available when explicitly present in `thinkingLevelMap`.
- `thinkingLevelMap` entries set to `null` remove that level from availability.
- Unsupported requested levels clamp to the nearest supported Pi level.
- `AgentSession` exposes `supportsThinking()`, `getAvailableThinkingLevels()`, and `cycleThinkingLevel()`.
- Model switches re-clamp inherited thinking levels against the new model.

## Protected Compaction Note

No compaction implementation was changed in this phase. The existing Hermes dual-pass/timing compaction layer remains protected; future compaction edits should be limited to direct Hermes/Pi parity fixes with regression tests.

## Regression

Added AI helper coverage:

- `test_get_supported_thinking_levels_matches_pi_reasoning_capabilities`
- `test_clamp_thinking_level_uses_nearest_supported_pi_level`

Added session helper coverage:

- `test_agent_session_thinking_level_helpers_follow_model_capabilities`
- `test_agent_session_thinking_level_helpers_disable_non_reasoning_cycle`

The focused tests first failed with missing helper/API errors:

```text
ImportError: cannot import name 'clamp_thinking_level'
AttributeError: 'AgentSession' object has no attribute 'supports_thinking'
```

## Implementation

- Added `get_supported_thinking_levels()` and `clamp_thinking_level()` to `appv22.ai.models`.
- Exported the new helpers from `appv22.ai`.
- Included `"off"` in the AI `ThinkingLevel` type alias.
- Added Pi-style `AgentSession` thinking helpers and camelCase aliases.
- Updated `set_thinking_level()`, `set_model()`, and model cycling to clamp through model capabilities.
- Adjusted existing tests that assumed unsupported `high` could persist on non-reasoning models.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_ai_models.py -k 'thinking_level' -q
```

Result: `2 passed, 9 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'thinking_level_helpers' -q
```

Result: `2 passed, 100 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `100 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_ai_models.py appV2.2/tests/test_cli.py appV2.2/tests/test_app_integration.py -q
```

Result: `23 passed`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `272 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes thinking-level capability helpers and model-switch clamping. Remaining likely slices include richer provider/extension parity, runtime-host details, and live TUI usability/rendering confidence checks while preserving the current Hermes compaction behavior.
