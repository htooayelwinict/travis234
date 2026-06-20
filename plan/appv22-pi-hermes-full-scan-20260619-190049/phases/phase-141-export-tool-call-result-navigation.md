# Phase 141: Pi Export Tool Call Rendering and Cached Navigation

## Goal

Bring appv22's exported HTML browser shell closer to Pi's export UI by rendering assistant tool calls inline with their matching tool results and by caching rendered entry nodes during tree navigation.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.js`
  - `findToolResult(toolCallId)`
  - `formatExpandableOutput(text, maxLines, lang)`
  - `renderToolCall(call)`
  - hidden standalone `toolResult` entries
  - cached rendered entry nodes
  - tool-result deep-link targets resolved to the owning tool-call block
- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `.tool-execution`
  - `.tool-output`
  - expandable output preview/full rendering

## Red Test

Added `test_agent_session_export_to_html_renders_tool_calls_with_cached_navigation`.

Focused run before implementation:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_tool_calls_with_cached_navigation -q
```

Result: failed as expected because the exported HTML did not include Pi-shaped tool-call rendering and cached navigation hooks.

## Implementation

- Added browser-side `findToolResult(toolCallId)` lookup over persisted session entries.
- Added `formatExpandableOutput(text, maxLines, lang)` for compact output previews with optional highlight.js formatting.
- Added `renderToolCall(call)` with a Pi-shaped switch renderer for `bash`, `read`, `write`, and custom/pre-rendered tools.
- Updated assistant-message rendering so text/thinking blocks remain visible and tool-call blocks render inline with their result output.
- Hid standalone `toolResult` messages from the transcript body while keeping them in persisted export data.
- Kept the markdown fallback path for assistant messages that only have flattened text content.
- Added cached entry-node rendering and tool-result deep-link targeting through `getScrollTargetElementId(entryId)` and `renderEntryToNode(entry)`.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_renders_tool_calls_with_cached_navigation -q` -> `1 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `12 passed, 117 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `127 passed, 2 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `300 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

The bare `python` command is not available in this macOS shell, so compileall was verified with `.venv/bin/python`.

## Remaining Gaps

- Full Pi export template parity remains incomplete.
- Live TUI ergonomics still need dedicated parity slices.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
