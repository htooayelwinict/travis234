# Phase 098 - TUI Extension Hidden Thinking Label

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setHiddenThinkingLabel(label?)`, and assistant message components must render the configured label when thinking blocks are hidden.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setHiddenThinkingLabel(label?)`.
- `pi/packages/coding-agent/src/modes/interactive/components/assistant-message.ts` stores `hiddenThinkingLabel`, supports `setHiddenThinkingLabel()`, and renders that label when `hideThinkingBlock` is enabled.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` applies label changes to existing assistant message components and the streaming component.

## Regression

Added `test_interactive_mode_extension_shortcut_can_set_hidden_thinking_label`.

The test first failed because the shortcut UI context did not expose `setHiddenThinkingLabel()` and existing assistant thinking content continued to render raw reasoning text:

```text
AssertionError: assert 'Reasoning hidden' in rendered
```

## Implementation

- Added `hide_thinking_block` and `hidden_thinking_label` state to `AssistantMessageComponent`.
- Added `AssistantMessageComponent.setHiddenThinkingLabel()` / `set_hidden_thinking_label()`.
- Added `AssistantMessageComponent.setHideThinkingBlock()` / `set_hide_thinking_block()`.
- Extended `message_to_component()` and `InteractiveRenderer` to pass and update hidden-thinking settings.
- Added `InteractiveMode.set_hidden_thinking_label(label=None)`.
- Added `_ExtensionShortcutUI.setHiddenThinkingLabel()` / `set_hidden_thinking_label()`.
- `InteractiveMode` now applies label changes recursively to existing history components and syncs future streaming assistant components through the renderer.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_set_hidden_thinking_label -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "thinking or shortcut or footer_status or working_message or working_status or working_indicator or prompt_for_input or select_option or can_confirm or terminal_input or status"
```

Result: `14 passed, 26 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `235 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (widgets/header/footer, title/editor/custom components), provider auth/model validation, and richer TUI/runtime surfaces.
