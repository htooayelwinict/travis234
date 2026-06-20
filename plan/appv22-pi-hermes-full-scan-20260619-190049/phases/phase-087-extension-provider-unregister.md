# Phase 087 - Extension Provider Unregister

## Goal

Port the next verified Pi provider-registry behavior into appv22 without importing Pi modules: `unregisterProvider()` must take effect immediately and restore the active model after a command-time provider override.

## Reference

- `pi/packages/coding-agent/src/core/extensions/runner.ts` routes `runtime.unregisterProvider()` to bound provider actions after core binding.
- `pi/packages/coding-agent/src/core/extensions/types.ts` specifies that `unregisterProvider()` removes a registered provider and restores built-in models that were overridden.
- Phase 086 added the matching appv22 command-time `registerProvider()` active-model override path.

## Regression

Added `test_agent_session_extension_command_can_unregister_provider_override`.

The test first failed because appv22's `_unregister_extension_provider()` was still a no-op:

```text
AssertionError: assert 'http://localhost:8080/command' == 'https://original.example.test'
```

## Implementation

- Added `AgentSession._extension_provider_original_models`, a session-local snapshot of active models before extension provider overrides.
- Recorded the original active model the first time a matching provider override is applied.
- Implemented `_unregister_extension_provider()` to restore the saved model when the current active model belongs to the unregistered provider.

## Scope Note

This phase restores the active session model for the command-time override path. Full Pi global model-registry cleanup for extension-created models, OAuth provider removal, auth storage, and `hasConfiguredAuth()` validation remain separate parity slices.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_can_unregister_provider_override -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or provider_override or custom_entries_and_messages"
```

Result: `14 passed, 79 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `224 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially global provider-registry cleanup, auth/model validation, extension UI hooks, and richer TUI/runtime surfaces.
