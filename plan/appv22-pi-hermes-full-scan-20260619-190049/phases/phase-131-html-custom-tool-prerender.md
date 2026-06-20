# Phase 131 - HTML Custom Tool Pre-render

## Goal

Port the next concrete Pi HTML export data-contract gap into appv22 without importing from `pi/`: support Pi-style export options and include `renderedTools` for custom tool calls/results when a tool HTML renderer is supplied.

This phase does not change Hermes compaction code.

## Reference

- `pi/packages/coding-agent/src/core/export-html/index.ts`
  - `ExportOptions`
  - `ToolHtmlRenderer`
  - `preRenderCustomTools()`
  - `TEMPLATE_RENDERED_TOOLS`

## Red

Added `test_agent_session_export_to_html_prerenders_custom_tools_only` in `appV2.2/tests/test_coding_agent.py`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_prerenders_custom_tools_only' -q
```

Result: failed as expected because appv22 treated the Pi-style options object as a filesystem path:

```text
TypeError: argument should be a str or an os.PathLike object where __fspath__ returns a str, not 'dict'
```

## Implementation

- `export_session_to_html()` now accepts either a path string or a Pi-style options dict.
- Supports `outputPath`, `toolRenderer`, and `themeName` option keys.
- Added custom tool pre-rendering:
  - assistant `toolCall` blocks for non-template tools call `renderCall()` / `render_call()`;
  - matching `toolResult` messages call `renderResult()` / `render_result()`;
  - rendered data is stored under `sessionData.renderedTools`;
  - built-in template-rendered tools (`bash`, `read`, `write`, `edit`, `ls`) are skipped unless paired with already-rendered custom data, matching Pi's exporter boundary.
- `AgentSession.export_to_html()` / `exportToHtml()` now passes through Pi-style options objects.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_prerenders_custom_tools_only' -q
```

Result: `1 passed, 116 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_prerenders_custom_tools_only or export_to_html_writes_standalone_session_view' -q
```

Result: `2 passed, 115 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'extension or compact or render' -q
```

Result: `32 passed, 18 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q
```

Result: `36 passed`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_to_jsonl or session_entries or branch or resource_loader or reload and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `14 passed, 103 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `115 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `287 passed, 2 deselected`.

The deselected tests are the existing macOS environment limitation where this machine lacks a usable `python` executable for two shell/exec tests.

## Remaining Count

The full goal remains active. This phase closes a narrow Pi HTML export data-contract gap. Remaining likely slices include the richer browser-side export template/tree behavior, deeper model/settings registry parity, and live TUI rendering confidence checks while preserving the current Hermes compaction behavior.
