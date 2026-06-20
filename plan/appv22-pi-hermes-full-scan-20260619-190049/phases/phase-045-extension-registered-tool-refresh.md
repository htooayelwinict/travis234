# Phase 045: Extension-Registered Tool Refresh

## Goal

Port the next verified Pi coding-agent registry gap: extension-registered tools must merge into the same definition-first registry as builtin and SDK tools, carry source metadata, and be refreshable after registration.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/extensions/loader.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/src/core/extensions/types.ts`

## Changes

- Added a local `appv22.coding_agent.extensions` module with `RegisteredTool` and `ExtensionRunner`.
- `ExtensionRunner.register_tool()` stores a `ToolDefinition` plus `SourceInfo`, defaulting to synthetic `<extension:name>` metadata.
- `AgentSession` now accepts `extension_runner=...`.
- `AgentSession.refresh_tools()` / `refreshTools()` rebuilds the definition-first registry from base tools plus extension-registered tools.
- `refresh_tools(include_all_extension_tools=True)` activates newly registered extension tools while preserving existing active tools.
- Extension tools obey `allowed_tool_names` / `excluded_tool_names`.
- `getAllTools()` returns extension `sourceInfo`, and extension tools execute through the normal `ToolDefinition` wrapper with `ToolContext`.
- Exported `ExtensionRunner` and `RegisteredTool` from `appv22.coding_agent`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_registered_tools"
```

Result: `1 passed, 44 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `45 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `165 passed`.

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
