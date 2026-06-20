# Phase 145: Pi TUI Cursor Marker Rendering

## Goal

Port Pi's TUI cursor-marker stripping behavior into appv22's live renderer so focused input components can emit the private `CURSOR_MARKER` sentinel without leaking it into terminal output.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/tui.ts`
  - `CURSOR_MARKER`
  - `extractCursorPosition(lines, height)`
  - TUI render path strips the marker before writing output and records the visible row/column.

## Red Test

Added `test_tui_strips_pi_cursor_marker_and_tracks_cursor_position`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_tui_strips_pi_cursor_marker_and_tracks_cursor_position -q
```

Result: failed as expected because appv22 wrote `\x1b_pi:c\x07` directly into terminal output.

## Implementation

- Imported appv22's existing `CURSOR_MARKER` into the TUI renderer.
- Added `_extract_cursor_position()` mirroring Pi's bottom-viewport marker scan.
- Stripped the marker before truncation, viewport selection, diffing, and terminal writes.
- Extended `RenderInfo` with `cursor_position` so the renderer exposes the Pi-style row/column bookkeeping.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_tui_strips_pi_cursor_marker_and_tracks_cursor_position -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `51 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus, overlays, throttled rendering, and richer editor behavior.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
