# Phase 096 - TUI Extension Confirm

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.confirm(title, message, opts?)` and return a boolean confirmation result.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.confirm(title, message, opts?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` implements `showExtensionConfirm()` by showing a Yes/No selector and returning `result === "Yes"`.
- `pi/packages/coding-agent/docs/extensions.md` documents `const ok = await ctx.ui.confirm("Delete?", "This cannot be undone")`, with `false` for cancellation or timeout.

## Regression

Added `test_interactive_mode_extension_shortcut_can_confirm`.

The test first failed because the shortcut UI context did not provide `confirm()`, so the handler failed and captured no value:

```text
AssertionError: assert [] == [True]
```

## Implementation

- Added `InteractiveMode.prompt_extension_confirm(title, message, options=None)`.
- Added `_ExtensionShortcutUI.confirm(title, message, options=None)`.
- Reused the line-oriented select dialog with `Yes`/`No` options and a `confirm:` rendered status label.
- Added `_extension_dialog_label()` so multiline Pi dialog titles render as one clean prompt line in the Python TUI.
- The method returns `True` only for `Yes`; `No`, invalid input, EOF, or an already-aborted dialog signal return `False`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_confirm -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or working_message or working_status or working_indicator or prompt_for_input or select_option or can_confirm or status"
```

Result: `11 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `233 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (terminal input listeners, widgets/header/footer, hidden thinking label), provider auth/model validation, and richer TUI/runtime surfaces.
