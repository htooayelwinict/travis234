# Phase 095 - TUI Extension Select

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.select(title, options, opts?)` and return the selected option string.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.select(title, options, opts?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` wires `select` through `createExtensionUIContext()` to `showExtensionSelector()`.
- `pi/packages/coding-agent/docs/extensions.md` documents `const choice = await ctx.ui.select("Pick one:", ["A", "B", "C"])`, with `undefined` returned on timeout/cancel/abort.

## Regression

Added `test_interactive_mode_extension_shortcut_can_select_option`.

The test first failed because the shortcut UI context did not provide `select()`, so the handler failed and captured no value:

```text
AssertionError: assert [] == ['production']
```

## Implementation

- Added `InteractiveMode.prompt_extension_select(title, choices, options=None)`.
- Added `_ExtensionShortcutUI.select(title, options, dialog_options=None)`.
- Reused the extension dialog abort helper from the input hook.
- The line-oriented TUI renders a `select:` prompt with numbered choices, accepts 1-based numeric input or an exact option label, echoes the selected option into history, and returns that option string.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_select_option -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or working_message or working_status or working_indicator or prompt_for_input or select_option or status"
```

Result: `10 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `232 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (`confirm`, terminal input listeners, widgets/header/footer, hidden thinking label), provider auth/model validation, and richer TUI/runtime surfaces.
