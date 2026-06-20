# Phase 128 - Session HTML Export

## Goal

Port Pi's `AgentSession.exportToHtml()` facade so appv22 can export a standalone HTML session view with the Pi export data contract.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/export-html/index.ts`
- `appV2.2/appv22/coding_agent/export_html.py`
- `appV2.2/appv22/coding_agent/agent_session.py`

Key Pi behaviors covered in this slice:

- `exportToHtml(outputPath?)` writes a standalone HTML file.
- The file embeds a base64 JSON `session-data` payload.
- The payload includes `header`, `entries`, `leafId`, `systemPrompt`, and tool metadata.
- The exported HTML includes a readable rendered transcript.
- The Pythonic alias `export_to_html()` maps to the same behavior.

## Protected Compaction Note

No compaction implementation, threshold, timing, manual compression, or automatic compression logic changed in this phase. This export reads already persisted session entries and current agent state only.

## Regression

Added:

- `test_agent_session_export_to_html_writes_standalone_session_view`

The test first failed with:

```text
AttributeError: 'AgentSession' object has no attribute 'export_to_html'. Did you mean: 'export_to_jsonl'?
```

## Implementation

- Added `appv22.coding_agent.export_html.export_session_to_html()`.
- Added `AgentSession.export_to_html()`/`exportToHtml()`.
- Embedded Pi-style `session-data` as base64 JSON.
- Rendered static HTML transcript sections for session messages and typed session entries.
- Escaped rendered content so exported text such as `<world>` is safe in HTML.
- Explicit output paths create parent directories when needed.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_writes_standalone_session_view' -q
```

Result after implementation: `1 passed, 111 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_to_jsonl or persists_and_reloads or branch_repoints or navigate_tree' -q
```

Result: `7 passed, 105 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'message_to_component or assistant_markdown or special_message or bash_execution' -q
```

Result: `5 passed, 45 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `110 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `282 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes the first Pi HTML export facade. Remaining likely slices include richer HTML tree/template behavior, replaced-session context details, additional runtime/extension APIs, and live TUI rendering checks while preserving the current Hermes compaction behavior.
