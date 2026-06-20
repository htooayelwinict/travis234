# Phase 139: Pi Export Image Rendering and Modal

## Goal

Bring appv22's exported HTML image rendering closer to Pi's browser-shell export design by rendering persisted image content blocks as visible image elements and wiring the existing image-modal shell.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.html`
  - `#image-modal`
  - `#modal-image`
- `pi/packages/coding-agent/src/core/export-html/template.js`
  - user-message image extraction from `content`
  - `.message-images`
  - `.message-image`
- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `.message-images`
  - `.message-image`

## Red Test

Added `test_agent_session_export_to_html_renders_image_blocks_with_modal_wiring`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_image_blocks_with_modal_wiring -q
```

Result: failed as expected because the exported HTML did not include `function renderMessageImages(content)`.

## Implementation

- Added `ImageContent` to the export regression fixture and verified persisted session data still serializes image blocks as Pi-shaped `{"type": "image", "data": ..., "mimeType": ...}`.
- Added exported HTML CSS for:
  - `.message-images`
  - `.message-image`
  - `.image-modal`
  - `.image-modal.open`
  - `.image-modal img`
- Added browser-side helpers:
  - `contentTextOnly(content)`
  - `renderMessageImages(content)`
  - `openImageModal(src)`
  - `closeImageModal()`
- Updated user-message rendering so image blocks render as `<img class="message-image">` elements and only text blocks flow through markdown rendering.
- Wired `.message-image` click handlers after each rendered navigation path to open the existing modal shell.
- Wired modal click handling to close and clear the modal image.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_image_blocks_with_modal_wiring -q` -> `1 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file' -q` -> `9 passed, 118 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `125 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `298 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Full Pi export template parity remains incomplete.
- Sidebar resizing, keyboard shortcuts, richer tool rendering, and full exported navigation caching still need dedicated parity slices.
- Live TUI ergonomics still need dedicated parity slices.
- The full appv22 Pi/Hermes objective remains active and unproven.
