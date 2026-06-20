# Phase 154: Pi TUI Ctrl+D Delete Char Forward

## Goal

Port Pi's Ctrl+D editor keybinding into appv22's `Input` component so Ctrl+D deletes the character under the cursor, matching Pi's `tui.editor.deleteCharForward` binding.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/keybindings.ts`
  - `tui.editor.deleteCharForward` default keys include `delete` and `ctrl+d`
- `appV2.2/appv22/tui/component.py`
  - appv22 already had Delete-key support through `_delete_char_forward()`

## Red Test

Added `test_input_ports_pi_ctrl_d_delete_char_forward_keybinding`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_ctrl_d_delete_char_forward_keybinding -q
```

Result: failed as expected because appv22 ignored Ctrl+D and left `hello` unchanged.

## Implementation

- Routed raw Ctrl+D (`\x04`) to `_delete_char_forward()`.
- Reused the existing Delete-key implementation path so undo and cursor behavior stay aligned.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_ctrl_d_delete_char_forward_keybinding -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `61 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall -q appV2.2/appv22` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `317 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, richer editor behavior, and full undo coverage for every input edit path.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
