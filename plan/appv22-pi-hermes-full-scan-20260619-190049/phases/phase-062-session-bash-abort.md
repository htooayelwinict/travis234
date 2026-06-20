# Phase 062: Session Bash Abort API

Status: complete

## Goal

Port Pi's session-level user-bash cancellation API so interactive/RPC callers can detect and abort a running bash command without aborting the model run.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/agent-session-bash-persistence.test.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `pi/packages/coding-agent/src/modes/rpc/rpc-mode.ts`

## Changes

- Added a per-user-bash `AbortSignal` on `AgentSession`.
- Added `abort_bash()` / `abortBash()`.
- Added `is_bash_running` / `isBashRunning`.
- Changed `execute_bash()` to pass the user-bash signal into `BashExecOptions`, reset running state in `finally`, and preserve the existing cancelled-result behavior for `RuntimeError("aborted")`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_abort_bash_cancels_running_command -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "bash_result or execute_bash or abort_bash or pending_bash or package_exports_bash_result"
```

Result: `4 passed, 60 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `64 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `191 passed`.

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

This closes the session-level half of Pi's user-bash cancellation API. The broad appv22 Pi/Hermes parity goal remains active because the source-wide completion audit is still not proven.
