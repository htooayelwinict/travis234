# Phase 114 - Default Model Map

## Goal

Port Pi's current default-model map into appv22 and remove the appv22-only startup fallback model.

## Reference

- `pi/packages/coding-agent/src/core/model-resolver.ts` exports `defaultModelPerProvider` with current defaults such as OpenAI `gpt-5.4`, OpenRouter `moonshotai/kimi-k2.6`, ZAI `glm-5.1`, MiniMax `MiniMax-M2.7`, Cerebras `zai-glm-4.7`, Ant Ling `Ring-2.6-1T`, and Vercel AI Gateway `zai/glm-5.1`.
- `findInitialModel()` later uses that map when selecting a default from available models.
- appv22 startup previously defaulted env-backed OpenRouter runs to `xiaomi/mimo-v2.5-pro`, which is not Pi's current OpenRouter default.

## Regression

Added:

- `test_default_model_per_provider_tracks_pi_defaults`
- Updated `test_disabled_when_flag_absent` to expect the Pi OpenRouter default `moonshotai/kimi-k2.6`.

The focused tests first failed because appv22 had no `DEFAULT_MODEL_PER_PROVIDER` or `get_default_model_for_provider()` export.

## Implementation

- Added `DEFAULT_MODEL_PER_PROVIDER` to `appv22.ai.env_config` using Pi's current default-model table.
- Added `get_default_model_for_provider(provider)`.
- Changed `APPV2_WORKER_LLM` default model resolution to use the OpenRouter entry from the Pi map.
- Changed the CLI fallback to use `get_default_model_for_provider("openrouter")` instead of the old appv22-only Xiaomi model literal.
- Exported the default-model map/helper from `appv22.ai`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_env_config.py::test_disabled_when_flag_absent tests/test_ai_env_config.py::test_default_model_per_provider_tracks_pi_defaults
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_env_config.py tests/test_cli.py tests/test_ai_models.py
```

Result: `15 passed`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `254 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase does not claim the full Pi `findInitialModel()` resolver algorithm; remaining candidates include initial model selection priority, scoped model parsing, OAuth callback-server/manual redirect UX, richer provider/extension hooks, runtime-host session switching details, and live compaction/TUI confidence checks.
