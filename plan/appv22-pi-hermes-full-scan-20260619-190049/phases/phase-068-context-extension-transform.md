# Phase 068: Extension Context Transform

## Goal

Port Pi coding-agent extension `context` handlers into appv22 so extensions can transform the message context sent to the provider before the LLM call, without mutating the saved session transcript.

## Reference

- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/src/core/sdk.ts`
- `pi/packages/coding-agent/test/suite/agent-session-model-extension.test.ts`

Pi clones the provider context, chains registered `context` handlers, accepts returned `messages`, and wires the result through SDK `transformContext`.

## Changes

- Added `ExtensionRunner.emit_context()` / `emitContext()` with clone-and-chain message handling plus extension error forwarding.
- Wrapped `AgentSession` context transformation so caller-provided `transform_context` still runs first, then extension `context` handlers run before provider conversion.
- Added regressions proving provider messages can be rewritten while stored session messages remain original.
- Added a direct runner chaining regression for multiple `context` handlers.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_context_extension_transforms_provider_messages_without_mutating_session tests/test_coding_agent.py::test_extension_runner_context_handlers_chain_messages -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "context_extension or context_handlers or before_agent_start or tool_result_extension or input_extension or message_end_extension"
```

Result: `10 passed, 64 deselected`.
