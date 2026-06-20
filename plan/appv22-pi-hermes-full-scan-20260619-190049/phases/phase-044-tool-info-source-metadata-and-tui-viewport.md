# Phase 044: ToolInfo Source Metadata and TUI Viewport Clipping

## Goal

Continue the Pi/Hermes compliance scan by porting a verified Pi coding-agent registry gap and fixing a live TUI viewport issue observed during app use.

## Reference Files

- `pi/packages/coding-agent/src/core/source-info.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/tui/src/tui.ts`

## Changes

- Added a local `SourceInfo` port in `appv22.coding_agent.source_info` with `create_synthetic_source_info()`.
- Added optional `source_info` to `ToolDefinition`.
- Updated `AgentSession` to retain source metadata for tool definitions:
  - builtin tools use synthetic `<builtin:name>` metadata with `source="builtin"`;
  - SDK/custom/bare tool definitions use synthetic `<sdk:name>` metadata with `source="sdk"` unless a definition supplies explicit metadata.
- Added Pi-style camelCase aliases: `getAllTools()`, `getActiveToolNames()`, `getToolDefinition()`, and `setActiveToolsByName()`.
- Updated `get_all_tools()` / `getAllTools()` to return Pi-shaped `ToolInfo` dictionaries with `promptGuidelines` and `sourceInfo`, plus snake_case compatibility aliases.
- Exported `SourceInfo` and `create_synthetic_source_info()` from `appv22.coding_agent`.
- Added a TUI viewport regression for transcripts taller than terminal rows.
- Updated `TUI` to clip frames to the terminal row viewport before full/diff rendering so redraws never move the cursor beyond the visible terminal height.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "tool_info_with_source_metadata"
```

Result: `1 passed, 43 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `44 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `20 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `164 passed`.

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
