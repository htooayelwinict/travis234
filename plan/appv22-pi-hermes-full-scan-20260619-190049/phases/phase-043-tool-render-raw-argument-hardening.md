# Phase 043: Tool Render Raw Argument Hardening

## Goal

Fix the real-run crash where a model emitted `read({"path": "...", "limit": "100.0"})`. `tool_execution_start` is emitted before validation, so TUI render hooks saw raw model arguments and the read renderer crashed while calculating a line range.

## Root Cause

- `agent_loop` emits `ToolExecutionStartEvent` before `_prepare_tool_call()` validates JSON-schema arguments.
- `ToolExecutionComponent` immediately renders the tool call using the tool definition's `render_call` hook.
- `read._format_read_line_range()` assumed `offset` and `limit` were already integers and did `start_line + args["limit"] - 1`.
- A raw string limit from the model therefore raised `TypeError` inside rendering, and the failure handler crashed again because the stale tool component was still in the render tree.

## Changes

- Added a renderer regression for `ToolExecutionComponent("read", {"limit": "100.0"})` proving raw model numeric strings do not crash rendering.
- Added an app-level regression proving a bad read numeric string returns the schema validation error in the TUI instead of a Python traceback.
- Hardened `read._format_read_line_range()` so it only renders ranges for actual integer values and leaves invalid args to normal tool validation.
- Added defensive fallback around `ToolExecutionComponent` definition-level `render_call` and `render_result` hooks so a render hook cannot take down tool execution.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "unvalidated_model_numeric_strings or bad_read_numeric_string"
```

Result: `2 passed, 17 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `19 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "read"
```

Result: `9 passed, 34 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `162 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this follow-up, 0 plan checklist items remain open.
