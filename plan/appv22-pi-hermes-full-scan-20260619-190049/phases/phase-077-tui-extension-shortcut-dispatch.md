# Phase 077: TUI Extension Shortcut Dispatch

## Status

Complete.

## Reality Check

Phase 075 ported the Pi-shaped `registerShortcut()` registry, but appv22's `InteractiveMode` did not consume registered shortcuts. Pi wires extension shortcuts into the interactive editor and executes matching handlers before normal prompt submission. Appv22's Python TUI is line-oriented, so the local port handles the equivalent available seam: exact submitted shortcut keys are intercepted before user-message rendering and provider dispatch.

## Changes

- Added a TUI regression proving a registered shortcut:
  - runs without calling the model,
  - receives a Pi-shaped TUI extension context,
  - can render a notification through `ctx["ui"].notify(...)`,
  - does not leave the shortcut key as a user prompt in history.
- Updated `InteractiveMode` to dispatch registered extension shortcuts before normal prompt handling.
- Added a minimal shortcut UI context with `notify()` and `showError()` / `show_error()` for local TUI feedback.
- Added context helpers for Pi-style fields such as `mode`, `hasUI`, `cwd`, `isIdle`, `hasPendingMessages`, `abort`, `compact`, `getContextUsage`, and `getSystemPrompt`.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_dispatches_extension_shortcut_without_model_turn -q`
  - Failed because the shortcut was treated as a normal prompt and the faux model was called.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_dispatches_extension_shortcut_without_model_turn -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "interactive_mode or shortcut"`
  - `11 passed, 20 deselected`
