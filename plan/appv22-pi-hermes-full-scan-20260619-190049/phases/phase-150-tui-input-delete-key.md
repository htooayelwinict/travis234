# Phase 150: Pi TUI Delete-Key Forward Deletion

## Goal

Port Pi's Delete-key forward-deletion behavior into appv22's `Input` component so `ESC [ 3 ~` deletes the character under the cursor instead of entering raw escape-sequence text.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/components/input.ts`
  - `tui.editor.deleteCharForward`
  - `handleForwardDelete()`
  - Delete the first grapheme after the cursor when the cursor is before the end of the input.

## Red Test

Added `test_input_ports_pi_delete_key_forward_deletion`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_delete_key_forward_deletion -q
```

Result: failed as expected because appv22 inserted the escape payload as text: `h[3~ello`.

## Implementation

- Routed `\x1b[3~` before printable-character handling in `Input.handle_input()`.
- Added `_delete_char_forward()` to remove the character at the cursor.
- Preserved cursor position and made Delete at end-of-line a no-op.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_delete_key_forward_deletion -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `57 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `313 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, undo behavior, word navigation, and richer editor behavior.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
