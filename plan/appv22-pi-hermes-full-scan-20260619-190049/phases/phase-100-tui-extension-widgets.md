# Phase 100 - TUI Extension Widgets

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setWidget(key, content, options?)`, render keyed widgets around the editor, support above/below placement, and clear/replacement behavior.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setWidget(key, content, options?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` stores `extensionWidgetsAbove` and `extensionWidgetsBelow`, removes existing widgets from both placements before setting, renders widget containers around the editor, defaults placement to `aboveEditor`, supports `belowEditor`, clears on `undefined`, and caps string-array widgets at `MAX_WIDGET_LINES`.
- `pi/packages/coding-agent/docs/extensions.md` documents string-array widgets, `placement: "belowEditor"`, component factories, and clearing.
- `pi/packages/coding-agent/examples/extensions/widget-placement.ts` demonstrates above/below editor widgets.

## Regression

Added `test_interactive_mode_extension_shortcut_can_set_and_clear_widgets`.

The test first failed because appv22's extension shortcut UI did not expose `setWidget()` and no widget lines were rendered:

```text
ValueError: 'Above editor widget' is not in list
```

## Implementation

- Added keyed `extension_widgets_above` and `extension_widgets_below` maps to `InteractiveMode`.
- Added dedicated `widget_container_above`, `editor_container`, and `widget_container_below` containers to mirror Pi's editor-surrounding widget layout.
- Moved the live prompt `Input` from chat history into `editor_container`, preserving submitted prompts as normal user-message history.
- Added `InteractiveMode.set_extension_widget(key, content, options=None)`.
- Added `_ExtensionShortcutUI.setWidget()` / `set_widget()`.
- Added string-array widget rendering with a ten-line cap and truncation notice.
- Added component/factory handling for Python-native widgets while keeping list/string behavior aligned with Pi's documented extension API.
- Added replacement and clearing semantics that remove existing keyed widgets from both above and below placements before rendering the new state.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_set_and_clear_widgets -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut" -q
```

Result: `12 passed, 30 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "real_prompt_loop or keeps_agent_output_above_status_footer or footer_status_diff" -q
```

Result: `3 passed, 39 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `237 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially remaining extension UI hooks (`setFooter`, `setHeader`, editor/custom component hooks), provider auth/model validation, and final full-audit proof before the overall goal can be marked complete.
