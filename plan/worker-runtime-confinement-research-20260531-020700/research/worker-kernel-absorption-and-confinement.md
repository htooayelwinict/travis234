# Worker Kernel Absorption And Confinement Research

## Question

How should the next worker runtime absorb and confine validated planner output plus selected envelope context/artifact schemas while preserving the decompressor and planner boundaries?

## Executive Summary

The worker kernel should become a capability-constrained execution host. It should accept a validated `Plan` as the executable contract and selected `Envelope` fields as provenance/safety context. It should not infer semantic intent, create steps, repair plan structure, or treat decompressor artifacts as runtime artifacts.

The central design principle is:

> Planner decides what may happen; worker kernel enforces what actually happens.

The next phase should introduce a kernel-side run context, typed runtime artifacts, a fail-closed artifact store, a scope resolver, and runtime gates for filesystem, command, network/tool/model usage, and result artifact validation. Actual OS/container sandboxing can be phased in after structural enforcement is in place.

## Boundary Model

### Decompressor Output Role

`Envelope` should be treated as descriptive provenance.

Safe kernel uses:

- Include `request_id`, `input_type`, `domains`, `intents`, `risks`, `constraints`, `context_needed`, `confidence`, and `complexity_hint` in run metadata.
- Use `constraints` and `risks` to choose stricter default runtime policy when the plan already permits an action.
- Use `raw_input` and `normalized_input` for audit/tracing with redaction consideration.
- Use `artifacts` only as semantic hints for logs or worker prompt context, not as resolved runtime inputs or paths.

Unsafe kernel uses:

- Do not copy `Envelope.artifacts` into `Task.input_artifacts`.
- Do not convert an artifact named `policy module` or `pipeline` into a writable path.
- Do not use envelope `risks` to invent missing steps.
- Do not weaken plan permissions because envelope confidence is high.
- Do not broaden write scope because the user mentioned a component.

Recommended absorbed envelope subset:

```text
KernelRunContext.envelope_summary:
  request_id
  input_type
  user_goal
  intents
  domains
  risks
  constraints
  context_needed
  complexity_hint
  confidence
  ambiguity
  assumptions
  artifact_hints
```

`artifact_hints` should be explicitly named as hints to prevent confusion with runtime artifacts.

### Planner Output Role

`Plan` is the executable declaration.

Kernel uses:

- `plan_id`, `request_id`, `execution_pattern`, and `global_invariants` to create run metadata.
- `steps` to drive execution order.
- `budget` to enforce run ceilings.
- `step.permissions` to create task capabilities.
- `step.input_artifacts` to resolve prior outputs only.
- `step.output_artifacts` to validate worker results.
- `step.phase`, `step.mode`, and `step.task_id` to choose policy profile and trace grouping.

Kernel must not:

- Reorder steps.
- Add missing `VERIFY` or `FINALIZE` steps.
- Change `write_files` based on worker type.
- Repair missing write scope by looking at envelope artifacts.
- Treat instruction text as a source of permission.

## Proposed Runtime Objects

This is a research schema, not an implementation instruction.

### KernelRunContext

Purpose: bounded run-level context that preserves upstream contracts without exposing full raw state to workers by default.

Fields to consider:

- `run_id`
- `plan_id`
- `request_id`
- `plan_summary`
- `envelope_summary`
- `global_invariants`
- `budget_policy`
- `runtime_policy`
- `artifact_store`
- `violation_log`

Recommended behavior:

- Full `Envelope` can remain available to the kernel for metadata and audit.
- Workers receive only step-local `Task` plus resolved input artifacts and safe metadata.
- Worker prompts should not automatically include raw user input unless the planner instruction needs direct response and the permission profile allows it.

### RuntimeArtifact

The current artifact is an untyped `dict`. The next boundary needs a normalized shape while still allowing payload-specific content.

Minimum useful fields:

```text
id: string
kind: string
producer_step_id: string
producer_worker_type: string
phase: string | null
mode: string | null
task_id: string | null
content: object | string | list | null
paths: list[string]
claims: list[string]
metadata: dict
created_at: string
```

Recommended `kind` values:

- `direct_guidance`
- `repo_inventory`
- `candidate_paths`
- `evidence`
- `dependency_evidence`
- `mutation_scope`
- `rollback_plan`
- `verification_plan`
- `patch_design`
- `change_summary`
- `rollback_patch`
- `verification_result`
- `final_report`
- `generic_note`

Important note:

- `kind` should not replace artifact ID validation.
- Artifact ID remains the planner-declared dependency handle.
- `kind` helps the kernel validate structure for high-risk artifacts.

### ScopeArtifact

Write-scope artifacts deserve stricter structure because they become confinement inputs.

Applicable IDs/signals:

- `mutation_scope`
- `allowed_write_paths`
- `writable_targets`
- `patch_scope`

Recommended normalized structure:

```text
id: mutation_scope
kind: mutation_scope
paths:
  - relative path strings under workspace root
allow_globs:
  - optional specific globs
deny_globs:
  - optional deny entries
max_files: integer | null
max_bytes_changed: integer | null
source_artifacts:
  - target_files
  - root_cause_evidence
reason: string
```

Validation rules:

- Paths must be relative to workspace root or canonicalized under workspace root.
- No absolute paths unless explicitly allowed by host policy and canonicalized under allowed roots.
- No `..` traversal after normalization.
- No workspace root-only scope like `.`, `./`, `/`, `*`, or broad empty path.
- Globs must be narrower than repository-wide unless explicitly allowed by policy.
- Every path must derive from prior discovery/design artifacts, but the kernel should only check provenance and structure, not semantic correctness.

### CommandPolicy

Current `run_commands` is boolean. Runtime confinement needs a narrower command contract before allowing shell execution.

Possible fields:

```text
run_commands: bool
allowed_commands: list[list[string]]
allowed_command_prefixes: list[list[string]]
cwd_policy: workspace_root | scoped_paths | temp_dir
timeout_seconds: int
env_allowlist: list[string]
network: disabled | allowlisted | default
```

Near-term recommendation:

- Treat `run_commands=false` as absolute deny.
- Treat `run_commands=true` as permission to use kernel-provided command runner only, not raw shell.
- Default to argument arrays, not shell strings.
- Restrict verification commands to known project commands or planner-declared command artifacts in a later schema pass.

### ToolPolicy

Current plan has `max_tool_calls` and permissions but no tool allowlist.

Possible fields:

```text
max_tool_calls
tool_allowlist
read_policy
write_policy
network_policy
model_policy
```

Near-term recommendation:

- The kernel should expose capabilities, not global tools.
- A worker should only receive interfaces matching step permissions.
- For example, a `direct_worker` with all permissions false gets no file reader, no writer, no command runner, and no network tool.

## Confinement Gates

### Gate 1: Plan Acceptance Gate

Purpose: fail closed before any worker runs.

Inputs:

- `Plan`
- optional `Envelope`
- worker registry
- kernel policy

Checks:

- Worker type registered.
- No duplicate step IDs.
- Every step has explicit boolean permissions.
- Every `input_artifact` exists by the time the step runs.
- Every `output_artifact` name is valid and non-empty.
- Budget values are non-negative and cover requested execution.
- `plan.request_id` matches `envelope.request_id` when envelope is provided.
- Direct-support profiles cannot have file/command permissions.
- Non-code workers cannot receive write capability.
- Mode-to-capability profile is not violated.

Relationship to planner validator:

- Some checks duplicate planner validation intentionally because the worker kernel is a trust boundary.
- Duplicate checks should be structural and runtime-facing only.
- The kernel should return `blocked` or `invalid_plan` instead of repairing.

### Gate 2: Artifact Resolution Gate

Purpose: ensure task inputs are exactly prior runtime outputs.

Inputs:

- `step.input_artifacts`
- artifact store

Checks:

- Every requested input artifact must be present.
- Missing input artifacts should block the step; do not silently omit them.
- Input artifacts should be immutable snapshots.
- Producer provenance should be attached to task metadata.
- Artifact IDs should not be overwritten silently.

Recommended change from current behavior:

- `TaskCompiler` currently ignores missing inputs if the planner said they exist but the store lacks them.
- The next runtime should fail closed here because runtime can diverge from planner assumptions when a worker fails to produce expected output.

### Gate 3: Scope Resolution Gate

Purpose: convert plan-declared write scope into concrete runtime confinement rules.

Inputs:

- `permissions.write_paths`
- `permissions.write_paths_from_artifacts`
- artifact store
- workspace root

Checks:

- Literal `write_paths` must be specific and canonicalized under allowed roots.
- `write_paths_from_artifacts` must resolve to `ScopeArtifact` structures.
- Scope artifacts must contain non-empty narrow paths.
- Scope artifacts must have provenance from a prior `DESIGN` step or equivalent validated metadata.
- Resolved write scope should be attached to the task as `resolved_write_paths` metadata.
- If `write_files=true` and no concrete path scope is resolvable, block before dispatch.

Non-goal:

- The kernel does not decide whether the chosen paths are semantically correct for the bug. It only checks that the resolved paths are narrow, prior-produced, and allowed.

### Gate 4: Capability Injection Gate

Purpose: provide workers with only the capabilities the plan allows.

Policy examples:

- `read_files=false`: no file read API.
- `write_files=false`: no file write API.
- `run_commands=false`: no command runner.
- `max_tool_calls=0`: no tool interfaces that count as tools.
- `max_model_calls=0`: no model client for the worker.
- `mode=observe_only`: deny writes even if a malformed plan tries to include them.
- `mode=plan_only`: deny writes; allow reading if permission true.
- `mode=bounded_mutation`: allow writes only through resolved write scope.
- `mode=verify_only`: allow commands only if `run_commands=true`; deny writes.
- `mode=summarize_only`: deny writes and commands by default.

This suggests workers should not directly import unrestricted file/command helpers. They should receive a `WorkerContext` or capability object from the kernel.

### Gate 5: Filesystem Gate

Purpose: enforce read/write permissions physically.

Near-term in-process enforcement:

- Provide file APIs that canonicalize paths under workspace root.
- Check read/write permission before every file operation.
- Restrict writes to resolved write paths.
- Record all file operations.
- Block direct writes outside the API by worker convention and tests.

Later hard enforcement:

- Run tools in sandboxed subprocess/container with mount restrictions.
- Use OS-level isolation where available.

Platform note:

- The current environment is macOS/darwin. Linux-only tools like Landlock/seccomp are not portable to the local dev environment.
- A portable first phase should use in-process capability wrappers and/or container profiles rather than depending on Linux-only kernel APIs immediately.

### Gate 6: Command Gate

Purpose: prevent uncontrolled shell/process execution.

Near-term rules:

- If `run_commands=false`, no command runner is exposed.
- If `run_commands=true`, command runner accepts argument arrays, not shell strings.
- Command execution has timeout, cwd, environment allowlist, and output size limits.
- Command runner records exact command, cwd, exit code, duration, stdout/stderr truncation metadata.
- Verification commands should be read-only from the file mutation perspective unless scoped otherwise.

Future rules:

- Add allowlists based on worker type and phase.
- Add explicit `allowed_commands` schema or command-plan artifacts from DESIGN.
- Run commands in a sandbox/container with network and filesystem boundaries.

### Gate 7: Network Gate

Purpose: prevent uncontrolled egress.

Current plan permissions do not include network access.

Near-term recommendation:

- Default network access to disabled for workers unless a future explicit permission exists.
- Research workers that need external docs should use approved MCP/documentation tools through a controlled interface, not arbitrary network.
- If external network becomes necessary, add a distinct `network_access` or `allowed_hosts` contract before enabling it.

### Gate 8: Result Validation Gate

Purpose: verify worker outputs match the plan's declared artifact contract.

Checks:

- Worker result status is known: `completed`, `failed`, `blocked`, `budget_exceeded`.
- Usage is non-negative and within task budget before aggregate budget update.
- Every declared `expected_outputs` artifact should be produced unless the result is blocked/failed.
- Produced artifact IDs must be a subset of expected outputs unless the worker has explicit permission for auxiliary artifacts.
- Artifact IDs must be unique within the run.
- Artifact payloads must conform to schema for high-risk IDs/kinds.
- Mutation steps must produce `change_summary` and rollback artifact when expected.
- Verification steps should output structured verification status, not only prose.

Important design fork:

- Strict mode: reject unexpected artifacts and missing expected artifacts.
- Permissive mode: allow auxiliary artifacts but mark them non-dependency unless declared.

Recommendation:

- Start strict for dependency artifacts. If auxiliary artifacts are useful, require explicit prefix or metadata like `auxiliary=true` and do not allow future steps to consume them unless the planner declared them.

### Gate 9: Stop And Failure Gate

Purpose: define terminal behavior without adding planner loops.

Current behavior stops on `failed`, `blocked`, or `budget_exceeded`.

Recommended next behavior:

- Continue stopping on failure/block/budget.
- Add structured `blocked_reason` codes.
- Add `replan_recommended=true` metadata when the failure is due to missing scope, failed verification, stale artifact, or invalid runtime contract.
- Do not automatically re-enter planner from kernel in this phase.
- Surface enough diagnostics for the outer graph or a future caller to decide whether to rerun decompressor/planner.

Potential statuses:

- `completed`
- `blocked`
- `failed`
- `budget_exceeded`
- `invalid_plan`
- `confinement_violation`
- `artifact_contract_violation`
- `verification_failed`

## Envelope Artifacts Vs Runtime Artifacts

This is the most important absorption rule.

`Envelope.artifacts`:

- Are concrete nouns from the user's input.
- Are descriptive hints.
- May include files/components/APIs/symbols/URLs.
- Are not guaranteed to exist.
- Are not guaranteed to be paths.
- Are not produced by runtime steps.
- Must not be task input artifacts.
- Must not become write scope directly.

Runtime artifacts:

- Are produced by worker results.
- Have IDs declared in `step.output_artifacts`.
- Can be consumed by later `step.input_artifacts`.
- Need provenance.
- Can become executable authority only if their kind/schema allows it.

Recommended naming:

- Call decompressor artifacts `artifact_hints` inside kernel context.
- Call worker outputs `runtime_artifacts` or just `artifacts` within the artifact store.

This avoids reintroducing the exact issue the planner validator was built to prevent.

## Worker-Type Runtime Profiles

### direct_worker

Expected use:

- Direct response/final support.

Default profile:

- No file read.
- No file write.
- No commands.
- No network.
- At most one model call if future direct generation uses a model.

Confinement:

- Can read only task instruction and safe envelope summary if passed.
- Cannot consume runtime artifacts unless the plan explicitly declares them.
- Output must match expected direct/final artifact.

### repo_worker

Expected use:

- Repository discovery and observation.

Default profile:

- Read files allowed only when `read_files=true`.
- No writes.
- Commands generally false unless explicitly allowed for read-only discovery.

Confinement:

- Read scope defaults to workspace root or narrower path hints.
- Output candidate paths and inventories, not write scope.
- Candidate paths should be typed as discovery artifacts.

### research_worker

Expected use:

- Research and synthesis across available context.

Default profile:

- No writes.
- Read files only if allowed.
- External access only via approved tools and future explicit network/tool policy.

Confinement:

- Outputs notes, evidence, final summaries.
- Should not emit write-scope artifacts unless planner/validator later permits for DESIGN-like research, but current write-scope rule favors DESIGN phase.

### infra_worker

Expected use:

- Infrastructure diagnosis and guidance.

Default profile:

- No writes in current catalog.
- Commands only if explicitly permitted and sandboxed.

Confinement:

- Infrastructure commands are high-risk; require command allowlist before active execution.

### code_worker

Expected use:

- Code analysis/design/mutation.

Default profile:

- Read files if allowed.
- Write files only in `MUTATE` + `bounded_mutation` + resolved write scope.
- Commands only if explicitly allowed.

Confinement:

- DESIGN mode can produce scope/design/rollback/test artifacts but cannot write.
- MUTATE mode can write only through resolved scope.
- Must output structured change summary and rollback artifact when declared.

### verify_worker

Expected use:

- Verification after mutation or standalone checks.

Default profile:

- No writes.
- Commands allowed only when `run_commands=true`.
- Reads allowed only when `read_files=true`.

Confinement:

- Verification result should be structured with pass/fail, commands/checks run, inputs checked, failures, and residual risk.

## Recommended Phased Path

### Phase 1: Kernel Contract Hardening Without Real Tool Execution

Goal:

- Make the existing stub kernel fail closed and contract-aware.

Scope:

- Accept optional envelope context or `KernelRunContext`.
- Runtime-validate registered workers, permission booleans, mode/capability compatibility, input artifact presence, expected output presence, and artifact ID uniqueness.
- Normalize artifacts into a typed/provenance-rich internal store.
- Preserve current graph shape.
- Keep existing workers as stubs.

Why first:

- It closes correctness gaps without adding dangerous execution capabilities.
- It creates the data model required for confinement.

Risks:

- Stricter result validation may break current stubs if they only emit first expected artifact.
- Tests will need plans whose workers produce all expected outputs or the kernel may need a transitional strictness flag.

### Phase 2: Scope Resolution And Capability Injection

Goal:

- Convert planner permissions and scope artifacts into explicit task capabilities.

Scope:

- Add `WorkerContext` or capability interfaces.
- Resolve `write_paths_from_artifacts` into canonical paths.
- Deny missing/invalid scope before dispatch.
- Attach resolved read/write scopes to task metadata.
- Keep file/command APIs stubbed or dry-run initially.

Why second:

- Scope resolution is prerequisite for safe writes.
- Capability injection prevents workers from depending on global unrestricted helpers.

### Phase 3: In-Process File And Command Gates

Goal:

- Provide real but controlled file and command access.

Scope:

- File reader/writer wrappers with canonical path enforcement.
- Command runner with no shell strings, timeouts, cwd/env restrictions, output limits, usage accounting.
- Operation logs in artifacts/result metadata.
- Strict denial of capabilities not declared by `Task.permissions`.

Why third:

- Provides useful runtime behavior while still being portable on macOS.

Risk:

- In-process gates are not a hard sandbox if worker code can bypass wrappers. This is acceptable only if built-in workers are trusted and tools are mediated.

### Phase 4: Real Worker Implementations Under Capabilities

Goal:

- Replace deterministic stubs with workers that use only injected capabilities.

Scope:

- Repo worker performs bounded file discovery.
- Code worker can design and apply scoped patches.
- Verify worker can run focused checks.
- Research/infra workers remain constrained.

Why fourth:

- Real behavior should wait until gates exist.

### Phase 5: Hard Isolation For High-Risk Execution

Goal:

- Run commands and mutations inside process/container sandboxes.

Scope:

- Container or subprocess profiles.
- Read/write mounts derived from resolved scope.
- Network disabled by default.
- Optional Linux-specific hardening in deployment environments.

Why fifth:

- Hard sandboxing has operational complexity and platform constraints.
- It should build on stable contract and capability semantics.

## Key Design Decisions To Preserve

- Keep worker kernel terminal for now; do not add automatic replan loops.
- Keep planner as semantic authority.
- Keep decompressor descriptive-only.
- Treat envelope artifacts as hints, not runtime artifacts.
- Treat runtime artifacts as immutable step outputs with provenance.
- Fail closed at runtime when artifacts, scope, permissions, or outputs are missing.
- Prefer structural enforcement over semantic inference.

## Open Questions For Implementation Planning

- Should `WorkerKernelRuntime.run()` become `run(plan, envelope=None)` or accept a dedicated `KernelRunContext`?
- Should artifact schemas be formal Pydantic models now, or should the kernel normalize dicts internally first?
- Should strict expected-output validation be enabled immediately, or introduced behind a compatibility mode for existing stubs?
- What is the minimum path schema for `mutation_scope` that balances safety and planner prompt complexity?
- Should command allowlists live in `PlanStep.permissions`, a DESIGN-produced artifact, or kernel policy?
- How should verification failures trigger rollback artifacts without automatic replan loops?
- Should direct_worker consume raw input or only planner instruction for final response?
- How much envelope metadata should workers see versus kernel-only audit logs?

## Recommendation

Start with Phase 1 and Phase 2 as the immediate next implementation plan. They are the smallest safe steps from the current code:

- They do not require real filesystem mutation.
- They do not require OS sandboxing.
- They preserve existing upstream prompt/validator contracts.
- They make future real workers safer because workers will be written against capabilities instead of global access.

Do not jump directly to real code mutation or command execution. The current kernel lacks the artifact, scope, and output validation needed to safely host those actions.
