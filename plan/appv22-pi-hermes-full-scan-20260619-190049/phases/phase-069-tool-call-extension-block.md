# Phase 069: Extension Tool Call Block

## Goal

Port Pi coding-agent extension `tool_call` interception into appv22 so extensions can block a tool call before the tool executes.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/test/suite/agent-session-model-extension.test.ts`

Pi installs `tool_call` interception through `agent.beforeToolCall`. `ExtensionRunner.emitToolCall()` chains handlers and returns immediately when a handler returns `{ block: true, reason }`.

## Changes

- Added `ExtensionRunner.emit_tool_call()` / `emitToolCall()`.
- Wired `AgentSession` into the existing lower-level `before_tool_call` hook.
- Converted extension block results into `BeforeToolCallResult`, preserving block reason text as the tool error result.
- Added a regression proving a blocked extension tool call does not execute the tool and appears as an error tool result in subsequent provider context.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_tool_call_extension_blocks_execution -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "tool_call_extension or tool_result_extension or context_extension or context_handlers or before_agent_start or input_extension or message_end_extension"
```

Result: `11 passed, 64 deselected`.
