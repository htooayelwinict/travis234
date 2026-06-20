# Phase 132: Pi Export From File and CLI Export Route

## Goal

Port Pi coding-agent standalone session export parity into appv22 without importing the Pi implementation:

- add an export-from-file API that reads an arbitrary session JSONL file directly from disk;
- keep standalone HTML export independent of any live `AgentSession` runtime state;
- add the Pi-style CLI `--export <session.jsonl> [output.html]` route that exits before provider/app startup.

## Reference

- `pi/packages/coding-agent/src/core/export-html/index.ts`
- `pi/packages/coding-agent/src/main.ts`

## Red Tests

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_html_from_file_reads_arbitrary_session_jsonl_without_live_state' -q`
  - Failed before implementation with missing `exportFromFile`.
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -k 'export_session_file_to_html_without_starting_app' -q`
  - Failed before implementation because `--export` was not recognized.

## Implementation

- Added `export_from_file()` and `exportFromFile` alias in `appV2.2/appv22/coding_agent/export_html.py`.
- Reused appv22 `SessionStore` loading to parse arbitrary JSONL session files and render the same standalone HTML shell.
- Kept file-export session payload scoped to Pi's export-from-file shape: `header`, `entries`, and `leafId`, without live `systemPrompt` or `tools`.
- Added output-path option normalization shared with existing HTML export behavior.
- Added `--export` to `appV2.2/appv22/cli.py`, exiting before provider registration or `CodingApp` construction.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_html_from_file_reads_arbitrary_session_jsonl_without_live_state' -q` -> `1 passed, 117 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -k 'export_session_file_to_html_without_starting_app' -q` -> `1 passed, 7 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `4 passed, 114 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `116 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `289 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed in this phase. The protected Hermes-style compaction suites remained green (`36 passed`) after the export and CLI changes.
