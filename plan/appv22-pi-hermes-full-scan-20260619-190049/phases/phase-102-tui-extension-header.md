# Phase 102 - TUI Extension Header

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setHeader(factory?)`, replace the built-in startup header with an extension component, dispose prior custom headers, and restore the built-in header when cleared.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setHeader(factory?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` stores a `headerContainer`, `builtInHeader`, and `customHeader`; custom headers replace the current header component and clearing restores the built-in header.
- `pi/packages/coding-agent/examples/extensions/custom-header.ts` demonstrates replacing the startup header from an extension.

## Regression

Added `test_interactive_mode_extension_shortcut_can_replace_and_restore_header`.

The test first failed because appv22's extension shortcut UI did not expose `setHeader()` and the built-in startup header stayed rendered:

```text
AssertionError: assert 'custom header' in rendered
```

## Implementation

- Added `built_in_header`, `header_container`, and `custom_header` state to `InteractiveMode`.
- Moved the startup header text into `header_container`, preserving the existing spacer below it.
- Added `InteractiveMode.set_extension_header(factory=None)`.
- Added `_ExtensionShortcutUI.setHeader()` / `set_header()`.
- Custom header factories receive `(tui, theme)` and can return normal appv22 components or component-like objects with `render()`.
- Existing custom headers are disposed before replacement or restoration.
- Clearing with `None` restores the built-in startup header.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_replace_and_restore_header -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut" -q
```

Result: `14 passed, 30 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "real_prompt_loop or keeps_agent_output_above_status_footer or renders_existing_special_messages or footer" -q
```

Result: `8 passed, 36 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `239 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially remaining extension UI hooks (editor/custom component hooks), provider auth/model validation, and final full-audit proof before the overall goal can be marked complete.
