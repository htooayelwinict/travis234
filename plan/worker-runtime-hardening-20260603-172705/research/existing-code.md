# Existing Code Notes

Current relevant files:

- `app/schemas.py`
  - Defines loose shared contracts: `Envelope`, `PlanStep`, `Plan`, `ReplanRequest`, `Task`, `Result`, `RuntimeState`.
  - `PlanStep.permissions`, `Task.permissions`, `Result.status`, artifacts, and usage are currently loose dictionaries/strings.

- `app/planner/validator.py`
  - Strong planner output validation exists, but it is called by the planner prompt chain, not by the worker kernel.
  - It checks request ID, worker type, phase/mode/task contracts, artifact dependencies, budgets, write scope, mutation safety, verification, and phase progression.

- `app/worker_kernel/runtime.py`
  - Sequential executor.
  - Runs `BudgetGate.check_plan(plan)`.
  - Compiles one task per step.
  - Dispatches one worker per step.
  - Handles `needs_replan` internally by calling `planner_runtime.replan(envelope, plan, replan_request)`.
  - Tracks completed step IDs only when `result.status == "completed"`.

- `app/worker_kernel/compiler.py`
  - Silently ignores missing `step.input_artifacts`.
  - This is a key bug for real worker execution.

- `app/worker_kernel/budget.py`
  - Tracks tool/model/worker usage.
  - Has `max_retries` and `retries_used`, but retry usage is not implemented.

- `app/worker_kernel/registry.py`
  - Maps `worker_type` to one worker object.
  - Future direction: registry should map `worker_type` to worker-group definitions or group runners.

- `app/worker_kernel/workers/*.py`
  - Current workers are placeholders. They return canned success artifacts.

- `tests/test_worker_kernel.py`
  - Existing coverage verifies direct execution, budget rejection, replan request generation, internal replan call, completed step IDs, and metadata propagation.
  - Several tests use loose permissions missing `web_research`; transition should either normalize those or update tests with strict permissions.

Current behavior to preserve:

- Internal replan path.
- `ReplanRequest.completed_step_ids`.
- `Result.metadata["replan"]` including request, replacement plan, original worker results, and depth.
- Existing planner/decompressor test pass.

Current behavior to change:

- Missing artifacts cannot be silently dropped.
- Unknown worker and worker exceptions should return structured kernel failure instead of escaping as raw exceptions.
- `max_retries` should become real attempt budget logic.
- Worker outputs should not be trusted unless completed and contract-valid.
