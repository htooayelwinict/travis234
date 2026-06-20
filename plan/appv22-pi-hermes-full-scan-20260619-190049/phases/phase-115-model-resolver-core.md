# Phase 115 - Model Resolver Core

## Goal

Port Pi's core model resolver helpers into appv22 so model references, thinking suffixes, custom provider model ids, and initial default selection follow the same rules.

## Reference

- `pi/packages/coding-agent/src/core/model-resolver.ts`
- `pi/packages/coding-agent/test/model-resolver.test.ts`

Key Pi behaviors covered in this slice:

- Exact model references support canonical `provider/modelId` strings and raw model IDs.
- Bare model IDs are rejected when ambiguous across providers.
- Provider/model splitting is preferred when the prefix is a known provider, but OpenRouter-style raw IDs with slashes/colons can still resolve.
- `parseModelPattern()` preserves model IDs containing colons and only strips valid thinking suffixes.
- Invalid thinking suffixes warn and fall back in scope-style parsing.
- CLI model resolution can build a custom provider model from the provider's base model and strip a valid `:thinking` suffix for custom IDs.
- Initial model selection prioritizes CLI, scoped models, saved defaults, then available models with known provider defaults.

## Regression

Added `tests/test_ai_model_resolver.py` with coverage for:

- `find_exact_model_reference_match()`
- `parse_model_pattern()`
- `resolve_cli_model()`
- `find_initial_model()`

The test file first failed with:

```text
ModuleNotFoundError: No module named 'appv22.ai.model_resolver'
```

## Implementation

- Added `appv22.ai.model_resolver`.
- Added Pi-shaped result dataclasses: `ScopedModel`, `ParsedModelResult`, `ResolveCliModelResult`, and `InitialModelResult`.
- Ported exact reference matching, alias-vs-dated partial matching, thinking-level parsing, CLI provider inference/custom fallback, and initial model selection priority.
- Reused the Phase 114 `DEFAULT_MODEL_PER_PROVIDER` map for known-provider default selection.
- Exported the resolver dataclasses and helpers from `appv22.ai`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_model_resolver.py
```

Result: `5 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_model_resolver.py tests/test_ai_env_config.py tests/test_ai_models.py
```

Result: `18 passed`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `259 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase ports the core model resolver helper behavior, but appv22 still needs future audit passes for resolver integration into richer CLI/model-scope inputs, OAuth callback-server/manual redirect UX, richer provider/extension hooks, runtime-host session switching details, and live compaction/TUI confidence checks.
