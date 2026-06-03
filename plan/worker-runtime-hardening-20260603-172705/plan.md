# Worker Runtime Hardening Implementation Plan

## Goal

Build a strict worker kernel runtime that accepts `Envelope + Plan`, validates the plan, compiles safe tasks, runs worker groups through kernel-controlled instance attempts, classifies failures, retries instance failures, triggers internal replans for planner-level failures, and finalizes results with auditable artifacts and logs.

## Implementation Status

Completed in the first hardening pass:

- strict worker-facing schema additions
- kernel preflight validation
- missing runtime artifact handling
- separated completed/partial/failed artifact stores
- structured issue taxonomy
- retry budget accounting
- single-instance and sequential worker-group runners
- internal replan payload cleanup

Deferred:

- real tool-capable worker implementations
- live LLM workflow verification with real worker execution

## Non-goals

- Do not combine decompressor and planner.
- Do not route replan through LangGraph state yet.
- Do not make planner output internal worker instance steps.
- Do not implement every real worker tool in the first phase.

## Target Architecture

```text
Graph state
  user_input
  envelope
  plan
      |
      v
WorkerKernelRuntime
  validate envelope + plan
  initialize KernelRunState
  for each PlanStep:
    compile Task
    resolve required artifacts
    run worker group
      spawn instance attempt(s)
      classify instance vs plan failure
      retry/replace instance failures
      return plan-level failure as ReplanSignal
    store completed artifacts only
    keep partial/failed artifacts separate
  if plan failure:
    build ReplanRequest
    PlannerRuntime.replan(...)
    run replacement plan once
  finalize Result
```

## Contract Model

Add or tighten these worker-facing schemas in `app/schemas.py`:

- `PermissionSet`
  - `read_files: bool`
  - `write_files: bool`
  - `run_commands: bool`
  - `web_research: bool`
  - `write_paths: list[str]`
  - `write_paths_from_artifacts: list[str]`

- `ArtifactRef`
  - `id: str`
  - optional `producer_step_id`
  - optional `producer_worker_type`

- `ArtifactPayload`
  - `id: str`
  - `content: Any`
  - `kind: str | None`
  - `producer: str | None`
  - `step_id: str | None`
  - `attempt_id: str | None`
  - `trust_level: str`
  - `metadata: dict[str, Any]`

- `ResultStatus`
  - `completed`
  - `failed`
  - `blocked`
  - `budget_exceeded`
  - `needs_replan`
  - `kernel_error`

- `WorkerIssue`
  - `issue_type: instance_failure | plan_failure | kernel_failure`
  - `code: str`
  - `message: str`
  - `step_id: str | None`
  - `worker_type: str | None`
  - `attempt_id: str | None`
  - `retryable: bool`
  - `metadata: dict[str, Any]`

- `ReplanSignal`
  - `reason: str`
  - `failed_step_id: str`
  - `issue_codes: list[str]`
  - `recommended_action: str | None`
  - `partial_artifacts: list[ArtifactPayload]`
  - `metadata: dict[str, Any]`

- Optional later: `WorkerGroupPolicy`
  - `max_attempts`
  - `instance_roles`
  - `parallelism`
  - `timeout_seconds`
  - `failure_policy`

Important schema choice:

Keep `PlanStep.worker_type` as the planner-visible group name. The worker kernel/registry decides internal instance roles.

## Failure Classification

Instance-level failures:

- LLM API error
- tool call error
- command timeout
- command unavailable
- worker implementation exception
- instance max tool calls exceeded
- instance max model calls exceeded
- transient source fetch failure

Kernel behavior:

- record `WorkerIssue(issue_type="instance_failure")`
- increment retry/attempt budget
- replace/retry worker instance when policy allows
- return terminal `failed` only after attempts are exhausted

Plan-level failures:

- missing required artifact
- artifact does not match instruction
- discovered repo differs from planner assumption
- mutation scope missing or too broad
- required web/source evidence unavailable
- dependency/package not present when plan assumed it
- target path not found
- verification contradicts root-cause assumption
- worker cannot proceed without changing plan shape

Kernel behavior:

- record `WorkerIssue(issue_type="plan_failure")`
- build `ReplanRequest`
- call `PlannerRuntime.replan(envelope, current_plan, replan_request)`
- run the replacement plan internally

Kernel-level failures:

- invalid plan after validation
- invalid schema payload
- unknown worker group
- registry misconfiguration
- replan recursion ceiling reached
- planner runtime unavailable when replan required

Kernel behavior:

- return structured `Result(status="kernel_error" or "failed")`
- include issue log in metadata
- do not mutate or continue blindly

## What The User Missed

1. Artifact lifecycle needs to be explicit.
   Completed artifacts, partial artifacts, failed-step artifacts, and replan evidence should not share one undifferentiated store.

2. Worker group orchestration needs a policy source.
   Planner should not specify every internal instance. Registry should provide defaults like source finder, scraper, formatter for `web_research_worker`.

3. Worker instance logs need stable IDs.
   Without `attempt_id`, future debugging will be painful.

4. Tool permissions need enforcement at kernel/tool adapter boundary.
   Typed permissions alone are not enough if a worker can bypass them.

5. Cancellation and timeout policy are needed before parallel workers.
   Especially for web research and command-running workers.

6. Backward compatibility needs an explicit bridge.
   Existing tests and old plan logs use looser permission dicts. Normalize first, enforce strictly after test migration.

## Files To Change

- `app/schemas.py`
- `app/worker_kernel/runtime.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/budget.py`
- `app/worker_kernel/dispatcher.py`
- `app/worker_kernel/registry.py`
- `app/worker_kernel/workers/base.py`
- `app/worker_kernel/workers/*.py`
- `app/planner/validator.py`
- `tests/test_worker_kernel.py`
- `tests/test_planner.py`
- `tests/test_graph.py`

Optional new files:

- `app/worker_kernel/contracts.py`
- `app/worker_kernel/issues.py`
- `app/worker_kernel/group.py`
- `app/worker_kernel/state.py`
- `app/worker_kernel/artifacts.py`

Recommendation: add new worker-kernel modules instead of overloading `runtime.py`.

## Phases

1. Contract and compatibility layer
2. Kernel preflight validation
3. Artifact resolution and missing-artifact failure
4. Issue taxonomy and structured failure handling
5. Real retry accounting and instance attempt policy
6. Worker group registry and group runner
7. Replan payload cleanup
8. Worker implementation migration
9. End-to-end verification and live LLM workflow tests

## Verification

Primary test command:

```bash
uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_kernel.py tests/test_graph.py
```

Focused command:

```bash
uv run pytest tests/test_worker_kernel.py
```

Additional future tests:

- invalid plan rejected by kernel
- missing artifact produces `needs_replan` with `missing_artifacts`
- instance failure retries and records attempt logs
- exhausted instance retries returns failed result with issue log
- plan failure builds `ReplanRequest`
- replan request excludes failed-step artifacts from completed artifacts
- worker group runs multiple instances under one `worker_type`
- budget counts attempts, model calls, tool calls, and worker group activity

## Rollback Strategy

Each phase should preserve the previous public `Plan`, `Task`, and `Result` creation patterns as much as possible. Use Pydantic compatibility to accept dict permissions while normalizing into typed contracts.

Avoid changing planner prompt output until the kernel can normalize old and new shapes.

If a phase breaks live planner output, rollback by disabling strict enforcement at kernel boundary while keeping schema additions.
