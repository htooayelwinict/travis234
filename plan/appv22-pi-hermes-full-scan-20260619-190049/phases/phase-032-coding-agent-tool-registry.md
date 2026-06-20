# Phase 032: Coding-Agent Tool Registry

## Scope

Port the first Phase 3 coding-agent session slice from Pi's definition-first tool registry model into appv22.

## Reference

- `pi/packages/coding-agent/src/core/sdk.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/regressions/2835-tools-allowlist-filters-extension-tools.test.ts`

## Changes

- Added coverage that default `AgentSession` prompts always expose the Pi default coding tools: `read`, `bash`, `edit`, and `write`.
- Added coverage that prompt wording no longer swaps active tools to a read-only heuristic set.
- Added coverage for Pi-style active/all tool registry APIs, explicit active tool updates, unknown-tool filtering, allowlists, and definition lookup.
- Added coverage that caller-provided `ToolDefinition` objects are wrapped into executable runtime tools with a `ToolContext`.
- Removed prompt-word heuristic activation from `AgentSession.prompt()`.
- Added a definition-first registry with allowed/excluded filters and synthesized `ToolDefinition` objects for bare `AgentTool` overrides.
- Wrapped caller-provided `ToolDefinition` objects into executable `AgentTool` objects without importing Pi modules.
- Added `get_active_tool_names()`, `get_all_tools()`, `get_tool_definition()`, and `set_active_tools_by_name()`.

## Red/Green Evidence

Red:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_exposes_default_coding_tools_for_greeting tests/test_coding_agent.py::test_agent_session_keeps_default_coding_tools_for_repo_inspection_prompt tests/test_coding_agent.py::test_agent_session_registry_set_active_tools_and_allowlist -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_registry_set_active_tools_and_allowlist -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_wraps_tool_definitions_into_runtime_tools -q
```

Results:

- Focused registry tests initially failed because greetings had no tools, repo-inspection prompts switched to `read`/`grep`/`find`/`ls`, and `allowed_tool_names` was not accepted.
- After checking Pi allowlist semantics, the allowlist test was corrected and failed until the allowlist became the initial active tool set.
- The ToolDefinition wrapper test failed because definitions were visible in the registry but no executable custom tool was active.

Green:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_exposes_default_coding_tools_for_greeting tests/test_coding_agent.py::test_agent_session_keeps_default_coding_tools_for_repo_inspection_prompt tests/test_coding_agent.py::test_agent_session_registry_set_active_tools_and_allowlist -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_wraps_tool_definitions_into_runtime_tools -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Results:

- focused registry regressions: `3 passed`
- ToolDefinition wrapper regression: `1 passed`
- `tests/test_coding_agent.py`: `19 passed`

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Results:

- `tests`: `129 passed`
- `py_compile`: exit 0

## Remaining Work

- Port Phase 3 session events: queue updates, compaction events, retry events, thinking-level/model changes, and session-info updates.
- Port prompt preflight behavior for streaming: steer vs follow-up.
- Port session persistence/branching hooks after the core event/session API is stable.
