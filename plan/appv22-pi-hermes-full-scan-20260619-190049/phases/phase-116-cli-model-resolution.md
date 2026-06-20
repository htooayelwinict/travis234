# Phase 116 - CLI Model Resolution

## Goal

Wire the Phase 115 Pi-style model resolver into appv22's CLI entry point so runtime startup supports the same `--provider <name> --model <pattern>` path instead of only constructing an OpenRouter model from env defaults.

## Reference

- `pi/packages/coding-agent/src/cli/args.ts`
- `pi/packages/coding-agent/src/main.ts`
- `pi/packages/coding-agent/src/core/model-resolver.ts`

Key Pi behaviors covered in this slice:

- CLI accepts `--provider`.
- CLI accepts `--model`.
- CLI resolves the selected model through `resolveCliModel()`-style logic.
- Registered model definitions win over the env-only OpenRouter fallback.
- Resolver warnings are surfaced on stderr; resolver errors fail through argparse.

## Protected Compaction Note

No compaction implementation was changed in this phase. The current Hermes dual-pass/timing compaction layer remains protected; future compaction edits should be limited to direct Hermes/Pi parity fixes with regression tests.

## Regression

Added `test_cli_provider_and_model_flags_resolve_registered_model`.

The test first failed with:

```text
pytest: error: unrecognized arguments: --provider --model qwen/qwen3-coder:exacto inspect
```

## Implementation

- Added CLI flags `--provider` and `--model`.
- Added a small appv22 CLI model-registry adapter over registered models plus the env-derived OpenRouter fallback.
- Updated `_model_from_env()` to use `resolve_cli_model()` when `--model` is present and preserve existing env/default behavior when it is not.
- Kept the change scoped to startup model selection; no TUI rendering or compaction code was edited.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_cli.py::test_cli_provider_and_model_flags_resolve_registered_model -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_cli.py -q
```

Result: `3 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_cli.py tests/test_ai_model_resolver.py tests/test_ai_env_config.py tests/test_ai_models.py -q
```

Result: `21 passed`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `260 passed, 2 deselected`.

```bash
git diff --check -- appV2.2/appv22/cli.py appV2.2/tests/test_cli.py plan/appv22-pi-hermes-full-scan-20260619-190049/README.md plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md plan/appv22-pi-hermes-full-scan-20260619-190049/phases/phase-116-cli-model-resolution.md
```

Result: passed.

## Remaining Count

The full goal remains active. This phase reduces one CLI/model startup mismatch, but appv22 still needs future audit passes for `--thinking`/model-scope cycling, richer OAuth callback/manual redirect UX, remaining provider/extension hooks, runtime-host details, and live TUI usability checks. Compaction should remain unchanged unless a direct Hermes/Pi parity gap is proven.
