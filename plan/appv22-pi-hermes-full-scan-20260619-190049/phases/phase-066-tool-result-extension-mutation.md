# Phase 066: Tool Result Extension Mutation

Status: complete

## Goal

Port Pi's `tool_result` extension mutation behavior so extensions can modify tool result content, details, and error state before the result becomes conversation context.

## Reference Files

- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/agent-session-model-extension.test.ts`
- `pi/packages/coding-agent/test/extensions-runner.test.ts`

## Changes

- Added `ExtensionRunner.emit_tool_result()` / `emitToolResult()`.
- Ported chained partial patch behavior for `content`, `details`, and `isError`.
- Wired `AgentSession` into the existing agent-loop `after_tool_call` hook so mutations happen before the `toolResult` message is emitted and before the next model turn sees it.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_tool_result_extension_modifies_result_before_context -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_tool_result_extension_modifies_result_before_context tests/test_coding_agent.py::test_agent_session_tool_result_extension_chains_partial_patches -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "tool_result_extension or message_end_extension or input_extension or extension"
```

Result: `10 passed, 60 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py -q -k "tool_call or after_tool_call or tool_result"
```

Result: `3 passed, 20 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `29 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `199 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check -- appV2.2/appv22 appV2.2/tests plan/appv22-pi-hermes-full-scan-20260619-190049
```

Result: passed before documentation update.

```bash
rg -n "from pi|import pi|from hermes|import hermes|hermes-agent|appV2\.1|appv21|sys\.path.*pi|PYTHONPATH.*pi|importlib.*pi" appV2.2/appv22 appV2.2/tests appV2.2/scripts -g '*.py'
```

Result: only expected docstring/self-test references.

## Reality Check

This closes Pi's `tool_result` mutation path for extension handlers. The broad appv22 Pi/Hermes parity goal remains active because a strict source-wide completion audit has not proven every in-scope surface.
