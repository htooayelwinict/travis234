# Phase 088 - Extension Provider Model Cleanup

## Goal

Port the next verified Pi provider-registry cleanup behavior into appv22 without importing Pi modules: `unregisterProvider()` removes extension-created provider models from the model registry.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` specifies that `unregisterProvider()` removes all models belonging to the named provider.
- Phase 086 added appv22 registration of Pi-style provider model dictionaries.
- Phase 087 restored active-model overrides but still left extension-created model registry entries behind.

## Regression

Added `test_agent_session_extension_unregister_provider_removes_extension_models`.

The test first failed because `unregisterProvider("proxy")` did not remove the extension-created `proxy/proxy-model` entry:

```text
AssertionError: assert Model(...) is None
```

## Implementation

- Added `appv22.ai.models.unregister_provider_models(provider)`.
- Exported `unregister_provider_models` from `appv22.ai`.
- Called `unregister_provider_models(name)` from `AgentSession._unregister_extension_provider()`.

## Scope Note

This phase removes extension-created provider models. Built-in model restoration for overridden providers, provider auth storage, OAuth provider lifecycle, and `hasConfiguredAuth()` validation remain separate parity slices.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_unregister_provider_removes_extension_models -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or provider_override or custom_entries_and_messages"
```

Result: `14 passed, 80 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `225 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially built-in provider restoration, provider auth/model validation, extension UI hooks, and richer TUI/runtime surfaces.
