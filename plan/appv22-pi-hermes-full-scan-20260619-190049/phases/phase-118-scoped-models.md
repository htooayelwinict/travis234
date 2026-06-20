# Phase 118 - Scoped Model Resolver and Cycling

## Goal

Port Pi's scoped model behavior into appv22 so `--models` patterns can define a scoped model list, choose the first scoped model on startup, and allow the session to cycle through scoped models with per-model thinking levels.

## Reference

- `pi/packages/coding-agent/src/core/model-resolver.ts`
- `pi/packages/coding-agent/src/main.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`

Key Pi behaviors covered in this slice:

- `resolveModelScope()` accepts comma-split model patterns from `--models`.
- Exact/fuzzy model patterns can include a valid `:<thinking>` suffix.
- Glob patterns match either `provider/modelId` or bare model id.
- Duplicate scoped models are suppressed.
- Unmatched scope patterns emit warnings.
- Startup with `--models` and no explicit `--model` selects the first scoped model and its thinking level.
- `AgentSession.cycle_model()` cycles within the scoped model list and applies the scoped thinking level.

## Protected Compaction Note

No compaction implementation was changed in this phase. The existing Hermes dual-pass/timing compaction layer remains protected; future compaction edits should be limited to direct Hermes/Pi parity fixes with regression tests.

## Regression

Added regressions for:

- `resolve_model_scope()` pattern/glob/thinking/deduplication behavior.
- CLI `--models` startup selection and scoped model handoff.
- `AgentSession.cycle_model()` scoped model cycling with thinking-level update.

The resolver regression first failed with:

```text
ImportError: cannot import name 'resolve_model_scope' from 'appv22.ai.model_resolver'
```

The CLI `--models` regression first failed with:

```text
pytest: error: unrecognized arguments: --models inspect
```

The session scoped-cycle regression first failed with:

```text
TypeError: AgentSession.__init__() got an unexpected keyword argument 'scoped_models'
```

## Implementation

- Added `resolve_model_scope()` to `appv22.ai.model_resolver`.
- Exported `resolve_model_scope()` from `appv22.ai`.
- Added `--models` parsing and comma splitting in the CLI.
- Extended startup model selection to carry `scoped_models`.
- Passed scoped models through `CodingApp` into `AgentSession`.
- Added `AgentSession.scoped_models`, `set_scoped_models()`, and `cycle_model()`.
- Added `ModelCycleResult` with Pi-style camelCase aliases.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_model_resolver.py::test_resolve_model_scope_matches_patterns_globs_thinking_and_dedupes -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_cli.py::test_cli_models_flag_sets_scoped_models_and_initial_model -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_coding_agent.py::test_agent_session_cycles_scoped_models_with_thinking_levels -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_model_resolver.py tests/test_cli.py tests/test_app_integration.py tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `115 passed, 2 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `267 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes scoped model resolution/startup/cycling basics. Remaining likely slices include richer OAuth callback/manual redirect UX, remaining provider/extension parity, runtime-host details, available-model cycling fallback, and live TUI usability/rendering confidence checks.
