# Phase 142: Pi Export Rich Tool Bodies

## Goal

Extend the Phase 141 exported tool-call renderer toward Pi parity by porting additional built-in tool bodies for image-bearing read results, write line counts, edit diffs, and ls limit/output rendering.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `.tool-images`
  - `.tool-image`
- `pi/packages/coding-agent/src/core/export-html/template.js`
  - `getResultImages()`
  - `renderResultImages()`
  - read tool-result image rendering
  - write line-count metadata
  - edit diff rendering from `result.details.diff`
  - ls path/limit rendering

## Red Test

Added `test_agent_session_export_to_html_renders_edit_ls_write_and_tool_images`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_edit_ls_write_and_tool_images -q
```

Result: failed as expected because the exported HTML did not include `.tool-images`.

## Implementation

- Added Pi-shaped exported CSS for `.tool-images` and `.tool-image`.
- Added browser-side tool-result image helpers:
  - `getResultImages()`
  - `renderResultImages()`
- Rendered image blocks for read tool results before the text output.
- Added write tool line-count metadata for long writes and Pi-shaped compact result output.
- Added edit tool rendering with diff lines classified as added, removed, or context.
- Added ls tool rendering with path shortening, optional limit metadata, and expandable output.
- Renamed the local text-result helper to Pi's `getResultText()` shape while keeping existing fallback behavior.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_edit_ls_write_and_tool_images -q` -> `1 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_tool_calls_with_cached_navigation -q` -> `1 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file' -q` -> `12 passed, 118 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `128 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `301 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

If a future broad compaction fix is needed, it must stay within Pi/Hermes scope and be backed by dedicated compaction verification.

## Remaining Gaps

- Exported tool rendering still needs any remaining Pi-specific custom/built-in bodies not covered by bash/read/write/edit/ls and pre-rendered custom tools.
- Live TUI ergonomics still need dedicated parity slices.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
