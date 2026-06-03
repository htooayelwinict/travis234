# Phase 2 - Kernel Preflight Validation

Status: Completed.

Goal: worker kernel validates `Envelope + Plan` before dispatch.

Changes:

- Add optional `validator` dependency to `WorkerKernelRuntime`.
- Validate request ID, artifact dependencies, worker types, permissions, budget, and mutation contracts before running workers.
- Return structured failed/kernel error result for invalid runtime input instead of raw exceptions where appropriate.
- Keep a compatibility mode if needed for legacy tests, but default should become safe.

Acceptance:

- Invalid plans do not dispatch any worker.
- Unknown worker groups become structured kernel errors or validation failures.
- Existing planner-generated plans still run.

Implementation notes:

- Kernel validates `Envelope + Plan` with `PlannerPlanValidator` when an envelope is supplied.
- Direct helper usage without an envelope keeps structural validation only.
- Invalid plans return `kernel_error` instead of dispatching workers.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py tests/test_graph.py
```
