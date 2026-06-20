# Phase 091 - TUI Extension Working Message

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setWorkingMessage(message?)` and update the visible working/status row.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setWorkingMessage(message?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` updates the interactive loading/working message and resets to the default when called without a message.

## Regression

Added `test_interactive_mode_extension_shortcut_can_set_working_message`.

The test first failed because the shortcut UI context could not make the rendered status row show `status: Indexing workspace`:

```text
AssertionError: assert 'status: Indexing workspace' in rendered
```

## Implementation

- Added `InteractiveMode.default_working_message`.
- Added `InteractiveMode.set_working_message(message=None)`.
- Added `_ExtensionShortcutUI.setWorkingMessage()` / `set_working_message()`.
- The Python TUI maps the Pi working message onto its visible `StatusLine`, resetting to `Idle` when called without an argument.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_set_working_message -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or status or working_message"
```

Result: `6 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `228 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (`select`, `confirm`, `input`, working visibility/indicator, widgets/header/footer), provider auth/model validation, and richer TUI/runtime surfaces.
