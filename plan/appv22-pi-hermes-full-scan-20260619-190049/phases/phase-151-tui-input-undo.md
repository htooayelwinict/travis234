# Phase 151: Pi TUI Ctrl-Minus Undo

## Goal

Port Pi's Ctrl-minus undo behavior into appv22's `Input` component for typed text and forward-delete edits.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/components/input.ts`
  - `tui.editor.undo`
  - `UndoStack<InputState>`
  - Consecutive word characters coalesce into one undo unit.
  - Whitespace starts a new undo unit.
  - Forward delete pushes a snapshot before mutation.

## Red Test

Added `test_input_ports_pi_ctrl_minus_undo_for_typing_and_delete`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_ctrl_minus_undo_for_typing_and_delete -q
```

Result: failed as expected because appv22 inserted the Ctrl-minus CSI-u payload as text: `hello world[45;5u`.

## Implementation

- Added a local undo stack storing `(value, cursor)` snapshots.
- Routed `\x1b[45;5u` before printable-character handling.
- Added `_push_undo()` and `_undo()`.
- Coalesced consecutive non-whitespace character typing into one undo unit.
- Started a new undo unit for whitespace typing.
- Pushed undo snapshots for backspace, forward delete, paste, word kill, line kill, yank, and yank-pop mutation paths.
- Reset the action chain on cursor movement and undo.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_ctrl_minus_undo_for_typing_and_delete -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `58 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `314 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, word navigation, richer editor behavior, and full undo coverage for every input edit path.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
