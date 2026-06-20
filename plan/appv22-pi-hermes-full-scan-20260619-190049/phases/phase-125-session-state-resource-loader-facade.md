# Phase 125 - Session State Resource Loader Facade

## Goal

Port Pi's read-only `AgentSession` facade for direct state/resource access where appv22 already had the same objects internally.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `appV2.2/appv22/coding_agent/agent_session.py`
- `appV2.2/appv22/coding_agent/resource_loader.py`

Key Pi behaviors covered in this slice:

- `session.state` returns the underlying agent state.
- `session.resourceLoader` exposes the active resource loader.
- `session.promptTemplates` exposes file-based prompt templates from the resource loader.

## Protected Compaction Note

No compaction implementation, threshold, timing, manual compression, or automatic compression logic changed in this phase. This is a read-only facade port over existing appv22 objects.

## Regression

Added:

- `test_agent_session_exposes_state_resource_loader_and_prompt_templates`

The test first failed with:

```text
AttributeError: 'AgentSession' object has no attribute 'state'
```

## Implementation

- Added `AgentSession.state`.
- Added `AgentSession.resource_loader` and `AgentSession.resourceLoader`.
- Added `AgentSession.prompt_templates` and `AgentSession.promptTemplates`.
- Returned existing internal objects/results directly instead of adding wrappers or changing reload behavior.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'exposes_state_resource_loader_and_prompt_templates' -q
```

Result after implementation: `1 passed, 107 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'resource_loader or prompt_templates or reload_resources or package_skills' -q
```

Result: `4 passed, 104 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `106 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `278 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes a small read-only Pi `AgentSession` facade gap. Remaining likely slices include retry facade parity, additional runtime/extension APIs, export helpers, and live TUI rendering checks while preserving the current Hermes compaction behavior.
