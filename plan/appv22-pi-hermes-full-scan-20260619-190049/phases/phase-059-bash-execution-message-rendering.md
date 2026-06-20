# Phase 059: Bash Execution Message Rendering

Status: complete

## Goal

Port Pi's `bashExecution` message model and TUI rendering path for user `!` / `!!` bash command history.

## Reference Files

- `pi/packages/coding-agent/src/core/messages.ts`
- `pi/packages/coding-agent/src/core/session-manager.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/bash-execution.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`

## Changes

- Added `BashExecutionMessage` with Pi field aliases: `exitCode`, `fullOutputPath`, and `excludeFromContext`.
- Added JSONL serialize/deserialize support for `role="bashExecution"` messages.
- Updated coding-agent LLM conversion so bash executions become user messages with Pi's `Ran \`command\`` fenced-output text.
- Honored `excludeFromContext` by skipping excluded bash executions during LLM conversion.
- Added branch-summarization conversion support for bash execution messages.
- Added `BashExecutionComponent` with running/completed status, collapsed preview, expanded output, exit/cancel/truncation display, full-output path display, and `[no context]` labeling.
- Updated `message_to_component()` to render existing bash execution history entries.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py tests/test_tui.py -q -k "bash_execution"
```

Result: `2 passed, 84 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py tests/test_tui.py -q
```

Result: `86 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `186 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check -- appV2.2/appv22 appV2.2/tests plan/appv22-pi-hermes-full-scan-20260619-190049
```

Result: passed before documentation update.

## Reality Check

This closes another concrete Pi coding-agent/TUI mismatch. The broad goal remains active because strict full-source parity still has to be proven by continuing targeted scans against newly identified Pi/Hermes surfaces.
