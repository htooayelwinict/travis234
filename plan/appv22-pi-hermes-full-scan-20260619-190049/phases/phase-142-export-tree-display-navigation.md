# Phase 142: Pi Export Tree Display and Navigation

## Goal

Bring appv22's exported HTML session tree closer to Pi's browser-shell export UI by porting rich tree display labels, connector prefixes, filtered-tree visual recalculation, and click-through navigation to the newest leaf below a selected node.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.js`
  - `toolCallMap`
  - `findNewestLeaf(nodeId)`
  - `flattenTree(roots, activePathIds)`
  - `buildTreePrefix(flatNode)`
  - `recalculateVisualStructure(filteredNodes, allFlatNodes)`
  - `formatToolCall(name, args)`
  - `parseSkillBlock(text)`
  - `getTreeNodeDisplayHtml(entry, label)`
  - cached tree rerender/update behavior
- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `.tree-prefix`
  - `.tree-marker`
  - `.tree-content`
  - tree role coloring classes

## Red Test

Added `test_agent_session_export_to_html_uses_pi_tree_display_and_navigation`.

Focused run before implementation:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_uses_pi_tree_display_and_navigation -q
```

Result: failed as expected because the exported HTML did not include `const toolCallMap = new Map();`.

## Implementation

- Added a browser-side `toolCallMap` so `toolResult` tree rows can display the originating tool-call label instead of generic message text.
- Added Pi-shaped `findNewestLeaf(nodeId)` so clicking a branch ancestor opens the newest leaf path while scrolling to the clicked node.
- Replaced simple depth-only tree flattening with Pi's active-branch-aware flattening data model.
- Added `buildTreePrefix(flatNode)` and connector/gutter metadata for stable tree branch prefixes.
- Added `recalculateVisualStructure(filteredNodes, allFlatNodes)` so filtered/search views keep coherent indentation and connector state after hidden intermediates are removed.
- Added `truncate`, `formatToolCall`, `parseSkillBlock`, and `getTreeNodeDisplayHtml` to render typed tree labels for user, skill, assistant, tool-result, compaction, branch-summary, custom, model, and thinking-level entries.
- Updated `renderTree()` to create Pi-style DOM nodes with `.tree-prefix`, `.tree-marker`, and `.tree-content`, then cache full tree renders and update only active/in-path markers on navigation.
- Added CSS for the richer tree node parts and role-specific display classes.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_uses_pi_tree_display_and_navigation -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `14 passed, 117 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- Generated export JavaScript syntax check with `node --check` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `129 passed, 2 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `302 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Full Pi export template parity remains incomplete.
- User-message skill invocation rendering in the main transcript still needs a dedicated parity slice.
- Live TUI ergonomics still need dedicated parity slices.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
