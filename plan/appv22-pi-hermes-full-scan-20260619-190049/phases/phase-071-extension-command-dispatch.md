# Phase 071: Extension Command Dispatch

## Goal

Port Pi coding-agent extension slash command registration and idle dispatch into appv22 so registered commands execute before provider turns and receive a Pi-shaped command context.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/coding-agent/src/core/extensions/loader.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/agent-session-queue.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-prompt.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-model-extension.test.ts`

Pi exposes `registerCommand()`, checks extension slash commands before input interception/provider dispatch, and passes command handlers a context with live system prompt helpers.

## Changes

- Added `RegisteredCommand` and `ExtensionRunner.register_command()` / `registerCommand()`.
- Added command lookup/list APIs.
- Added `AgentSession` slash-command dispatch before input hooks and provider calls.
- Added `ExtensionCommandContext` with `getSystemPrompt()` and `getSystemPromptOptions()`.
- Exported command-related types from `appv22.coding_agent`.
- Added regressions proving idle extension commands do not consume provider turns or session messages, and that command context exposes fresh system prompt options.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_dispatches_extension_command_without_provider_turn tests/test_coding_agent.py::test_agent_session_extension_command_context_exposes_system_prompt_options -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or provider_extension_hooks or before_provider_request or tool_call_extension or tool_result_extension or context_extension or context_handlers or before_agent_start or input_extension or message_end_extension"
```

Result: `15 passed, 64 deselected`.
