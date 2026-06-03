# Worker Tooling Hardening Plan

## Goal

Improve worker tool use for greenfield app creation and file/workspace
manipulation while keeping the worker kernel as the control plane for budgets,
permissions, retries, and write scope.

## Research Summary

OpenAI function/tool calling guidance emphasizes named function tools, clear
descriptions, JSON schema parameters, strict schema mode, tool outputs returned
to the model, and optional multiple tool calls in one model turn. The repo
already follows this pattern with `WorkerToolCall`, strict worker decisions, and
named tools. The failure is that we exposed too much environment probing through
a raw command-shaped tool instead of a specific structured tool.

## Acceptance Criteria

- `infra_worker` can inspect local runtime capabilities without shell-chained
  commands.
- Workers can create several scoped files in one tool call.
- Planner can use `filesystem_worker` for file scaffolding/manipulation plans.
- Validator recognizes `filesystem_worker` as write-capable only for MUTATE.
- Existing decompressor/planner/worker tests pass.
- Live greenfield calculator API probe reaches mutation/verification or exposes
  the next concrete blocker with matrix evidence.

## Files To Change

- `app/worker_kernel/tools.py`
- `app/worker_kernel/workers/filesystem_worker.py`
- `app/worker_kernel/workers/agentic_templates.py`
- `app/worker_kernel/workers/__init__.py`
- `app/planner/contracts.py`
- `app/planner/validator.py`
- `app/planner/prompt_chain.py`
- `tests/test_worker_agentic.py`
- `tests/test_planner.py` if validator coverage requires updates

## Phases

1. Structured tools: add `runtime_capabilities`, `write_many_files`, and
   minimal file operation helpers guarded by existing write scope.
2. Worker wiring: add `filesystem_worker` with focused system prompts and tool
   set.
3. Planner wiring: expose worker type in catalog, phase model, validator.
4. Verification: unit/regression tests, broad test suite, live greenfield probe.
5. Iteration: fix only the next concrete blocker if the live run fails before
   producing project files.

## Risks

- Widening command execution would be unsafe; avoid it.
- Batch writes must still validate every path against write scope.
- `filesystem_worker` must not become a bypass around code-worker safety.
- Greenfield plans may need larger mutation scopes than legacy code patches.

## Verification

- `uv run pytest tests/test_worker_agentic.py tests/test_worker_kernel.py -q`
- `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_graph.py tests/test_worker_agentic.py tests/test_worker_kernel.py -q`
- Live no-mock calculator API probe with qwen worker model.

