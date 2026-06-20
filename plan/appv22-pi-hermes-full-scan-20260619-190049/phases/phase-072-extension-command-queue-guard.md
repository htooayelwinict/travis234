# Phase 072: Extension Command Queue Guard

## Goal

Port Pi coding-agent queueing rules for extension slash commands into appv22 so extension commands cannot be queued via steering or follow-up messages.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/agent-session-queue.test.ts`

Pi executes extension commands immediately through `prompt()`, but `steer()` and `followUp()` reject registered extension commands with a clear error instead of queueing them as ordinary text.

## Changes

- Added shared extension-command parsing in `AgentSession`.
- Reused the parser for prompt dispatch.
- Added Pi-style queue guards in `steer()` and `follow_up()`.
- Added a regression proving registered extension commands are rejected from both queues and do not leave pending queue state.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_rejects_queued_extension_commands tests/test_coding_agent.py::test_agent_session_dispatches_extension_command_without_provider_turn tests/test_coding_agent.py::test_agent_session_extension_command_context_exposes_system_prompt_options -q
```

Result: `3 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension_command or provider_extension_hooks or before_provider_request or tool_call_extension or tool_result_extension or context_extension or context_handlers or before_agent_start or input_extension or message_end_extension or prompt_queues_during_streaming"
```

Result: `17 passed, 63 deselected`.
