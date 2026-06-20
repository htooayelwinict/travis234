# Phase 149: Pi TUI Bracketed Paste Sanitization

## Goal

Port Pi's bracketed-paste sanitization into appv22's `Input` component so pasted text remains a single-line editor value.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/tui/src/components/input.ts`
  - `handlePaste(pastedText)`
  - Remove `\r\n`, `\r`, and `\n`.
  - Replace `\t` with four spaces.
  - Insert cleaned text at the cursor and advance the cursor by the cleaned length.

## Red Test

Added `test_input_ports_pi_bracketed_paste_sanitization`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_bracketed_paste_sanitization -q
```

Result: failed as expected because appv22 inserted raw newlines and tabs into the editor value.

## Implementation

- Routed bracketed paste insertion through `_insert_paste()`.
- Added Pi-equivalent paste normalization:
  - remove CRLF, CR, and LF
  - expand tabs to four spaces
- Updated cursor placement to use the cleaned paste length.
- Reset the input action chain after paste, matching Pi's behavior of breaking the previous edit action.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py::test_input_ports_pi_bracketed_paste_sanitization -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `56 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `312 passed, 2 deselected`

## Compaction Note

No compaction runtime code was touched. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI still needs deeper Pi parity around raw terminal input, focus management, overlays, throttled rendering, and richer editor behavior.
- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
