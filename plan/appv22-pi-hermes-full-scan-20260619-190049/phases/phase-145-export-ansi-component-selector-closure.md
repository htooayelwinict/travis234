# Phase 145: Pi Export ANSI Component Renderer and Selector Closure

## Goal

Close the remaining concrete exported-HTML browser-shell parity gaps found by scanning Pi's export template against appv22: Pi's custom tool ANSI component rendering path and the final missing CSS selectors.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/ansi-to-html.ts`
  - ANSI SGR conversion to inline styles
  - `ansiLinesToHtml(lines)` wrapping each rendered line as `.ansi-line`
- `pi/packages/coding-agent/src/core/export-html/tool-renderer.ts`
  - custom tool renderer output comes from component `.render(width)` lines
  - collapsed and expanded tool result components are converted through `ansiLinesToHtml`
- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `.sidebar-controls`
  - `.tree-custom-message`
  - `.ansi-line`
  - `.footer`
  - `#messages`
  - print selector including `#sidebar-toggle`

## Red Tests

Added `test_agent_session_export_to_html_converts_custom_tool_ansi_components`.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_converts_custom_tool_ansi_components -q
```

Result: failed as expected because the exported HTML lacked `.ansi-line` CSS and the custom renderer component output was not converted into Pi-style line-wrapped ANSI HTML.

Expanded `test_agent_session_export_to_html_uses_pi_visual_edge_styles` for the remaining scanned selectors.

Initial run:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_uses_pi_visual_edge_styles -q
```

Result: failed as expected because the exported HTML lacked the Pi `#messages` CSS selector after the class selector patch.

## Implementation

- Added a Python port of Pi's ANSI-to-HTML SGR conversion for exported custom tool renderer output.
- Added support for Pi-style renderer components with `.render(width)` and line arrays while preserving the existing raw HTML string renderer contract.
- Converted custom tool call, collapsed result, and expanded result component output into `.ansi-line` HTML.
- Added the missing Pi export CSS selectors: `.sidebar-controls`, `.tree-custom-message`, `.ansi-line`, `.footer`, `#messages`, and the print hide selector including `#sidebar-toggle`.
- Re-ran a Pi export template name scan. Remaining reported ID gap is only the `#e8a838` warning-color literal being parsed by the simple regex as an ID, not a selector.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_uses_pi_visual_edge_styles appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_converts_custom_tool_ansi_components appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_prerenders_custom_tools_only -q` -> `3 passed`
- Pi export selector/function scan -> missing CSS classes `[]`; missing CSS variables `[]`; missing JS functions `[]`; missing IDs only `['e8a838']` from a color literal
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `18 passed, 117 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q` -> `53 passed`
- generated export script extracted from a fresh `.venv/bin/python` export and checked with `node --check` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m compileall appV2.2/appv22 appV2.2/scripts -q` -> passed
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `133 passed, 2 deselected`
- `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `309 passed, 2 deselected`

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

## Remaining Gaps

- Live TUI ergonomics still need dedicated parity slices.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
