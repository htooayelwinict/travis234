# Worker Runtime Readiness: Envelope and Plan Quality

## Problem Statement
Assess whether the live output in `plan/live-test-file-edit-research-20260602-175207.json` has enough schema quality and runtime context to drive a real multi-instance worker runtime for a file-editing + research task. Also decide how a worker kernel should reroute when it needs replanning while keeping workflow complexity low.

## Constraints
- Do not code yet.
- Direct-path routing before decompressor is assumed separately and is not the focus.
- Current graph path is decompressor -> planner -> worker kernel.
- Current worker kernel compiles each `PlanStep` into a `Task` using only step instruction, matched input artifacts, expected outputs, permissions, budgets, and step metadata.
- Current worker kernel executes steps sequentially by list order.

## Observed Output Quality
The envelope is good for planner input. It captures the user goal, high complexity, mutation risk, research requirement, ambiguity, constraints, and needed context.

The plan is good as a semantic execution recipe. It has phase order, worker types, instructions, permissions, artifacts, invariants, budget, and success criteria.

The plan is not yet sufficient as a robust multi-instance worker-runtime contract. It relies on sequential list order and text instructions instead of explicit dependency edges and explicit worker context contracts.

## Necessary Fields
Envelope:
- `request_id`
- `normalized_input`
- `user_goal`
- `input_type`
- `intents`
- `risks`
- `artifacts`
- `context_needed`
- `constraints`
- `ambiguity`
- `assumptions`

Plan:
- `plan_id`
- `request_id`
- `objective`
- `steps`
- `budget`
- `global_invariants`
- `success_criteria`

Step:
- `step_id`
- `worker_type`
- `phase`
- `mode`
- `task_id`
- `instruction`
- `input_artifacts`
- `output_artifacts`
- `permissions`
- `max_tool_calls`
- `max_model_calls`

## Fields That Are Useful but Not Runtime-Critical
Envelope:
- `raw_input`: useful for audit/replan, but should not always be sent to every worker.
- `domains`: useful for planner routing, less useful to workers after plan compilation.
- `complexity_hint`: useful for planner budget/shape, not a worker execution field.
- `confidence`: useful for gating/replan policy, not a worker execution field.
- `metadata`: useful for observability, not worker action.

Plan:
- `planner`: useful for traceability only.
- `strategy`: useful for humans/debugging, but workers mostly need objective + step contracts.
- `execution_pattern`: useful for validation and traceability, but not enough for parallel dispatch.
- `metadata`: useful for diagnostics and model provenance.

## Missing or Weak for Multi-Instance Runtime
1. `depends_on` per step.
Current order is implicit. Multi-instance workers need explicit dependency edges so independent steps can run in parallel and dependent steps wait correctly.

2. A worker-visible objective snapshot.
Workers currently do not get the top-level objective/global invariants unless the planner repeats them in step instructions. This can work, but it is fragile. A small `task_context` or `objective_snapshot` should be passed with every task.

3. Artifact contracts.
The plan says artifact names such as `algorithm_spec`, `mutation_scope`, and `comparison_evidence`, but does not define required shape, minimum fields, source/citation rules, or blocking states. Real workers need artifact expectations, not just artifact names.

4. Replan policy.
There is no first-class status such as `needs_replan`, `needs_clarification`, or `blocked_missing_context` with a structured payload for the planner.

5. Nonzero operational budgets.
The live plan has `max_tool_calls=0` for all steps, including discovery, web research, mutation, and verification. That is incompatible with real workers that need to read files, search the web, edit files, or run checks. It is acceptable only for stub/LLM-only roleplay workers.

6. Parallel execution policy.
The plan has phases but no `parallel_group`, `depends_on`, or readiness rule. A real multi-instance runtime should not infer parallel safety from phase names alone.

## Evaluated Replan Options
### Option A: Worker Kernel Sends Failed Step Back Through Decompressor
- Feasibility: easy with current graph shape.
- Risk: high.
- Issue: decompressor is for user input compression, not execution-state recovery. It will re-interpret a runtime failure as a new user request and burn unnecessary tokens.

### Option B: Worker Kernel Calls Planner Directly With Replan Context
- Feasibility: high.
- Risk: low-to-medium.
- Fit: best match. Planner owns plan structure, dependencies, budgets, worker selection, and repair.
- Complexity: moderate but localized.

### Option C: Worker Kernel Handles All Replanning Internally
- Feasibility: medium.
- Risk: medium-to-high.
- Issue: kernel becomes planner-like and duplicates policy logic.

### Option D: Full Graph Restart From Original User Input
- Feasibility: high.
- Risk: high.
- Issue: loses completed artifacts unless carefully preserved and adds token burn.

## Recommended Reroute Path
Use direct planner reroute, not decompressor reroute.

Flow:
```text
worker returns needs_replan
  -> worker_kernel stops current branch
  -> worker_kernel creates replan_request artifact
  -> planner_runtime.replan(original_envelope_summary, current_plan, completed_artifacts, failed_step, reason)
  -> planner returns plan_patch or replacement continuation_plan
  -> worker_kernel resumes from the changed step(s)
```

Keep it simple at first:
- `needs_replan` status from worker result.
- `replan_request` artifact with `failed_step_id`, `reason`, `missing_context`, `completed_artifacts`, `budget_remaining`, and `recommended_action`.
- Planner returns a continuation plan, not a full restart.

## Recommended Path
The current envelope and plan schemas are enough for LLM planning and sequential stub-worker execution. They are not enough yet for reliable real multi-instance worker execution.

Do not add more broad fields to Envelope. Keep Envelope as planner-facing.

Strengthen Plan/Task contracts instead:
- add explicit step dependencies
- add a compact objective/global-invariant context passed to every task
- add artifact contracts
- add worker result statuses for `needs_replan` and `needs_clarification`
- make tool budgets match permissions

## MCP Second Opinion Kept
Open Bridge independently flagged the same issues: missing dependency graph, worker context drift risk, and the mismatch between permissions and `max_tool_calls=0`. It recommended a structured escalation loop where workers return a replan-needed result and the kernel routes directly to planner with the failed step and accumulated artifacts.

## Next Steps
1. Fix planner output quality so tool budgets are nonzero when permissions imply real operations.
2. Add a minimal replan contract before adding advanced parallelism.
3. Add `depends_on` or equivalent explicit dependency edges before multi-instance dispatch.
4. Pass a compact objective/global-invariant snapshot to workers, not the full envelope.

## Saved Path
`brainstorm/worker-runtime-envelope-plan-quality-20260602.md`
