# Phase 041: TUI Rendering Components and App Wiring

## Goal

Port the remaining planned Pi TUI/rendering slices into appv22: component-backed prompt input, key handling, list selection, markdown rendering, status/footer surfaces, assistant/tool render components, compact tool rendering, and differential redraw coverage.

## Reference Files

- `pi/packages/tui/src`
- `pi/packages/tui/src/components/select-list.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/assistant-message.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/tool-execution.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/footer.ts`
- `pi/packages/coding-agent/test/tool-execution-component.test.ts`

## Changes

- Added focused render regressions for markdown, prompt input editing, select-list navigation/filtering, footer/status surfaces, assistant thinking/error/narrow wrapping, compact/expanded tool execution rendering, and TUI full/diff redraw constraints.
- Added `Markdown`, `Input`, `SelectItem`, `SelectList`, `StatusLine`, and `FooterComponent` to `appv22.tui.component`, and exported them from `appv22.tui`.
- Expanded `AssistantMessageComponent` to render assistant markdown text, thinking blocks, tool-call markers, error stops, and aborted stops through composable TUI components.
- Expanded `ToolExecutionComponent` to support Pi-style constructor arguments, compact/expanded state, definition-level `render_call` and `render_result` hooks, generic result fallback rendering, and terminal-width truncation.
- Wired `CodingApp` to pass active `ToolDefinition` objects into `InteractiveRenderer`, and subscribed the renderer at the `AgentSession` layer so session-decorated events are preserved.
- Updated `InteractiveMode` to render startup/history/prompt/status/footer through the component stack while retaining the existing synchronous input seam used by tests.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "markdown_input_select or assistant_markdown or tool_execution_uses_render_hooks or footer_status_diff"
```

Result: `4 passed, 10 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `14 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `43 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `157 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this phase, 0 plan checklist items remain open.
