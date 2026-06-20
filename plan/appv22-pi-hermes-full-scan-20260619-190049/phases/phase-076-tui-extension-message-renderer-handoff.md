# Phase 076: TUI Extension Message Renderer Handoff

## Status

Complete.

## Reality Check

Phase 074 ported the Pi-shaped extension message-renderer registry, but `InteractiveMode` still rendered existing custom session messages through `message_to_component(message)` without passing the registered renderer map. That meant extension renderers existed but were not used when reopening/rendering session history in the TUI.

## Changes

- Added a regression proving an extension-registered renderer for `custom_type="context"` controls existing custom-message history rendering.
- Updated `InteractiveMode._populate_existing_history()` to pass `ExtensionRunner.get_message_renderers()` into `message_to_component()`.
- Kept the fallback renderer path unchanged when no extension runner or message renderers are present.

## Verification

- Red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_uses_extension_custom_message_renderer -q`
  - Failed because `"custom rendered: Extension-provided context"` was absent and fallback `[context]` rendering was used.
- Green focused tests:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_uses_extension_custom_message_renderer -q`
  - `1 passed`
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "interactive_mode or custom_message"`
  - `10 passed, 20 deselected`
