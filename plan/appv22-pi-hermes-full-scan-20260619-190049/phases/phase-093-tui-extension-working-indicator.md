# Phase 093 - TUI Extension Working Indicator

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setWorkingIndicator(options?)` and update the built-in working/status row indicator.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setWorkingIndicator(options?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` wires `setWorkingIndicator` into the interactive extension UI context.
- `pi/packages/coding-agent/docs/extensions.md` documents configured frames, empty frames for hiding the indicator, and omitted options for restoring the default.

## Regression

Added `test_interactive_mode_extension_shortcut_can_set_working_indicator`.

The test first failed because `ui.setWorkingIndicator({"frames": ["*"]})` did not make the rendered working/status row include the configured indicator:

```text
AssertionError: assert 'status: * Indexing workspace' in rendered
```

## Implementation

- Added `StatusLine` message/indicator state so status message updates can preserve the configured indicator.
- Added `StatusLine.set_indicator()`.
- Added `InteractiveMode.set_working_indicator(options=None)`.
- Added `_ExtensionShortcutUI.setWorkingIndicator()` / `set_working_indicator()`.
- The Python TUI renders the first configured frame as a static indicator prefix and treats empty frames or omitted options as no indicator, matching the line-oriented TUI's current non-animated rendering model.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_set_working_indicator -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or working_message or working_status or working_indicator or status"
```

Result: `8 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `230 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (`select`, `confirm`, `input`, terminal input listeners, widgets/header/footer, hidden thinking label), provider auth/model validation, and richer TUI/runtime surfaces.
