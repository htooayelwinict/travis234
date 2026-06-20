# Phase 090 - TUI Extension Status

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setStatus(key, text)` and render the status in the footer/status bar.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setStatus(key, text)` for footer/status-bar text.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` wires `setStatus` to `setExtensionStatus()`.

## Regression

Added `test_interactive_mode_extension_shortcut_can_set_footer_status`.

The test first failed because the shortcut handler could not make the footer render `ext: ready`:

```text
AssertionError: assert 'ext: ready' in rendered
```

## Implementation

- Added `extension_statuses` support to `FooterComponent`.
- Added `InteractiveMode.extension_statuses` storage.
- Added `InteractiveMode.set_extension_status()`.
- Added `_ExtensionShortcutUI.setStatus()` / `set_status()`.
- Footer rendering now includes sorted extension statuses alongside model, thinking level, context usage, compaction count, pending count, and cwd.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_set_footer_status -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or status"
```

Result: `5 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `227 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (`select`, `confirm`, `input`, `setWorkingMessage`, widgets/header/footer), provider auth/model validation, and richer TUI/runtime surfaces.
