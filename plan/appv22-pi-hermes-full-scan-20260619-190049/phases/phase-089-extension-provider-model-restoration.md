# Phase 089 - Extension Provider Model Restoration

## Goal

Port the next Pi provider-registry restoration behavior into appv22 without importing Pi modules: when an extension replaces an existing provider's model list, `unregisterProvider()` restores the pre-existing models.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` specifies that `unregisterProvider()` restores built-in models that were overridden by extension provider registration.
- Pi provider registration treats `models` as a provider replacement, not an append-only list.
- Phase 088 removed extension-created provider models, but did not restore pre-existing registered models after replacement.

## Regression

Added `test_agent_session_extension_unregister_provider_restores_existing_models`.

The test first failed before unregister because extension registration appended the override model instead of replacing the provider model list:

```text
AssertionError: assert ['original-model', 'override-model'] == ['override-model']
```

## Implementation

- Added `appv22.ai.models.set_provider_models(provider, models)`.
- Exported `set_provider_models` from `appv22.ai`.
- Added `AgentSession._extension_provider_original_registry` to snapshot a provider's model list before the first extension replacement.
- Changed provider registration with `models` to replace the provider's model list via `set_provider_models()`.
- Changed provider unregister to restore the saved registry snapshot when one exists, otherwise remove extension-created models.

## Scope Note

This phase covers provider model-list replacement/restoration. Provider auth storage, OAuth provider lifecycle, and `hasConfiguredAuth()` validation remain separate parity slices.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_unregister_provider_restores_existing_models -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or provider_override or custom_entries_and_messages"
```

Result: `14 passed, 81 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_ai_models.py tests/test_coding_agent.py::test_agent_session_extension_unregister_provider_restores_existing_models tests/test_coding_agent.py::test_agent_session_extension_unregister_provider_removes_extension_models -q
```

Result: `5 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `226 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially provider auth/model validation, OAuth/auth storage, extension UI hooks, and richer TUI/runtime surfaces.
