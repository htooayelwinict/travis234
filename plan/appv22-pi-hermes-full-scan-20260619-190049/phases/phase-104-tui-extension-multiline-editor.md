# Phase 104 - TUI Extension Multi-line Editor Hook

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.editor(title, prefill?)`, and the Python line-oriented TUI must provide a practical multi-line editor equivalent with visible rendering and returned text.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines the extension UI `editor(title, prefill?)` hook.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` exposes `showExtensionEditor` from the interactive extension UI context.
- `pi/packages/coding-agent/src/modes/interactive/extension-editor.ts` owns the interactive extension editor surface.
- `pi/packages/coding-agent/docs/extensions.md` documents extension-driven editor prompts.

## Regression

Added `test_interactive_mode_extension_shortcut_can_open_multiline_editor`.

The test first failed because appv22's extension shortcut UI did not expose an `editor()` method, so the shortcut handler never captured the submitted editor text:

```text
AssertionError: assert [] == ['edited line 1\nedited line 2']
```

## Implementation

- Added `InteractiveMode.prompt_extension_editor(title, prefill=None)`.
- Rendered a visible `editor: <title>` status row before collecting editor input.
- Rendered optional prefill text into the TUI history.
- Used the configured `input_fn` to collect submitted text through the same testable terminal input path as other extension dialogs.
- Returned `None` when the input function indicates EOF/cancel-equivalent input loss.
- Recorded submitted text in history so extension editor activity is visible in the TUI transcript.
- Added `_ExtensionShortcutUI.editor(title, prefill=None)` / `ui.editor(...)` forwarding.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_open_multiline_editor -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut" -q
```

Result: `16 passed, 30 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "prompt_for_input or select_option or can_confirm or multiline_editor or input or real_prompt_loop" -q
```

Result: `7 passed, 39 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `241 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially remaining extension UI hooks (custom components, autocomplete), provider auth/model validation, runtime-host session switching details, and final full-audit proof before the overall goal can be marked complete.
