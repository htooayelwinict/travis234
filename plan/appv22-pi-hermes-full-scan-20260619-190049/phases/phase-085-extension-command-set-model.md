# Phase 085 - Extension Command Set Model

## Goal

Port the next verified Pi extension command-context model gap into appv22 without importing Pi modules: command handlers must be able to call `setModel(model)` and receive a boolean success result.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts` binds command-context `setModel` to session `setModel()`, returning `false` if provider auth is not configured and `true` after a successful model switch.
- `pi/packages/coding-agent/src/core/extensions/types.ts` exposes `setModel(model): Promise<boolean>` on the extension command/context surface.

## Regression

Added `test_agent_session_extension_command_context_can_set_model`.

The test first failed because the appv22 command context did not expose `setModel()`:

```text
AttributeError: 'ExtensionCommandContext' object has no attribute 'setModel'
```

## Implementation

- Added `ExtensionCommandContext.setModel()` / `set_model()`.
- Wired the command context through `AgentSession._extension_set_model()`.
- Reused `AgentSession.set_model()` so model state changes continue to use the same session persistence and thinking-level behavior as existing appv22 model changes.
- Returned `True` on successful appv22 model switch.

## Scope Note

This phase ports the command-context API shape and successful model-change path. Full Pi provider-auth/model-registry validation (`hasConfiguredAuth`, provider registration, provider unregistration, and current-model refresh after registry changes) remains a separate parity slice.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_command_context_can_set_model -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or custom_entries_and_messages"
```

Result: `12 passed, 79 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `222 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially extension provider registration/auth behavior, extension UI hooks, and richer TUI/runtime surfaces.
