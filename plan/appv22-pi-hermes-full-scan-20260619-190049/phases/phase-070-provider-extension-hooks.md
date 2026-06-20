# Phase 070: Provider Extension Hooks

## Goal

Port Pi coding-agent provider extension hooks into appv22 stream options so extensions can inspect or mutate provider request payloads and observe provider responses.

## Reference

- `pi/packages/coding-agent/src/core/sdk.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/test/suite/harness.ts`

Pi wires `before_provider_request` into stream `onPayload` and `after_provider_response` into stream `onResponse`. `emitBeforeProviderRequest()` chains returned payloads.

## Changes

- Added `ExtensionRunner.emit_before_provider_request()` / `emitBeforeProviderRequest()`.
- Wired `AgentSession` provider callbacks into the underlying `Agent` stream options.
- Added `after_provider_response` event emission with Pi-shaped `status` and `headers` fields.
- Added regressions proving provider payload mutation and response observation are available through stream options.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_provider_extension_hooks_are_wired_into_stream_options tests/test_coding_agent.py::test_extension_runner_before_provider_request_chains_payloads -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "provider_extension_hooks or before_provider_request or tool_call_extension or tool_result_extension or context_extension or context_handlers or before_agent_start or input_extension or message_end_extension"
```

Result: `13 passed, 64 deselected`.
