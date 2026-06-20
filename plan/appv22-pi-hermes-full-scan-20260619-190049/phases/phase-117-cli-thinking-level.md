# Phase 117 - CLI Thinking Level Startup

## Goal

Port Pi CLI startup thinking-level behavior into appv22 so initial sessions can start with a thinking level from either `--model <pattern>:<thinking>` or explicit `--thinking <level>`.

## Reference

- `pi/packages/coding-agent/src/cli/args.ts`
- `pi/packages/coding-agent/src/main.ts`
- `pi/packages/coding-agent/src/core/model-resolver.ts`

Key Pi behaviors covered in this slice:

- A valid thinking suffix in `--model` sets the initial session thinking level.
- Explicit `--thinking` takes precedence over a thinking suffix in `--model`.
- Invalid `--thinking` values warn and fall back to the default `off` level.
- `CodingApp` forwards startup thinking level into `AgentSession`.

## Protected Compaction Note

No compaction implementation was changed in this phase. The existing Hermes dual-pass/timing compaction layer remains protected; future compaction edits should be limited to direct Hermes/Pi parity fixes with regression tests.

## Regression

Added regressions for:

- `CodingApp(..., thinking_level="high")` initializes the underlying session with `high`.
- `--model anthropic/claude-sonnet-4-5:high` passes `high` to the app startup path.
- `--thinking high` overrides `--model ...:low`.
- Invalid `--thinking turbo` emits a warning and uses `off`.

The first app regression failed with:

```text
TypeError: CodingApp.__init__() got an unexpected keyword argument 'thinking_level'
```

The first CLI suffix regression failed with:

```text
TypeError: FakeApp.__init__() missing 1 required keyword-only argument: 'thinking_level'
```

The explicit `--thinking` regression failed with:

```text
pytest: error: unrecognized arguments: --thinking inspect
```

The invalid-level regression failed with:

```text
AssertionError: assert 'turbo' == 'off'
```

## Implementation

- Added `thinking_level` to `CodingApp.__init__()` and forwarded it to `AgentSession`.
- Added a startup model-selection result in the CLI that carries both `model` and optional `thinking_level`.
- Preserved Phase 116 model resolution and added thinking suffix propagation from `resolve_cli_model()`.
- Added `--thinking` parsing with Pi-compatible valid levels: `off`, `minimal`, `low`, `medium`, `high`, `xhigh`.
- Applied explicit `--thinking` precedence over model-pattern suffixes.
- Added invalid-level warning/fallback to keep bad values from entering the session state.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_cli.py::test_cli_model_thinking_suffix_sets_initial_thinking_level -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_app_integration.py::test_coding_app_forwards_initial_thinking_level_to_session -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_cli.py::test_cli_invalid_thinking_level_warns_and_uses_default tests/test_cli.py::test_cli_thinking_flag_overrides_model_suffix tests/test_cli.py::test_cli_model_thinking_suffix_sets_initial_thinking_level tests/test_app_integration.py::test_coding_app_forwards_initial_thinking_level_to_session -q
```

Result: `4 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_cli.py tests/test_app_integration.py tests/test_ai_model_resolver.py tests/test_ai_env_config.py tests/test_ai_models.py -q
```

Result: `29 passed`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `264 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes startup thinking-level parity for single-model CLI startup. Remaining likely slices include `--models` scoped model cycling, richer OAuth callback/manual redirect UX, remaining provider/extension parity, runtime-host details, and live TUI usability/rendering confidence checks.
