# LLM Powered Worker Instances And Type Separation Research

Date: 2026-05-31

## Question

How should the worker runtime become LLM-powered without making the worker kernel a semantic planner, and how should kernel, worker instance, artifact, capability, and model-facing types be separated?

## Executive Summary

The safest direction is not an LLM-powered kernel. The worker kernel should remain deterministic and become the supervisor, instance factory, budget gate, capability gate, artifact store, and result validator. LLMs should power worker instances through kernel-injected model capabilities.

Recommended principle:

> The kernel owns authority. Worker instances own bounded reasoning.

In practice, a `WorkerKernelRuntime` should create a per-step worker instance context from a validated `PlanStep`, prior runtime artifacts, selected envelope provenance, and kernel policy. If `max_model_calls > 0`, the context may expose a metered `WorkerModelClient`. If `max_model_calls == 0`, the worker receives no model capability. Model outputs should be structured JSON validated into runtime artifacts, not free-form state that the kernel trusts automatically.

## Source Pointers

- `app/schemas.py` currently defines the shared `Envelope`, `Plan`, `PlanStep`, `Task`, `Result`, and graph state models.
- `app/worker_kernel/runtime.py` currently coordinates budget, task compilation, dispatch, artifact aggregation, and stop-on-failure behavior.
- `app/worker_kernel/compiler.py` currently converts each `PlanStep` into a `Task` and resolves prior artifacts, but silently omits missing inputs.
- `app/worker_kernel/dispatcher.py` and `app/worker_kernel/registry.py` currently route tasks by `worker_type` to long-lived registered worker objects.
- `app/worker_kernel/workers/*.py` currently implement deterministic stubs with `run(task) -> Result`.
- `app/decompressor/contracts.py` and `app/planner/contracts.py` already define small model-client protocols based on `complete_json(stage, prompt, schema)`.
- `app/decompressor/model_client.py` already provides an OpenAI/OpenRouter-compatible JSON model client wrapper.
- `app/planner/prompt_chain.py` already demonstrates the important pattern: model proposes structured JSON, deterministic code validates, and bounded repair happens before runtime trust.
- `plan/worker-runtime-confinement-research-20260531-020700/research/worker-kernel-absorption-and-confinement.md` already recommends a capability-constrained kernel, typed artifacts, scope resolution, and fail-closed runtime gates.

## Current Architecture Facts

The runtime chain is:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

Current authority split:

- Decompressor understands the user request and emits descriptive provenance.
- Planner chooses the execution contract and emits validated steps, phases, modes, permissions, budgets, and artifact dependencies.
- Worker kernel executes the plan sequentially with a budget gate and deterministic stub workers.

Current LLM pattern:

- Decompressor and planner can be LLM-backed.
- Both use JSON schema outputs and deterministic validation.
- Tests use fake model clients rather than live model calls.
- Planner repair is model-driven and bounded, while deterministic code avoids semantic AST mutation.

This should carry into worker runtime: LLMs can draft worker artifacts, but deterministic kernel gates decide whether those artifacts become runtime facts.

## Possibility Map

### Option 1: LLM-Powered Kernel

Description:

- The kernel itself calls an LLM to interpret plan steps, choose tools, repair missing inputs, decide whether scope is safe, or dynamically select the next action.

Pros:

- Fastest path to flexible behavior.
- Could compensate for imperfect planner output.

Cons:

- Violates the established boundary where the planner is semantic authority and the kernel enforces.
- Makes runtime behavior harder to test and audit.
- Risks converting envelope hints or instruction prose into execution authority.
- Makes budget and permission failures ambiguous because the kernel may try to reason around them.

Recommendation:

- Avoid. The kernel may format prompts or validate model call usage, but it should not ask an LLM what the plan means or how permissions should change.

### Option 2: Workers Own Their Own Model Clients

Description:

- Each worker object is initialized with a model client and calls it internally.

Pros:

- Simple worker implementation.
- Mirrors many agent libraries.

Cons:

- Harder for the kernel to enforce per-step `max_model_calls`.
- Workers can accidentally bypass model policy, provider configuration, tracing, and redaction rules.
- Long-lived worker singletons can leak state between tasks unless carefully reset.

Recommendation:

- Acceptable only as a temporary test harness with fake clients. Not the main architecture.

### Option 3: Kernel-Injected Worker Model Capability

Description:

- The kernel creates a per-step `WorkerContext` and injects a metered `WorkerModelClient` only when the step allows model calls.
- Worker code calls `context.model.complete_json(...)` and receives structured data.
- The context records stage, prompt hash/metadata, schema, model usage, and errors.

Pros:

- Preserves deterministic kernel authority.
- Centralizes provider configuration, redaction, budgeting, tracing, and policy.
- Makes `max_model_calls=0` enforceable by omission.
- Works with current fake-client testing style.
- Allows each worker type to have different prompt/output schemas without widening the global `Task` schema too early.

Cons:

- Requires changing worker protocol from `run(task)` to `run(task, context)` or adding an adapter layer.
- Requires a small model gateway and per-step usage accounting.

Recommendation:

- Best immediate direction after kernel contract hardening.

### Option 4: Ephemeral Worker Instances Per Step

Description:

- The registry maps `worker_type` to a factory, not a reusable singleton.
- The kernel builds an instance spec per step and asks the factory for a fresh worker instance or stateless runner.

Pros:

- Avoids cross-step hidden memory.
- Makes per-step model/capability injection natural.
- Later supports subprocess/container execution because the same spec can become a process launch contract.

Cons:

- More plumbing than the current registry.
- Current tests assume simple registered worker objects.

Recommendation:

- Adopt gradually. Keep current `WorkerRegistry` compatible by allowing registered objects and registered factories during migration.

### Option 5: Long-Lived LLM Agent Per Worker Type

Description:

- Each worker type is a long-lived agent with memory, tools, and model client.

Pros:

- Convenient for conversational or iterative agent behavior.
- Could cache worker-specific context.

Cons:

- Conflicts with artifact-only inter-step memory.
- Risks state leakage across runs and request IDs.
- Harder to replay or audit.

Recommendation:

- Defer. If needed later, make memory explicit runtime artifacts, not hidden worker state.

## Recommended Runtime Shape

Use this authority chain:

```text
Plan + optional Envelope
  -> KernelRunContext
  -> StepAcceptanceGate
  -> ArtifactResolutionGate
  -> CapabilityProfile
  -> WorkerInstanceSpec
  -> WorkerContext
  -> Worker.run(task, context)
  -> ResultValidationGate
  -> RuntimeArtifactStore
```

The kernel should create the worker context from structural facts only:

- `plan_id`, `request_id`, `run_id`
- `step_id`, `worker_type`, `phase`, `mode`, `task_id`
- `instruction`
- resolved input runtime artifacts
- expected output artifact IDs
- explicit permissions
- resolved read/write/command/model capabilities
- selected envelope summary for audit and prompt context
- budget counters and violation log

The worker should never receive:

- full mutable artifact store
- unrestricted file/command/network/model clients
- raw envelope artifacts as runtime inputs
- permission-changing authority
- plan-repair authority

## Worker Instance Model

Separate worker type from worker instance.

`worker_type` is a planner-declared catalog value:

- `direct_worker`
- `repo_worker`
- `research_worker`
- `infra_worker`
- `code_worker`
- `verify_worker`

`WorkerInstanceSpec` is a kernel-created per-step runtime object:

```text
worker_type: str
run_id: str
step_id: str
phase: str | null
mode: str | null
task_id: str | null
model_budget: int
tool_budget: int
capability_profile_id: str
allowed_artifact_inputs: list[str]
expected_artifact_outputs: list[str]
policy: dict
trace_id: str
```

`WorkerContext` is what the instance receives:

```text
run:
  run_id
  request_id
  plan_id
task:
  step_id
  worker_type
  phase
  mode
  task_id
artifacts:
  resolved immutable input artifacts
capabilities:
  read_files? file reader
  write_files? scoped writer
  run_commands? command runner
  model? metered JSON model client
  network? absent by default
limits:
  max_tool_calls
  max_model_calls
  timeout policy
audit:
  envelope_summary
  operation log
```

The important difference is that `Task` remains the planner-to-worker task payload, while `WorkerContext` is the kernel-owned authority envelope.

## LLM Model Capability

Add a worker-specific protocol instead of letting workers depend on provider SDKs:

```text
class WorkerModelClient(Protocol):
    def complete_json(
        self,
        *,
        stage: str,
        prompt: str,
        schema: dict[str, Any],
        task_id: str,
        artifact_id: str | None = None,
    ) -> str: ...
```

The kernel-provided implementation should enforce:

- no client when `max_model_calls=0`
- per-step model-call ceiling
- aggregate plan model-call ceiling
- stage naming and trace metadata
- prompt redaction policy
- output size limits
- structured JSON output only for runtime artifacts
- provider/model configuration from worker runtime env, not worker code

Possible environment names for later implementation:

- `WORKER_LLM_ENABLED`
- `WORKER_LLM_API_KEY`
- `WORKER_LLM_MODEL`
- `WORKER_LLM_BASE_URL`
- `WORKER_LLM_TIMEOUT_SECONDS`
- `WORKER_LLM_TEMPERATURE`
- `WORKER_LLM_RESPONSE_FORMAT`
- `WORKER_LLM_PROVIDER_SORT`
- `WORKER_LLM_MAX_TOKENS`

This mirrors existing decompressor/planner wiring while keeping worker model policy separate.

## Model Output Pattern

Workers should ask the model for artifact payloads, not for kernel actions.

Good model task:

```text
Given this task instruction, safe envelope summary, and immutable input artifacts,
produce JSON for artifact kind verification_result.
```

Bad model task:

```text
Decide what files you need, what permissions are needed, and whether to modify the plan.
```

Every LLM-backed worker call should have:

- a stage name tied to worker type and phase
- a JSON schema tied to the expected artifact kind
- compact task-local prompt context
- explicit allowed and forbidden actions
- no hidden artifact authority outside the context
- deterministic validation before the result is accepted

Example stages:

- `direct_worker.finalize.direct_guidance`
- `repo_worker.discover.repo_inventory`
- `research_worker.analyze.evidence`
- `code_worker.design.patch_design`
- `code_worker.mutate.change_summary`
- `verify_worker.verify.verification_result`

## Type Separation Model

The runtime should separate five type families.

### 1. Upstream Contract Types

Existing shared schemas:

- `Envelope`
- `Plan`
- `PlanStep`

Authority:

- `Envelope` is provenance and semantic context.
- `Plan` and `PlanStep` are executable declarations after planner validation.

Do not put kernel-only operational details here unless the field must cross graph boundaries.

### 2. Kernel Internal Types

Recommended new worker-kernel-local models:

- `KernelRunContext`
- `KernelPolicy`
- `CapabilityProfile`
- `WorkerInstanceSpec`
- `WorkerContext`
- `ResolvedScope`
- `OperationLogEntry`
- `ModelCallRecord`
- `ConfinementViolation`

Authority:

- These are created by the kernel and should not be model-emitted.
- They can live under `app/worker_kernel/` rather than the top-level `app/schemas.py` until they need to become public graph state.

### 3. Runtime Artifact Types

Recommended normalized base shape:

```text
RuntimeArtifact:
  id
  kind
  producer_step_id
  producer_worker_type
  phase
  mode
  task_id
  content
  paths
  claims
  metadata
  created_at
```

Special high-risk artifact schemas:

- `ScopeArtifact`
- `RollbackPlanArtifact`
- `PatchDesignArtifact`
- `ChangeSummaryArtifact`
- `VerificationResultArtifact`
- `FinalReportArtifact`

Authority:

- Runtime artifacts are the only valid inter-step data dependency.
- Artifact `id` satisfies planner dependencies.
- Artifact `kind` chooses validation schema.
- Scope artifacts may become confinement input only after strict schema and provenance validation.

### 4. Worker Prompt/Input Types

These are model-facing prompt DTOs, not execution authority:

- `WorkerPromptEnvelopeSummary`
- `WorkerPromptTaskView`
- `WorkerPromptArtifactView`
- `WorkerPromptPolicyView`

Authority:

- Safe for prompt construction.
- Redacted and minimized.
- Cannot satisfy `input_artifacts` and cannot grant capability.

### 5. Worker Result Types

Existing shared schema:

- `Result`

Recommended worker-kernel-local validation helpers:

- `WorkerResultEnvelope`
- `ArtifactValidationReport`
- `UsageReport`
- `WorkerTrace`

Authority:

- `Result` is accepted only after kernel validation.
- Worker-reported usage is input to budget accounting, not trusted proof by itself.
- Missing expected artifacts, unexpected dependency artifacts, duplicate artifact IDs, and invalid artifact schemas should block or fail closed.

## Keep These Concepts Separate

Do not overload one field to mean several things.

`worker_type`:

- Who executes the step.
- Stable catalog value.
- Not a permission grant.
- Not an artifact schema.

`phase`:

- Where the step sits in the lifecycle.
- Used for policy profile and trace grouping.
- Not a worker implementation class.

`mode`:

- Small runtime posture enum.
- Maps to capability defaults.
- Not semantic work content.

`permissions`:

- Explicit capability declarations.
- Must be enforced by kernel gates.
- Not model prompt advice.

`artifact id`:

- Dependency handle declared by the planner.
- Must match expected outputs and input references.
- Not enough to validate payload shape.

`artifact kind`:

- Payload schema selector.
- Helps validate high-risk artifacts.
- Does not replace artifact ID dependency validation.

`model profile`:

- Provider/model/runtime configuration.
- Kernel policy concern.
- Should not be chosen by individual workers from task text.

## How Each Worker Can Be LLM-Powered

### direct_worker

Model role:

- Generate final/direct guidance from task instruction and safe envelope summary.

Constraints:

- No file, command, network, or runtime artifacts unless plan declares them.
- Usually one model call.

Output:

- `direct_guidance` or `final_report` artifact.

### repo_worker

Model role:

- Summarize deterministic repository observations or rank candidate files discovered by controlled file readers.

Constraints:

- Reads only through kernel file capability.
- Does not output write-scope artifacts by default.
- Candidate paths are discovery evidence, not writable authority.

Output:

- `repo_inventory`, `candidate_paths`, `target_observation`, `dependency_evidence`.

### research_worker

Model role:

- Synthesize evidence, compare options, and produce research notes from allowed inputs.

Constraints:

- No arbitrary network until a future explicit network policy exists.
- Cannot grant write authority.

Output:

- `evidence`, `root_cause_hypotheses`, `research_notes`, `dependency_evidence`.

### infra_worker

Model role:

- Interpret allowed config/log artifacts and produce infra diagnosis or command recommendations.

Constraints:

- Commands should remain recommendations until command allowlists and sandboxing exist.

Output:

- `infra_findings`, `command_plan`, `risk_report`.

### code_worker

Model role:

- In `DESIGN`, create patch design, rollback plan, verification plan, and mutation scope from evidence and candidates.
- In `MUTATE`, draft or apply scoped changes only through kernel writer.

Constraints:

- May write only in `MUTATE` with `mode=bounded_mutation`, `write_files=true`, and resolved scope.
- The model may propose patch content, but the kernel applies or records it through scoped APIs.

Output:

- `mutation_scope`, `rollback_plan`, `patch_design`, `change_summary`, `rollback_patch`.

### verify_worker

Model role:

- Summarize deterministic check results and assess residual risk.

Constraints:

- Commands run only through kernel command runner.
- No writes.

Output:

- `verification_result`, `test_result`, `final_report` when finalization is delegated.

## Immediate Implementation Direction

The next implementation plan should not jump directly to full LLM workers. Use a staged path.

### Phase A: Contract Hardening

- Fail when input artifacts are missing.
- Validate expected output artifacts.
- Reject duplicate artifact IDs and unexpected dependency artifacts.
- Normalize artifacts with provenance.
- Add structured blocked/error reason codes.

### Phase B: Kernel Internal Types

- Add `KernelRunContext`, `RuntimeArtifact`, `WorkerInstanceSpec`, `WorkerContext`, and `CapabilityProfile` under `app/worker_kernel/`.
- Keep top-level `app/schemas.py` stable unless graph state needs to carry a new public type.

### Phase C: Capability Injection

- Change worker protocol to accept context, or add an adapter that supports both old `run(task)` and new `run(task, context)` while migrating.
- Expose file, command, and model capabilities only when allowed.
- Keep real writes and commands disabled or dry-run until scope and command gates are strict.

### Phase D: Worker Model Gateway

- Add `WorkerModelClient` protocol and `WorkerLLMGateway` wrapper.
- Reuse the existing OpenAI-compatible JSON client style.
- Add worker-specific env config.
- Test with fake clients.
- Count model calls through the same budget gate.

### Phase E: LLM-Backed Low-Risk Workers First

- Start with `direct_worker` and `research_worker` because they do not need file mutation.
- Then add `repo_worker` with controlled read capability.
- Only after scope resolution is strong, add `code_worker` design and mutation support.
- Add `verify_worker` command execution after command policy is explicit.

## Key Risks

Risk: Kernel becomes a hidden planner.

Mitigation: Kernel only builds context, validates structure, injects capabilities, and records traces. It never asks an LLM to repair plan semantics or broaden permissions.

Risk: LLM worker output becomes authority too early.

Mitigation: Model output becomes a candidate artifact first. Only schemas like `ScopeArtifact` can feed confinement, and only after provenance/schema validation.

Risk: Worker instances leak state.

Mitigation: Prefer per-step ephemeral instances. Keep cross-step memory as runtime artifacts only.

Risk: Model usage bypasses budget.

Mitigation: Workers receive no raw provider client. They receive a metered context model capability or nothing.

Risk: Type churn spreads through `app/schemas.py`.

Mitigation: Put kernel-internal types under `app/worker_kernel/` first. Promote only stable public graph contracts.

Risk: Current tests and stubs break under strict output validation.

Mitigation: Add a transitional adapter or update stubs to emit every expected artifact. Strictness should be the target behavior for mutating flows.

## Recommendation

Implement LLM-powered worker runtime as kernel-governed, per-step worker instances with injected capabilities. Keep the kernel deterministic, keep planner output as the executable contract, and let LLMs produce typed artifact payloads inside narrow worker contexts.

The most important type split is:

```text
Envelope/Plan/PlanStep      upstream contract
KernelRunContext           deterministic run authority
WorkerInstanceSpec         per-step instance creation contract
WorkerContext              injected capability surface
RuntimeArtifact            immutable inter-step data
ScopeArtifact              validated confinement input
WorkerPrompt*              redacted model-facing DTOs
Result                     worker output envelope, accepted only after validation
```

This preserves the current architecture while giving the worker layer a clean path to useful LLM reasoning.
