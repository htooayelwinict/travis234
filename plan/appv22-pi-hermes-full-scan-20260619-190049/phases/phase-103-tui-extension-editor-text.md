# Phase 103 - TUI Extension Editor Text Hooks

## Goal

Port the next Pi extension UI hooks into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setEditorText(text)`, `ui.getEditorText()`, and `ui.pasteToEditor(text)`, and those calls must control the core input editor.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `pasteToEditor(text)`, `setEditorText(text)`, and `getEditorText()`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` wires `pasteToEditor` to bracketed paste input, `setEditorText` to `editor.setText(text)`, and `getEditorText` to the current editor value.
- `pi/packages/coding-agent/docs/extensions.md` documents extension editor text prefill, readback, and paste behavior.

## Regression

Added `test_interactive_mode_extension_shortcut_can_control_editor_text`.

The test first failed because appv22's extension shortcut UI did not expose the editor methods, so the shortcut handler failed before recording the expected editor buffer:

```text
AssertionError: assert [] == ['prefill + pasted']
```

## Implementation

- Added persistent `editor_text` state to `InteractiveMode`.
- Added `active_editor` tracking while the line-oriented `Input` is visible.
- New prompt inputs are initialized with the persistent editor buffer.
- Non-shortcut prompt submission clears the buffer after the prompt is accepted.
- Shortcut dispatch leaves the buffer intact so extension-prefilled text can be submitted on the next prompt.
- Added `InteractiveMode.set_editor_text(text)`, `get_editor_text()`, and `paste_to_editor(text)`.
- Added `_ExtensionShortcutUI.setEditorText()` / `set_editor_text()`, `getEditorText()` / `get_editor_text()`, and `pasteToEditor()` / `paste_to_editor()`.
- Added bracketed paste handling to `Input.handle_input()` and made `Input.set_value()` move the cursor to the end of the new value.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_control_editor_text -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut" -q
```

Result: `15 passed, 30 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "input or real_prompt_loop or terminal_input or listener" -q
```

Result: `4 passed, 41 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `240 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially remaining extension UI hooks (custom components, multi-line editor, autocomplete), provider auth/model validation, and final full-audit proof before the overall goal can be marked complete.
