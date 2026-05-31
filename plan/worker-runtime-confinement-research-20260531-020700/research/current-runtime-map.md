# Current Runtime Map

## Question

What do the current decompressor, planner, schemas, validator, graph, and worker kernel already guarantee, and where does the next worker-runtime boundary need to start?

## Summary

The current system has a clear upstream contract split:

- The decompressor emits a descriptive-only `Envelope` and strips planner/kernel-shaped leakage.
- The planner emits a validated phase-aware `Plan` with artifact lineage, permissions, phase/mode/task metadata, mutation-scope discipline, rollback discipline, and verification-after-mutation discipline.
- The worker kernel currently executes as a simple deterministic stub boundary. It enforces budget and passes step metadata into `Task`, but it does not yet enforce runtime file/command/tool confinement, does not receive the `Envelope`, does not resolve write-scope artifacts into concrete paths, and does not validate worker-produced artifacts against expected outputs.

This means the next phase should not revisit decompressor/planner semantics. It should build the worker kernel as a runtime enforcement host for already-declared contracts.

## Decompressor Boundary

Source pointers:

- `app/decompressor/prompt_chain.py`
- `app/decompressor/runtime.py`
- `app/decompressor/canonicalize.py`
- `app/decompressor/redaction.py`
- `app/decompressor/contracts.py`
- `app/schemas.py`

Current behavior:

- `DecompressorRuntime.run(user_input)` creates a runtime-owned `request_id` and calls the LLM prompt chain.
- `LLMPromptChainDecompressor` prompts the model to describe the request only.
- The prompt explicitly says not to plan execution, choose workers, create steps, or set budgets.
- Redaction runs before model-bound prompt text.
- The LLM output validates through `DecompressedEnvelope` with `extra="forbid"`.
- `canonicalize_envelope()` strips forbidden planner/kernel-shaped keys recursively.

Important `Envelope` fields:

- `request_id`
- `raw_input`
- `normalized_input`
- `user_goal`
- `input_type`
- `intents`
- `domains`
- `risks`
- `artifacts`
- `context_needed`
- `constraints`
- `complexity_hint`
- `confidence`
- `ambiguity`
- `assumptions`
- `metadata`

Current decompressor safety boundary:

- Descriptive-only.
- Does not emit planner hints, execution hints, budget hints, worker types, steps, or tool limits.
- `FORBIDDEN_ENVELOPE_FIELDS` removes keys like `planner_hint`, `execution_hints`, `budget_hint`, `steps`, `strategy`, `worker_type`, `max_tool_calls`, and `max_model_calls`.
- The decompressor may preserve `artifacts` as concrete nouns from input, but those are not runtime artifacts.

Implication for worker runtime:

- The worker kernel should not consume `Envelope.artifacts` as executable artifact inputs or write scope.
- If the kernel receives `Envelope`, it should use selected fields as audit/context/safety provenance only unless a prior worker has converted them into runtime artifacts.
- Any future envelope-to-kernel passage must preserve the decompressor boundary: descriptive signals, not execution authority.

## Planner Boundary

Source pointers:

- `app/planner/prompt_chain.py`
- `app/planner/validator.py`
- `app/planner/contracts.py`
- `app/planner/runtime.py`
- `tests/test_planner.py`

Current planner behavior:

- `PlannerRuntime.run(envelope)` calls `LLMPlanCompiler` when configured.
- Planner prompt emits a `Plan` JSON and can repair invalid plans through bounded LLM repair passes.
- Deterministic code validates contracts but does not synthesize semantic plans.
- Budget normalization is arithmetic-only.
- Planner metadata records prompt-chain diagnostics.

Planner contract constants:

- Allowed worker types: `direct_worker`, `repo_worker`, `code_worker`, `research_worker`, `infra_worker`, `verify_worker`.
- Allowed modes: `observe_only`, `plan_only`, `bounded_mutation`, `verify_only`, `summarize_only`.
- Write-scope artifact names/signals: `mutation_scope`, `allowed_write_paths`, `writable_targets`, `patch_scope`.

Important `Plan` fields:

- `plan_id`
- `request_id`
- `planner`
- `objective`
- `strategy`
- `execution_pattern`
- `steps`
- `budget`
- `global_invariants`
- `success_criteria`
- `metadata`

Important `PlanStep` fields:

- `step_id`
- `worker_type`
- `phase`
- `mode`
- `task_id`
- `instruction`
- `input_artifacts`
- `output_artifacts`
- `max_tool_calls`
- `max_model_calls`
- `permissions`

Validated planner guarantees:

- `plan.request_id` matches `envelope.request_id`.
- `plan_id` is non-empty.
- `plan.planner` is not a worker type.
- Step IDs are unique.
- Worker types are known.
- Phase-aware plans populate every `step.phase`, `step.mode`, `step.task_id`, top-level `execution_pattern`, and top-level `global_invariants`.
- Mode values are constrained by schema and phase-to-mode mapping.
- Phase order cannot regress.
- No steps after `FINALIZE`.
- `FINALIZE` outputs a final artifact.
- Every step declares boolean `read_files`, `write_files`, and `run_commands` permissions.
- Plan budget covers step tool/model calls and worker count.
- Every `step.input_artifact` must be produced by an earlier step.
- `write_files=true` is only allowed for `code_worker`.
- Any write step must be `MUTATE` when phase-aware.
- `MUTATE` must set `write_files=true` and `read_files=true`.
- `MUTATE` must consume write scope and root-cause/evidence/fix-design context.
- Write steps must use literal specific `write_paths` or `write_paths_from_artifacts`.
- `write_paths_from_artifacts` must reference earlier DESIGN-produced write-scope artifacts.
- Mutation requires a pre-write rollback/revert artifact and must consume it.
- Mutation requires prior DESIGN output `mutation_scope`, `rollback_plan`, and `verification_plan` or `test_plan`.
- Mutation requires a later `verify_worker` step.
- Post-mutation verification must consume `change_summary`, write scope, and evidence/root-cause artifacts.
- Verification must consume mutation outputs and output verification/test artifacts.

Direct support archetype:

- One `direct_worker` step.
- `phase="FINALIZE"`.
- `mode="summarize_only"`.
- `task_id="direct_support"`.
- `input_artifacts=[]`.
- `output_artifacts=["direct_guidance"]`.
- All permissions false.
- Budget allows no tools and one model call.

Instruction context block policy:

- Every generated `step.instruction` should begin with labels in order:
- `Known facts:`
- `Unknowns:`
- `Do now:`
- `Do not do:`
- `Output:`

Current validation limitation:

- The validator does not parse instruction context block internals.
- This is intentional so deterministic code does not become a semantic planner.

Implication for worker runtime:

- Worker kernel can rely on a validated plan shape but must still enforce runtime behavior.
- The worker kernel should not repair or reinterpret planner decisions.
- If runtime confinement detects missing concrete scope, it should block, return a structured `Result`, and possibly expose replan-worthy diagnostics, not mutate the plan.

## Graph Boundary

Source pointer:

- `app/graph.py`

Current graph:

- `START -> decompressor_node -> planner_node -> worker_kernel_node -> END`

Current node handoff:

- Decompressor node stores `envelope` in state.
- Planner node validates state `envelope` and stores `plan` in state.
- Worker kernel node validates state `plan` and calls `worker_kernel_runtime.run(plan)`.

Important gap:

- The current worker kernel receives only `Plan`, not `Envelope`.
- The graph state contains both, so passing selected envelope context later is straightforward without changing upstream contracts.

Implication for worker runtime:

- A future worker-kernel API could accept `run(plan, envelope=None)` or a `KernelRunContext` containing `plan` plus selected envelope provenance.
- The initial implementation can be additive if tests preserve `run(plan)` compatibility, but a breaking change may be acceptable if explicitly planned.

## Worker Kernel Boundary

Source pointers:

- `app/worker_kernel/runtime.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/budget.py`
- `app/worker_kernel/dispatcher.py`
- `app/worker_kernel/registry.py`
- `app/worker_kernel/workers/*.py`
- `tests/test_worker_kernel.py`

Current runtime behavior:

- Creates `run_id=f"run_{plan.plan_id}"`.
- Builds `BudgetGate(plan.budget)`.
- Runs `budget_gate.check_plan(plan)`.
- Maintains an in-memory artifact store keyed by artifact ID.
- Compiles each `PlanStep` to `Task` using `TaskCompiler`.
- Calls `budget_gate.before_task(task)`.
- Dispatches task to worker by `worker_type`.
- Calls `budget_gate.after_result(result)`.
- Stores produced artifacts by `id` or `artifact_id`.
- Stops on result statuses `failed`, `blocked`, or `budget_exceeded`.
- Returns aggregate `Result` with final artifacts, usage, and worker result metadata.

Current `TaskCompiler` behavior:

- Resolves each named `step.input_artifact` from prior `artifact_store` if present.
- Silently omits input artifact names not found in the store.
- Copies `phase`, `mode`, and `task_id` into `Task.metadata`.
- Copies `instruction`, expected outputs, call limits, and permissions.

Current `BudgetGate` behavior:

- Validates non-empty plan and non-negative per-step call maxima.
- Rejects plans whose requested workers/tool calls/model calls exceed budget.
- Rejects tasks that would exceed cumulative budget before dispatch.
- Adds actual usage after result and rejects over-budget worker output.

Current worker stubs:

- `DirectWorker` returns instruction as content under first expected output.
- `RepoWorker` returns a generic repository scan artifact.
- `ResearchWorker` returns generic research notes.
- `InfraWorker` returns generic infra recommendations.
- `CodeWorker` returns generic code change artifact and marks whether `write_files` was true.
- `VerifyWorker` returns generic focused checks artifact and lists input IDs.

Current worker-kernel guarantees:

- Budget is enforced before dispatch and after reported result usage.
- Unknown workers fail through registry lookup.
- Phase/mode/task metadata reaches workers.
- Results are aggregated with metadata.

Current worker-kernel gaps:

- It does not receive or record envelope context.
- It does not validate that `Task.permissions` are explicitly boolean at runtime.
- It does not validate phase/mode permissions at runtime.
- It does not ensure `direct_worker` has no tools/files/commands at runtime.
- It does not resolve `permissions.write_paths_from_artifacts` into concrete paths.
- It does not validate that artifacts used as write scope have a structural schema containing allowed paths.
- It does not inspect or gate actual filesystem reads/writes.
- It does not gate shell commands or command arguments.
- It does not gate network access.
- It does not validate that each worker result produced all expected output artifact IDs.
- It does not reject unexpected artifact IDs.
- It does not validate artifact IDs for duplicates/collisions beyond last-write-wins behavior.
- It does not track artifact provenance beyond result metadata.
- It does not enforce artifact immutability.
- It silently omits missing input artifacts in `TaskCompiler`; planner validation should prevent this, but runtime should still fail closed.
- It does not distinguish planner contract validation errors from runtime confinement violations in result metadata.
- It does not implement rollback/revert execution if mutation or verification fails.
- It does not stop on verification content indicating failed checks because current workers are stubs and result semantics are not structured.

## Prior Planning Context

Source pointers:

- `plan/langgraph-runtime-architecture-20260528-233454/phases/phase-3-worker-kernel.md`
- `plan/constraint-phase-planner-runtime-20260530-171038/phases/phase-4-kernel-alignment-and-integration.md`
- `plan/constraint-phase-planner-runtime-20260530-171038/research/planner-contract-discipline-qa.md`
- `plan/constraint-phase-planner-runtime-20260530-171038/research/planner-contract-hygiene-qa-2.md`
- `plan/constraint-phase-planner-runtime-20260530-171038/research/artifact-write-scope-semantics.md`
- `plan/constraint-phase-planner-runtime-20260530-171038/research/brainstorm-user-reply-replan-vs-resume-20260531-010346.md`

Existing decisions relevant to worker runtime:

- Keep the graph simple and terminal for now.
- Do not add kernel-level automatic replan/resume loops yet.
- Treat user replies as fresh decompressor/planner runs for now.
- Planner contract tightened structural guarantees, but actual runtime enforcement remains a worker-kernel follow-up.
- Write-scope semantics intentionally require DESIGN to narrow broad discovery candidates into explicit scope artifacts.

## Research Conclusion

The next worker-runtime phase should start at the seam where the planner stops: runtime execution of a validated plan. The kernel should absorb:

- `Plan` as the executable declaration.
- `PlanStep.permissions` as capability declarations.
- Prior worker artifacts as the only runtime artifact inputs.
- Selected `Envelope` fields as provenance and policy context, not as runtime artifact inputs.

The kernel should confine:

- Artifact resolution.
- File reads and writes.
- Commands.
- Tool/model usage.
- Worker output artifact shape.
- Step status transitions.

The kernel should not:

- Generate or repair plans.
- Decide semantic intent.
- Convert envelope artifacts directly into runtime inputs.
- Broaden write scope based on raw user text.
- Hide contract problems by silently omitting missing inputs or fabricating artifacts.
