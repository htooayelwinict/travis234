# Phase 067: Before Agent Start Extension Injection

Status: complete

## Goal

Port Pi's `before_agent_start` extension behavior so extensions can inject custom messages and temporarily modify the system prompt before a model turn begins.

## Reference Files

- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/agent-session-model-extension.test.ts`
- `pi/packages/coding-agent/test/extensions-runner.test.ts`

## Changes

- Added `ExtensionRunner.emit_before_agent_start()` / `emitBeforeAgentStart()`.
- Ported chained system prompt modification semantics.
- Wired `AgentSession.prompt()` so idle model turns call `before_agent_start` after prompt/input processing and before dispatching to the agent loop.
- Converted extension-injected messages into local `CustomMessage` entries so they participate in provider context and session state through the existing custom-message conversion path.
- Reset `agent.state.system_prompt` to the base session prompt before each idle prompt, matching Pi's per-turn modified prompt behavior.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_before_agent_start_injects_custom_message_and_system_prompt -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_before_agent_start_injects_custom_message_and_system_prompt tests/test_coding_agent.py::test_extension_runner_before_agent_start_chains_system_prompt_updates -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "before_agent_start or tool_result_extension or message_end_extension or input_extension or custom_message_next_turn"
```

Result: `9 passed, 63 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `29 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `201 passed`.

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

This closes Pi's `before_agent_start` injection path for custom messages and system prompt changes. The broad appv22 Pi/Hermes parity goal remains active because a strict source-wide completion audit has not proven every in-scope surface.
