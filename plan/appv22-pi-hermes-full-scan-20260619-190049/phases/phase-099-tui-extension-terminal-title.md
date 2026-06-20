# Phase 099 - TUI Extension Terminal Title

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setTitle(title)` and write the same terminal title OSC sequence as Pi.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setTitle(title)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` forwards `setTitle` to `this.ui.terminal.setTitle(title)`.
- `pi/packages/tui/src/terminal.ts` writes `\x1b]0;${title}\x07` for terminal title updates.
- `pi/packages/tui/test/virtual-terminal.ts` mirrors the same OSC write in tests.

## Regression

Added `test_interactive_mode_extension_shortcut_can_set_terminal_title`.

The test first failed because appv22's extension shortcut UI did not expose `setTitle()` and no title OSC sequence was emitted:

```text
AssertionError: assert '\x1b]0;appv22 - workspace\x07' in terminal.output
```

## Implementation

- Added `Terminal.set_title(title)` to the appv22 terminal protocol.
- Added `FakeTerminal.set_title()` and `ProcessTerminal.set_title()` with the Pi OSC `0;title` sequence.
- Added camelCase `setTitle` aliases on terminal implementations for Pi-shaped direct calls.
- Added `InteractiveMode.set_terminal_title(title)`.
- Added `_ExtensionShortcutUI.setTitle()` / `set_title()` forwarding directly to the terminal.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_set_terminal_title -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut" -q
```

Result: `11 passed, 30 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `236 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially remaining extension UI hooks (`setWidget`, `setFooter`, `setHeader`, editor/custom component hooks), provider auth/model validation, and final full-audit proof before the overall goal can be marked complete.
