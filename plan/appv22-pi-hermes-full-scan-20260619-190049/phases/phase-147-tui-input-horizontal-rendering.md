# Phase 147: Pi TUI Input Horizontal Rendering

## Goal

Port Pi's single-line input render behavior into appv22 so focused long input scrolls around the cursor and displays Pi's inverse-video fake cursor instead of truncating from the beginning and losing the cursor marker.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/components/input.ts`
  - Visible input window follows the cursor.
  - End-of-line cursor reserves one column for the fake cursor.
  - Focused input emits `CURSOR_MARKER` before the fake cursor.
  - Fake cursor uses inverse video: `\x1b[7m...\x1b[27m`.

## Red Test

Added `test_input_render_scrolls_to_cursor_and_uses_pi_fake_cursor`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_render_scrolls_to_cursor_and_uses_pi_fake_cursor -q
```

Result: failed as expected because appv22 rendered `> abcdefghij`, truncating from the start and dropping `CURSOR_MARKER`.

## Implementation

- Replaced whole-line prompt/value truncation with a Pi-style visible input window.
- Kept appv22's configurable prompt while applying Pi's available-width calculation.
- Added focused cursor marker emission in the visible window.
- Added inverse-video fake cursor rendering.
- Added a local `_slice_by_column()` helper instead of importing from Pi.
- Updated the earlier cursor-marker TUI regression to assert marker stripping and plain text through `strip_ansi()` while allowing Pi fake-cursor styling.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_render_scrolls_to_cursor_and_uses_pi_fake_cursor -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `54 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `310 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, and richer editor behavior.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
