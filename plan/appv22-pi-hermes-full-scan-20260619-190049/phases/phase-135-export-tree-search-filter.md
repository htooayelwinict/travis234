# Phase 135: Pi Export Tree Search and Filter

## Goal

Continue the Pi export-html browser UI port by wiring the session-tree search and filter controls that were present in the Phase 133 shell but not functional.

This phase does not change Hermes compaction code.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.html`
  - `#tree-search`
  - `.filter-btn[data-filter=...]`
  - `#tree-status`
- `pi/packages/coding-agent/src/core/export-html/template.js`
  - `filterMode`
  - `searchQuery`
  - `filterNodes(flatNodes, currentLeafId)`
  - `forceTreeRerender()`
  - search input and filter button event handlers

## Red Test

Added `test_agent_session_export_to_html_wires_tree_search_and_filters` in `appV2.2/tests/test_coding_agent.py`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_wires_tree_search_and_filters' -q
```

Result: failed as expected because appv22 had the sidebar controls but lacked the backing browser JS:

- `filterMode`;
- `searchQuery`;
- `hasTextContent`;
- `getSearchableText`;
- `filterNodes`;
- `forceTreeRerender`;
- `tree-search` input listener;
- filter button listener;
- filtered/total tree status.

## Implementation

- Added browser-side filter state to `appV2.2/appv22/coding_agent/export_html.py`.
- Ported content extraction/searchable-text helpers for message, custom, compaction, branch-summary, model-change, thinking-level, and label entries.
- Added Pi-style filter modes:
  - `default`;
  - `no-tools`;
  - `user-only`;
  - `labeled-only`;
  - `all`.
- Updated tree rendering to render only filtered rows and show `${filtered.length} / ${rows.length} entries`.
- Added `forceTreeRerender()` and wired `#tree-search` input plus `.filter-btn` clicks.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_wires_tree_search_and_filters' -q` -> `1 passed, 121 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `7 passed, 115 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `120 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `293 passed, 2 deselected`
- `git diff --check -- appV2.2/appv22/coding_agent/export_html.py appV2.2/tests/test_coding_agent.py plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md` -> passed

## Compaction Note

No compaction runtime code was changed in this phase. The protected Hermes-style compaction suites remained green (`36 passed`) after the export tree search/filter changes.

## Remaining Export Gaps

- Full Pi `template.css` and `template.js` parity remains incomplete.
- Rich tool result rendering, copy-link/deep-link behavior, image modal behavior, sidebar resizing, JSONL download, and header statistics need additional parity slices.
