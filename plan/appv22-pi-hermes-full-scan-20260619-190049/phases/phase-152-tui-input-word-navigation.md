# Phase 152: Pi TUI Alt+B/Alt+F Word Navigation

## Goal

Port Pi's Alt+B and Alt+F word-navigation behavior into appv22's `Input` component so cursor movement by word does not insert literal `b` or `f` text.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/components/input.ts`
  - `tui.editor.cursorWordLeft`
  - `tui.editor.cursorWordRight`
  - `moveWordBackwards()`
  - `moveWordForwards()`
- `pi/packages/tui/src/keybindings.ts`
  - default word-left keys include `alt+b`
  - default word-right keys include `alt+f`

## Red Test

Added `test_input_ports_pi_alt_b_alt_f_word_navigation`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_b_alt_f_word_navigation -q
```

Result: failed as expected because appv22 inserted literal `b`, producing `hello worldb`.

## Implementation

- Routed `\x1bb` and `\x1bf` before printable-character handling.
- Added `_move_word_backward()` and `_move_word_forward()`.
- Reused appv22's local `_find_word_backward()` and `_find_word_forward()` helpers.
- Reset the input action chain on word movement, matching Pi's behavior.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_b_alt_f_word_navigation -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `59 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `315 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, richer editor behavior, and full undo coverage for every input edit path.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
