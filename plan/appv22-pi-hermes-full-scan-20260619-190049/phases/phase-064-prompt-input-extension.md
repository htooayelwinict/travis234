# Phase 064: Prompt Input Extension Hook

Status: complete

## Goal

Port Pi's prompt-level `input` extension interception into appv22's coding-agent session.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/test/extensions-input-event.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-model-extension.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-prompt.test.ts`

## Changes

- Added `ExtensionRunner.emit_input()` / `emitInput()`.
- Ported chained input transforms: handlers can return `{"action": "transform", "text": ...}` and optional replacement `images`.
- Ported input `handled` short-circuit: handled prompts return without model execution.
- Wired `AgentSession.prompt()` to emit `input` before idle prompt dispatch or streaming queueing.
- Preserved Pi's `streamingBehavior` visibility rule: handlers see it only while the agent is currently streaming.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_input_extension_transforms_and_handles_prompt tests/test_coding_agent.py::test_agent_session_input_extension_sees_streaming_behavior_before_queue -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "input_extension or prompt_queues_during_streaming or extension or custom_message_next_turn"
```

Result: `8 passed, 58 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `29 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `195 passed`.

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

This closes Pi's prompt-level `input` extension interception for transform, handled, and streaming-behavior event data. The broad appv22 Pi/Hermes parity goal remains active because a strict source-wide completion audit has not proven every in-scope surface.
