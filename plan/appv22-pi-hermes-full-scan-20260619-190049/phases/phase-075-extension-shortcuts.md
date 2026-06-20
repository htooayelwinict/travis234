# Phase 075: Extension Shortcuts

## Goal

Port the core Pi coding-agent extension shortcut registration surface into appv22 so extensions can register keyboard shortcuts and retrieve normalized shortcut definitions.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/coding-agent/src/core/extensions/loader.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/test/extensions-runner.test.ts`

Pi exposes `registerShortcut()` and `getShortcuts()`. The full Pi runner also handles built-in collision diagnostics; this phase ports the core extension registry and normalized lookup behavior used by the TUI handoff path.

## Changes

- Added `ExtensionShortcut`.
- Added `ExtensionRunner.register_shortcut()` / `registerShortcut()`.
- Added `get_shortcuts()` / `getShortcuts()`.
- Normalized shortcut keys to lowercase and kept Pi's later-extension override behavior for duplicate extension shortcut keys.
- Exported `ExtensionShortcut` from `appv22.coding_agent`.
- Added a regression for shortcut normalization, duplicate override, and handler access.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_extension_runner_shortcut_registration_normalizes_and_overrides -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "shortcut_registration or message_renderer or flag_registration or lifecycle_handlers or extension_command or provider_extension_hooks or tool_call_extension or context_extension"
```

Result: `10 passed, 73 deselected`.
