# Phase 097 - TUI Extension Terminal Input

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.onTerminalInput(handler)` and return an unsubscribe callback.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `TerminalInputHandler = (data) => { consume?: boolean; data?: string } | undefined`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` wires `onTerminalInput` through `addExtensionTerminalInputListener()`.
- `pi/packages/tui/src/tui.ts` exposes `addInputListener(listener): () => void`.

## Regression

Added `test_interactive_mode_extension_shortcut_can_listen_to_terminal_input`.

The test first failed because the shortcut UI context did not provide a listener hook, so the next submitted line was not observed or rewritten:

```text
AssertionError: assert [] == ['rewrite']
```

## Implementation

- Added `InteractiveMode._terminal_input_listeners`.
- Added `InteractiveMode.add_terminal_input_listener(handler)` returning an unsubscribe callback.
- Added `InteractiveMode._dispatch_terminal_input(data)` before normal prompt handling.
- Added `_ExtensionShortcutUI.onTerminalInput()` / `on_terminal_input()`.
- The line-oriented TUI dispatches submitted input strings, supports returned `{"data": ...}` rewrites, supports returned `{"consume": True}` consumption, and preserves Pi's unsubscribe behavior.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_listen_to_terminal_input -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "shortcut or footer_status or working_message or working_status or working_indicator or prompt_for_input or select_option or can_confirm or terminal_input or status"
```

Result: `12 passed, 27 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `234 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially more extension UI hooks (widgets/header/footer, hidden thinking label), provider auth/model validation, and richer TUI/runtime surfaces.
