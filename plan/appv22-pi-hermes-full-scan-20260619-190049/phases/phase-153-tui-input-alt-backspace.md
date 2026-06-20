# Phase 153: Pi TUI Alt+Backspace Delete Word Backward

## Goal

Port Pi's Alt+Backspace delete-word-backward behavior into appv22's `Input` component so terminal escape sequences delete the previous word instead of deleting one character or inserting text.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/keybindings.ts`
  - `deleteWordBackward` default keys include `ctrl+w` and `alt+backspace`
- `pi/packages/tui/src/keys.ts`
  - maps `\x1b\x7f` and `\x1b\b` to `alt+backspace`
- `pi/packages/tui/src/components/input.ts`
  - routes `deleteWordBackward` through `deleteWordBackwards()`

## Red Test

Added `test_input_ports_pi_alt_backspace_delete_word_backward`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_backspace_delete_word_backward -q
```

Result: failed as expected because appv22 treated Alt+Backspace as ordinary backspace and produced `hello worl`.

## Implementation

- Routed both Pi terminal encodings for Alt+Backspace before printable-character and ordinary backspace handling.
- Reused appv22's existing `_delete_word_backward()` behavior, which already feeds the local kill ring.
- Preserved Ctrl+W behavior and did not touch compaction runtime code.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_alt_backspace_delete_word_backward -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `60 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall -q appV2.2/appv22` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `316 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, richer editor behavior, and full undo coverage for every input edit path.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
