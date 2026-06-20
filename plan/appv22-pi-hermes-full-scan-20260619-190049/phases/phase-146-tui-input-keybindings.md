# Phase 146: Pi TUI Input Keybindings

## Goal

Port Pi's single-line TUI input movement and kill-ring keybindings into appv22's `Input` component so the live editor behaves closer to `pi/packages/tui/src/components/input.ts`.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/components/input.ts`
  - Ctrl+A / Ctrl+E line movement
  - Ctrl+W delete-word-backward into kill ring
  - Ctrl+U delete-to-line-start into kill ring
  - Ctrl+K delete-to-line-end into kill ring
  - Ctrl+Y yank
  - Alt+Y yank-pop rotation

## Red Tests

Added `test_input_ports_pi_line_movement_and_kill_yank_keybindings`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_line_movement_and_kill_yank_keybindings -q
```

Result: failed as expected because Ctrl+A left the cursor at the end of the input.

Added `test_input_ports_pi_line_kill_and_yank_pop_keybindings`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_line_kill_and_yank_pop_keybindings -q
```

Result: failed as expected because Ctrl+U did not delete the line prefix into the kill ring.

## Implementation

- Added Pi-style kill-ring state to `Input`.
- Added Ctrl+A and Ctrl+E cursor movement.
- Added Ctrl+W delete-word-backward using local word-boundary helpers.
- Added Ctrl+U and Ctrl+K line kill operations.
- Added Ctrl+Y yank and Alt+Y yank-pop rotation.
- Preserved existing autocomplete, bracketed paste, arrow, home/end, submit, backspace, and printable-character paths.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_line_movement_and_kill_yank_keybindings -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_line_kill_and_yank_pop_keybindings -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `53 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `309 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus, overlays, throttled rendering, and richer editor rendering.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
