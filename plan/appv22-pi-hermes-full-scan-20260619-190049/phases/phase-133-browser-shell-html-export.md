# Phase 133: Pi Browser-Shell HTML Export

## Goal

Continue the Pi export-html port by replacing appv22's static, server-rendered HTML transcript with a Pi-style browser shell:

- session data remains base64 encoded in `#session-data`;
- the visible transcript is rendered by browser JavaScript after decoding the payload;
- the HTML includes Pi session-tree/sidebar controls and bootstrap IDs;
- raw conversation text is no longer duplicated into the static HTML body.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.html`
- `pi/packages/coding-agent/src/core/export-html/template.js`
- `pi/packages/coding-agent/src/core/export-html/index.ts`

## Red Tests

`PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_writes_standalone_session_view or export_to_html_uses_pi_browser_shell_contract or export_html_from_file_reads_arbitrary_session_jsonl_without_live_state' -q`

Failed before implementation because appv22 still rendered static message sections in the HTML and did not include Pi's browser-shell controls/bootstrap:

- missing `id="hamburger"`, `id="tree-search"`, `id="tree-container"`, `id="sidebar-resizer"`, and `id="image-modal"`;
- missing browser-side base64 decode/bootstrap JS;
- raw escaped conversation text was present in the static HTML body.

## Implementation

- Reworked `appV2.2/appv22/coding_agent/export_html.py` to emit a Pi-style browser shell with sidebar/tree/filter controls.
- Added browser-side data loading that decodes `#session-data` using `atob`, `Uint8Array`, and `TextDecoder`.
- Added JavaScript tree/path helpers matching the Pi template contract names, including `buildTree()`, `buildActivePathIds()`, and `getPath(targetId)`.
- Added client-side header, tree, and active-path message rendering for exported sessions.
- Updated export tests so message content is verified from decoded session data, not duplicated static HTML.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_writes_standalone_session_view or export_to_html_uses_pi_browser_shell_contract or export_html_from_file_reads_arbitrary_session_jsonl_without_live_state' -q` -> `3 passed, 117 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `5 passed, 115 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `118 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `291 passed, 2 deselected`
- `git diff --check -- appV2.2/appv22/coding_agent/export_html.py appV2.2/tests/test_coding_agent.py` -> passed

## Compaction Note

No compaction runtime code was changed in this phase. The protected Hermes-style compaction suites remained green (`36 passed`) after the HTML export shell changes.

## Remaining Export Gaps

- The CSS/JS are still a scoped Python port, not a full line-for-line port of Pi's large `template.css` and `template.js`.
- Vendored `marked` and `highlight.js` behavior is stubbed by empty script tags in this phase.
- Rich tool rendering, markdown rendering, deep-link polish, and image modal behavior need additional parity slices.
