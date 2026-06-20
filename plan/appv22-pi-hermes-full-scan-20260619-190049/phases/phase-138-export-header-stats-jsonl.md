# Phase 138: Pi Export Header Stats and JSONL Download

## Goal

Bring appv22's exported HTML session header closer to Pi's browser-shell export design by adding session statistics, header action buttons, and JSONL download behavior directly in `appv22`.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.js`
  - `formatTokens(...)`
  - `downloadSessionJson(...)`
  - `computeStats(...)`
  - `globalStats`
  - `renderHeader(...)`
  - header toggle button wiring
- `pi/packages/coding-agent/src/core/export-html/template.css`
  - header, help bar, info item, system prompt, and tools-list styling

## Red Test

Used `test_agent_session_export_to_html_wires_header_stats_and_jsonl_download`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_wires_header_stats_and_jsonl_download -q
```

Result: failed as expected because the exported HTML did not include `function formatTokens(count)`.

## Implementation

- Kept the existing Pi-shaped exported header CSS, `computeStats(entryList)`, `globalStats`, `renderHeader()`, and header toggle handlers.
- Added the missing browser-side `formatTokens(count)`.
- Added the missing browser-side `downloadSessionJson()` and exposed it through `window.downloadSessionJson` for the header button.
- Removed duplicate helper definitions after a symbol scan confirmed two `formatTokens` / `downloadSessionJson` definitions.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_wires_header_stats_and_jsonl_download -q` -> `1 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `9 passed, 117 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `124 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `297 passed, 2 deselected`
- `git diff --check -- appV2.2/appv22/coding_agent/export_html.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/tests/test_coding_agent.py plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md` -> passed
- trailing-whitespace scan over touched tracked/untracked files -> passed

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Full Pi export template parity remains incomplete.
- Exported image modal, sidebar resizing, keyboard shortcuts, and richer tool rendering still need dedicated parity slices.
- Live TUI ergonomics still need dedicated parity slices.
- The full appv22 Pi/Hermes objective remains active and unproven.
