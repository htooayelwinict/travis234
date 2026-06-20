# Phase 143: Pi Export Transcript Entry Blocks

## Goal

Bring appv22's exported HTML transcript body closer to Pi's browser shell by replacing generic visible entry sections with Pi-shaped body blocks for skill-invoked user turns, assistant text/thinking/error output, model changes, compactions, branch summaries, and displayable custom hook messages.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `.user-message`
  - `.assistant-message`
  - `.assistant-text`
  - `.skill-user-entry`
  - `.skill-invocation`
  - `.model-change`
  - `.compaction-content`
  - `.hook-message`
  - `.branch-summary`
  - `.error-text`
- `pi/packages/coding-agent/src/core/export-html/template.js`
  - skill-block user rendering through `parseSkillBlock(...)`
  - assistant text/thinking blocks before tool-call blocks
  - assistant aborted/error status rendering
  - hidden standalone `toolResult` entries
  - Pi-specific non-message entry blocks

## Red Test

Added `test_agent_session_export_to_html_renders_pi_transcript_entry_blocks`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_pi_transcript_entry_blocks -q
```

Result: failed as expected because the exported HTML did not include `.skill-user-entry:hover .copy-link-btn`.

## Implementation

- Added Pi-shaped exported CSS for transcript body blocks, hover link affordances, highlights, skill invocations, compactions, hook messages, branch summaries, model changes, and assistant errors.
- Added exported `formatTimestamp(timestamp)` for timestamp body blocks.
- Reworked `renderEntry(entry)` so:
  - user messages with `<skill ...>` content render through Pi's `content`/`text`/`parseSkillBlock(text)` flow as a collapsed skill invocation plus optional user message block;
  - normal user messages render as `.user-message`;
  - assistant text/thinking blocks render before tool-call blocks;
  - assistant aborted/error stop reasons render as `.error-text`;
  - bash execution messages render with the same tool execution visual style;
  - model changes, compactions, branch summaries, and displayable custom messages render as Pi-specific blocks;
  - non-display custom messages and unknown settings entries stay hidden from the transcript body.
- Updated the Phase 141 tool-call regression to expect `html += renderToolCall(block);`, matching the Pi-shaped body renderer.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_pi_transcript_entry_blocks -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `15 passed, 117 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- Generated export JavaScript syntax check with `node --check` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `130 passed, 2 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `303 passed, 2 deselected`
- generated export script extracted from a fresh `.venv/bin/python` export and checked with `node --check` -> passed

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

If a future broad compaction fix is needed, it must stay within Pi/Hermes scope and be backed by dedicated compaction verification.

## Remaining Gaps

- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- Live TUI ergonomics still need dedicated parity slices.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
