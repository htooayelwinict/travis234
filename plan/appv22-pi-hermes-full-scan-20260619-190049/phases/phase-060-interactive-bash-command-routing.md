# Phase 060: Interactive Bash Command Routing

Status: complete

## Goal

Port Pi interactive `!` and `!!` bash command routing so user shell commands run locally instead of being sent to the model.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/bash-execution.ts`

## Changes

- Added `BashResult`.
- Added `AgentSession.execute_bash()` / `executeBash()` using the existing `BashOperations` abstraction and local operations by default.
- Added `AgentSession.record_bash_result()` / `recordBashResult()` to append and persist `BashExecutionMessage`.
- Updated `InteractiveMode` to intercept `! command` and `!! command` before normal prompt/model execution.
- Rendered live bash output through `BashExecutionComponent`.
- Preserved Pi's `!!` behavior by marking the message `excludeFromContext` and verifying LLM conversion skips it.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py tests/test_tui.py -q -k "execute_bash_records or bang_runs_bash"
```

Result: `2 passed, 86 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py tests/test_tui.py -q
```

Result: `88 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `188 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check -- appV2.2/appv22 appV2.2/tests plan/appv22-pi-hermes-full-scan-20260619-190049
```

Result: passed before documentation update.

## Reality Check

This closes the execution/routing half of Pi's bash mode after Phase 059 added the message and renderer. The broad goal remains active until a strict audit proves no remaining in-scope Pi/Hermes surfaces are missing.
