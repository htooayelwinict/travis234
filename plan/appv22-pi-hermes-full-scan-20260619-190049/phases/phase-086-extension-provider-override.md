# Phase 086 - Extension Provider Override

## Goal

Port the next verified Pi extension provider gap into appv22 without importing Pi modules: extension code can call `registerProvider()` during command execution and the active model is updated immediately without a reload.

## Reference

- `pi/packages/coding-agent/src/core/extensions/runner.ts` queues provider registrations before core binding, flushes them during `bindCore()`, and applies later `registerProvider()` calls immediately.
- `pi/packages/coding-agent/src/core/agent-session.ts` binds provider actions to `_modelRegistry.registerProvider()` and refreshes the current model from the registry.
- `pi/packages/coding-agent/test/agent-session-dynamic-provider.test.ts` verifies command-time provider overrides update the active model and the next provider call without reload.

## Regression

Added `test_agent_session_extension_command_can_register_provider_override_without_reload`.

The test first failed because appv22's `ExtensionRunner` had no Pi-style `registerProvider()` API:

```text
AttributeError: 'ExtensionRunner' object has no attribute 'registerProvider'
```

## Implementation

- Added `ExtensionRunner.registerProvider()` / `register_provider()`.
- Added `ExtensionRunner.unregisterProvider()` / `unregister_provider()` as an API-compatible hook.
- Added `ExtensionRunner.bindProviderActions()` / `bind_provider_actions()` with pending-registration flushing.
- Bound provider actions from `AgentSession` before `session_start`, so pre-bind and event-time registrations can affect the active session.
- Added `AgentSession._register_extension_provider()` to:
  - update the active model when the registered provider matches the current model provider,
  - convert Pi-style provider/model config dictionaries into appv22 `Model` entries,
  - register optional custom `streamSimple` handlers in the appv22 API provider registry.

## Scope Note

This phase covers command-time provider overrides and basic model registration. Full Pi `unregisterProvider()` built-in restoration, provider auth storage, OAuth, and `hasConfiguredAuth()` model validation remain separate parity slices.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_can_register_provider_override_without_reload -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or provider_override or custom_entries_and_messages"
```

Result: `13 passed, 79 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `223 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially `unregisterProvider()` restoration, provider auth/model validation, extension UI hooks, and richer TUI/runtime surfaces.
