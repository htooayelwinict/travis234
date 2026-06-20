# Phase 065: Message End Extension Replacement

Status: complete

## Goal

Port Pi's `message_end` extension replacement behavior so finalized messages can be modified before listeners and session persistence observe them.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/test/suite/agent-session-runtime.test.ts`
- `pi/packages/coding-agent/test/suite/regressions/3982-message-end-cost-override.test.ts`

## Changes

- Added `ExtensionRunner.emit_message_end()` / `emitMessageEnd()`.
- Wired `AgentSession._handle_agent_event()` to call extension `message_end` handlers before session persistence and public listener emission.
- Replaced finalized messages in-place so agent state, event payloads, and persisted session entries stay synchronized.
- Added Pi's same-role validation for replacements; invalid role changes emit an extension error and preserve the original message.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_message_end_extension_replaces_assistant_message -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_message_end_extension_replaces_assistant_message tests/test_coding_agent.py::test_agent_session_message_end_extension_rejects_role_change -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "message_end_extension or input_extension or extension or auto_retry"
```

Result: `10 passed, 58 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `29 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `197 passed`.

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

This closes Pi's finalized-message replacement hook for `message_end` handlers. The broad appv22 Pi/Hermes parity goal remains active because a strict source-wide completion audit has not proven every in-scope surface.
