# Phase 092 - TUI Extension Working Visibility

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setWorkingVisible(visible)` and hide or show the built-in working/status row.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setWorkingVisible(visible)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` stores `workingVisible`, wires `setWorkingVisible`, and only renders the built-in working loader row when visible.
- `pi/packages/coding-agent/docs/extensions.md` documents `ctx.ui.setWorkingVisible(false)` as hiding the built-in working loader row entirely.

## Regression

Added `test_interactive_mode_extension_shortcut_can_hide_working_status`.

The test first failed because `ui.setWorkingVisible(false)` did not hide the rendered status row:

```text
AssertionError: assert 'status: Hidden extension status' not in rendered
```

## Implementation

- Added `StatusLine.visible`, `StatusLine.set_visible()`, and a render guard that returns no rows when hidden.
- Added `InteractiveMode.set_working_visible(visible)`.
- Added `_ExtensionShortcutUI.setWorkingVisible()` / `set_working_visible()`.
- Kept the footer visible while the working/status row is hidden, matching the Pi behavior of hiding only the built-in loader row.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_hide_working_status -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or working_message or working_status or status"
```

Result: `7 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `229 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (`select`, `confirm`, `input`, working indicator, widgets/header/footer), provider auth/model validation, and richer TUI/runtime surfaces.
