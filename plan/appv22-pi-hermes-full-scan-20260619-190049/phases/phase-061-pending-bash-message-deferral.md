# Phase 061: Pending Bash Message Deferral

Status: complete

## Goal

Port Pi's `AgentSession` pending bash-message behavior so user bash executions recorded during an active model run do not break tool-use/tool-result ordering.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/agent-session-bash-persistence.test.ts`

## Changes

- Added `AgentSession._pending_bash_messages`.
- Added `has_pending_bash_messages` / `hasPendingBashMessages`.
- Changed `record_bash_result()` / `recordBashResult()` to queue `BashExecutionMessage` objects while `session.is_streaming` is true.
- Added `_flush_pending_bash_messages()` and flush calls before and after `_run_agent_prompt()`, matching Pi's prompt-boundary behavior.
- Exported `BashResult` from `appv22.coding_agent`, matching Pi's public coding-agent surface.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_defers_bash_result_while_streaming_then_flushes -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_coding_agent_package_exports_bash_result tests/test_coding_agent.py::test_agent_session_defers_bash_result_while_streaming_then_flushes -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "bash_result or execute_bash or prompt_queues_during_streaming or auto_retry"
```

Result: `5 passed, 57 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `63 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `190 passed`.

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

This closes a newly identified Pi coding-agent mismatch after Phase 060. The broad appv22 Pi/Hermes parity goal remains active because a strict source-wide completion audit has not proven every Pi/Hermes surface is ported.
