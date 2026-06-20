# Phase 134: Pi Markdown and Highlight HTML Export

## Goal

Continue the Pi export-html port by replacing the Phase 133 empty vendor-script stubs with appv22-local copies of Pi's vendored markdown and syntax-highlighting assets, then route browser-rendered message text through the same marked/highlight renderer shape.

## Reference

- `pi/packages/coding-agent/src/core/export-html/vendor/marked.min.js`
- `pi/packages/coding-agent/src/core/export-html/vendor/highlight.min.js`
- `pi/packages/coding-agent/src/core/export-html/template.js`

## Red Test

`PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_embeds_markdown_highlight_renderer' -q`

Failed before implementation because appv22 exported empty vendor script tags and did not include:

- `marked v15.0.4`;
- `Highlight.js v11.9.0`;
- `marked.use({`;
- `safeMarkedParse`;
- `hljs.highlight(code, { language: lang }).value`;
- a browser-side markdown rendering path for message text.

## Implementation

- Added appv22-local copies of Pi's vendored assets:
  - `appV2.2/appv22/coding_agent/export_html_assets/vendor/marked.min.js`
  - `appV2.2/appv22/coding_agent/export_html_assets/vendor/highlight.min.js`
- Verified asset parity with Pi by SHA-256:
  - `marked.min.js`: `74c9f2e02c180c3e6caa09881a0b24032c86473af90acb1c87b6dc7255d491dd`
  - `highlight.min.js`: `837a6fa5b0c736b52bbde2b2b6190f305da3fc9ed41681db5321507057b5c846`
- Updated `appV2.2/appv22/coding_agent/export_html.py` to embed those appv22-local assets into Pi's `{{MARKED_JS}}` and `{{HIGHLIGHT_JS}}` positions.
- Ported Pi's marked renderer configuration shape:
  - `breaks: true`, `gfm: true`;
  - HTML/tag tokenizer override so HTML-like input is displayed as text;
  - strict strikethrough tokenizer;
  - URL scheme allow-list for links/images;
  - highlight.js-backed fenced code rendering;
  - escaped inline code rendering.
- Routed browser-rendered user/assistant message text through `safeMarkedParse(messageText(message))`.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_embeds_markdown_highlight_renderer' -q` -> `1 passed, 120 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `6 passed, 115 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `119 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `292 passed, 2 deselected`
- `git diff --check -- appV2.2/appv22/coding_agent/export_html.py appV2.2/tests/test_coding_agent.py appV2.2/appv22/coding_agent/export_html_assets/vendor/marked.min.js appV2.2/appv22/coding_agent/export_html_assets/vendor/highlight.min.js` -> passed

## Compaction Note

No compaction runtime code was changed in this phase. The protected Hermes-style compaction suites remained green (`36 passed`) after the HTML export renderer changes.

## Remaining Export Gaps

- The appv22 template CSS/JS is still a scoped Python port, not a full line-for-line port of Pi's large `template.css` and `template.js`.
- Rich tool rendering, search/filter behavior, deep-link polish, image modal behavior, and full branch-tree UI interactions need additional parity slices.
