# Phase 094 - TUI Extension Input

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.input(title, placeholder?, opts?)` and return a user-entered value.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.input(title, placeholder?, opts?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` wires `input` through `createExtensionUIContext()` to `showExtensionInput()`.
- `pi/packages/coding-agent/docs/extensions.md` documents `const name = await ctx.ui.input("Name:", "placeholder")`, with `undefined` returned on timeout/cancel/abort.

## Regression

Added `test_interactive_mode_extension_shortcut_can_prompt_for_input`.

The test first failed because the shortcut UI context did not provide `input()`, so the handler failed and captured no value:

```text
AssertionError: assert [] == ['ported-ui']
```

## Implementation

- Added `InteractiveMode.prompt_extension_input(title, placeholder=None, options=None)`.
- Added `_ExtensionShortcutUI.input(title, placeholder=None, options=None)`.
- Added local already-aborted dialog signal handling for dict-shaped and object-shaped `signal.aborted`.
- The line-oriented TUI renders an `input:` prompt into history, calls `input_fn` with a title/placeholder prompt, echoes the submitted value into history, and returns it to the extension.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_prompt_for_input -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or working_message or working_status or working_indicator or prompt_for_input or status"
```

Result: `9 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `231 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (`select`, `confirm`, terminal input listeners, widgets/header/footer, hidden thinking label), provider auth/model validation, and richer TUI/runtime surfaces.
