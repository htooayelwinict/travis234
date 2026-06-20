# Phase 148: Pi TUI Alt+D Input Keybinding

## Goal

Port Pi's Alt+D delete-word-forward input behavior into appv22's `Input` component.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/components/input.ts`
  - `tui.editor.deleteWordForward`
  - Delete the next word or punctuation run from the cursor.
  - Store deleted text in the kill ring so Ctrl+Y can yank it back.

## Red Test

Added `test_input_ports_pi_alt_d_delete_word_forward_keybinding`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_d_delete_word_forward_keybinding -q
```

Result: failed as expected because appv22 treated Alt+D as printable `d` input after the escape byte, producing `dhello world`.

## Implementation

- Routed `\x1bd` before printable-character handling.
- Added `_delete_word_forward()` using the local `_find_word_forward()` helper.
- Reused the existing Pi-style kill ring so deleted forward text can be yanked back with Ctrl+Y.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_d_delete_word_forward_keybinding -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `55 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `311 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, and richer editor behavior.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
