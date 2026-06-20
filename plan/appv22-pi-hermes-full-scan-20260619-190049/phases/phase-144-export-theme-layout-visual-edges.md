# Phase 144: Pi Export Theme Layout and Visual Edges

## Goal

Bring appv22's exported HTML browser shell styling closer to Pi's template by porting Pi's theme variables, line-height-driven layout, centered content geometry, sidebar/tree visual states, tool-parameter hints, markdown styling, and highlight.js color tokens.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `--line-height`
  - `--dim`
  - `--selectedBg`
  - `--borderAccent`
  - custom-message and user-message theme variables
  - syntax and markdown color variables
  - sticky sidebar/resizer geometry
  - centered `#content > *` layout
  - tree in-path / not-in-path visual states
  - `.help-hint`, `.info-value`, `.tool-params-hint`
  - markdown block/table/code styles
  - highlight.js token colors

## Red Tests

Added `test_agent_session_export_to_html_uses_pi_theme_and_layout_tokens`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_uses_pi_theme_and_layout_tokens -q
```

Result: failed as expected because the exported HTML did not include `--line-height: 18px;`.

The existing `test_agent_session_export_to_html_uses_pi_visual_edge_styles` also failed in the export subset because appv22 lacked Pi's help-hint, tool-parameter hint, system-prompt note, markdown, and syntax-highlight edge styles.

## Implementation

- Added Pi-shaped theme variables to the exported CSS, including dim/warning/success/error, selected/background accents, custom-message colors, markdown colors, tool diff colors, and syntax highlighting colors.
- Switched the exported shell base font and body line-height to Pi's 12px / 18px cell rhythm.
- Ported Pi-like sidebar, filter, tree node, in-path, and not-in-path visual states.
- Ported Pi's sticky transparent sidebar resizer styling.
- Ported Pi's centered main content layout with `#content > *` constrained to 800px.
- Added Pi help-hint and info-value layout styles.
- Added Pi tool-parameter hint styles and emitted the matching hint span in rendered tool definitions.
- Added Pi system prompt note/provider-prompt styles.
- Added Pi markdown block styles for headings, paragraphs, links, inline code, pre blocks, blockquotes, lists, rules, tables, and images.
- Added Pi highlight.js token color styles.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_uses_pi_theme_and_layout_tokens -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_uses_pi_visual_edge_styles -q` -> `1 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `17 passed, 117 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- generated export script extracted from a fresh `.venv/bin/python` export and checked with `node --check` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `132 passed, 2 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `305 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

If a future broad compaction fix is needed, it must stay within Pi/Hermes scope and be backed by dedicated compaction verification.

## Remaining Gaps

- Exported browser-shell visual parity still needs final edge audit against the current Pi template.
- Live TUI ergonomics still need dedicated parity slices.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
