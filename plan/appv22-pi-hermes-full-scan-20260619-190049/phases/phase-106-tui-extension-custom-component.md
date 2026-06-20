# Phase 106 - TUI Extension Custom Component Hook

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.custom(factory, options?)`, temporarily replace the editor surface with the returned component, route input into it, return the value passed to `done(value)`, restore the editor surface, and dispose the component.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.custom(factory, options?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` implements `showExtensionCustom()`: save editor text, replace the editor container, focus the custom component, resolve on `done(value)`, restore the editor, and dispose the component.
- `pi/packages/coding-agent/docs/extensions.md` documents `ctx.ui.custom()` as a temporary focused custom component surface.
- `pi/packages/coding-agent/docs/tui.md` shows custom components calling `done()` from keyboard/input handlers.

## Regression

Added `test_interactive_mode_extension_shortcut_can_open_custom_component`.

The test first failed because appv22's extension shortcut UI did not expose `custom()`, so the shortcut handler never captured the returned custom result:

```text
AssertionError: assert [] == [{'accepted': True}]
```

## Implementation

- Added `InteractiveMode.prompt_extension_custom(factory, options=None)`.
- Factories are called with `(tui, None, None, done)` to mirror Pi's `(tui, theme, keybindings, done)` shape inside the Python line-oriented TUI.
- The editor container is temporarily replaced by the returned component and rendered before input is collected.
- Terminal input from the configured `input_fn` is routed to the component's `handle_input(data)` until `done(value)` closes it.
- Closing restores the previous editor container children and saved editor text.
- Closing returns the `done(value)` payload to the extension shortcut handler.
- Component `dispose()` is called after close and disposal errors are ignored, matching Pi's defensive cleanup.
- Added `_ExtensionShortcutUI.custom(factory, options=None)` forwarding.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_open_custom_component -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut or custom_component or autocomplete or input" -q
```

Result: `19 passed, 29 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `243 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially richer extension/provider hooks, provider auth/model validation, runtime-host session switching details, and final full-audit proof before the overall goal can be marked complete.
