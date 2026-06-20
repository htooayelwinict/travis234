# Phase 046: Extension Lifecycle Handlers

Status: complete

## Goal

Port the next verified Pi coding-agent extension gap: lifecycle handler registration/emission, before-event cancellation semantics, shutdown helper behavior, and the AgentSession runner/start-event surface.

## Reference Files

- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/agent-session-runtime.ts`
- `pi/packages/coding-agent/src/core/sdk.ts`

## Changes

- Extended local `ExtensionRunner` with `on()`, `on_error()` / `onError()`, `emit_error()` / `emitError()`, `has_handlers()` / `hasHandlers()`, and `emit()`.
- Ported Pi before-event cancellation semantics for `session_before_switch`, `session_before_fork`, `session_before_compact`, and `session_before_tree`: keep the latest non-cancel result and stop immediately on `cancel: true`.
- Added `emit_session_shutdown_event()` plus camelCase alias `emitSessionShutdownEvent`.
- Exported `emit_session_shutdown_event` from `appv22.coding_agent`.
- Added `AgentSession.session_start_event` constructor/factory option and emit `session_start` at session construction, defaulting to `{type: "session_start", reason: "startup"}`.
- Added `AgentSession.extension_runner` / `extensionRunner` and `has_extension_handlers()` / `hasExtensionHandlers()`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "lifecycle_handlers or extension_runner_and_emits_session_start"
```

Result: `2 passed, 45 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `47 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `167 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/extensions.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/appv22/coding_agent/__init__.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps are resource-loader reload hooks and runtime-host session switching. The full plan checklist still has 0 unchecked items.
