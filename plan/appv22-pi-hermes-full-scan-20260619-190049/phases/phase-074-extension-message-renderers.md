# Phase 074: Extension Message Renderers

## Goal

Port Pi coding-agent extension message renderer registration into appv22 so extensions can register and retrieve renderers for custom message types.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/coding-agent/src/core/extensions/loader.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/test/extensions-runner.test.ts`

Pi exposes `registerMessageRenderer()` and `getMessageRenderer(customType)`, returning `undefined` for missing custom types. Appv22 already had TUI custom-message components that accept renderer maps; this phase ports the missing runner registry surface.

## Changes

- Added `ExtensionRunner.register_message_renderer()` / `registerMessageRenderer()`.
- Added `get_message_renderer()` / `getMessageRenderer()`.
- Added `get_message_renderers()` / `getMessageRenderers()` for the local renderer-map handoff path.
- Added a regression for renderer registration and lookup.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_extension_runner_message_renderer_registration_and_lookup -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "message_renderer or flag_registration or lifecycle_handlers or extension_command or provider_extension_hooks or tool_call_extension or tool_result_extension or context_extension or before_agent_start"
```

Result: `13 passed, 69 deselected`.
