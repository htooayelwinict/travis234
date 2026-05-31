# Brainstorm: Worker Runtime Absorption And Confinement Options

## Problem Statement

The next phase is worker runtime. The current decompressor and planner now emit strong descriptive and planning contracts. The worker kernel needs to absorb the validated plan and selected envelope context, execute bounded worker steps, and confine runtime behavior. The challenge is to improve execution safety without turning the worker kernel into a second planner or allowing model/planner artifacts to bypass runtime controls.

## Constraints

- Research only; no implementation in this pass.
- Preserve current upstream boundaries.
- Decompressor stays descriptive-only.
- Planner stays semantic authority for steps, phases, modes, artifacts, and permissions.
- Worker kernel should enforce contracts at runtime, not create or repair semantic plans.
- Current graph is acyclic: `decompressor_node -> planner_node -> worker_kernel_node -> END`.
- No kernel-level automatic replan/resume loop yet.
- Envelope artifacts are semantic hints only.
- Runtime step inputs must come from prior worker output artifacts.
- Write scope must remain narrowed by planner/DESIGN outputs before mutation.
- Current environment is macOS, so Linux-only sandboxing is not a good first dependency.

## Option 1: Minimal Contract-Aware Stub Kernel

### Description

Keep workers mostly deterministic/stubbed, but make the kernel fail closed around plan contracts:

- Accept optional envelope summary for audit.
- Validate runtime input artifact presence.
- Validate expected output artifacts.
- Track artifact provenance.
- Reject duplicate/unknown artifact IDs.
- Runtime-check permission booleans and mode/capability compatibility.
- Keep budget enforcement.

### Pros

- Smallest safe next step.
- Preserves current test shape and graph topology.
- Creates a clear runtime trust boundary.
- Does not introduce unsafe real file or command execution.
- Makes later worker implementation easier.

### Cons

- Still does not perform useful repo mutation or verification.
- Does not physically sandbox file/command access.
- Existing stubs may need to emit all expected outputs.

### Fit

Very high as the first implementation phase.

## Option 2: Capability-Based In-Process Runtime

### Description

Introduce `WorkerContext` capabilities and require workers to use injected interfaces:

- `read_file()` only when `read_files=true`.
- `write_file()` only when `write_files=true` and path is inside resolved scope.
- `run_command()` only when `run_commands=true`.
- Model/tool clients only when call budget allows.
- Artifact store and scope resolver mediate all inputs/outputs.

### Pros

- Strong architectural fit: the kernel becomes an enforcement host.
- Portable on macOS and CI.
- Allows useful real workers without immediate Docker/OS sandbox dependency.
- Makes violations testable with fake workers and fake filesystem adapters.

### Cons

- Not a hard security sandbox if untrusted worker code can bypass provided capabilities.
- Requires refactoring worker protocol beyond `worker.run(task)`.
- Needs careful design so workers do not import unrestricted helpers.

### Fit

High as the second phase after contract hardening.

## Option 3: Dry-Run Confinement Observer

### Description

Add operation tracing before active blocking:

- Workers run through capability wrappers.
- Wrappers record would-be reads, writes, commands, and network attempts.
- Violations are logged as warnings or QA failures, not blocked.
- Useful for measuring planner/runtime mismatch before strict enforcement.

### Pros

- Lower risk rollout.
- Good for observing live planner outputs and worker behavior.
- Helps tune artifact schemas and path-scope resolution.

### Cons

- Dangerous if used with real mutation because violations still occur.
- Can create false confidence if dry-run is not clearly labeled.
- Adds an extra mode that tests and operators must understand.

### Fit

Moderate. Useful only before real mutation or in test/simulation. Not recommended as default for mutation.

## Option 4: OS-Level Or Container Sandbox First

### Description

Run every step in an isolated subprocess/container with mounts and network derived from resolved task permissions.

### Pros

- Strongest enforcement for untrusted code and commands.
- Prevents bypass of in-process wrappers.
- Natural fit for arbitrary CLI/test execution.

### Cons

- High operational complexity.
- Slower per step.
- Requires environment-specific setup.
- On current macOS dev environment, Linux-specific primitives are not directly portable.
- Still needs artifact schema, scope resolution, and capability contract first.

### Fit

Low as the immediate next phase, high as a later high-risk execution layer.

## Option 5: Planner-Driven Dynamic Tool Execution Without Kernel Gates

### Description

Let worker implementations directly inspect plan instructions and perform file/command actions using general tooling.

### Pros

- Fastest route to visible functionality.
- Minimal runtime architecture work.

### Cons

- Violates the safety direction of the planner contract work.
- Planner artifacts could become de facto authority without runtime validation.
- Write scope may be declared but not enforced.
- Commands could execute outside planned bounds.
- Hard to test and audit.

### Fit

Poor. This should be avoided.

## Comparison Matrix

| Option | Safety | Effort | Portability | Immediate Value | Architecture Fit | Recommendation |
| --- | --- | --- | --- | --- | --- | --- |
| Minimal contract-aware stub | Medium | Low | High | Medium | Very high | Do first |
| Capability-based runtime | High for trusted workers | Medium | High | High | Very high | Do second |
| Dry-run observer | Medium | Medium | High | Medium | Medium | Optional test mode |
| OS/container sandbox | Very high | High | Medium/Low locally | High later | High later | Defer |
| Planner-driven direct tools | Low | Low | High | High short-term | Poor | Avoid |

## Recommended Path

Use a phased blend:

1. Start with **Option 1** to make the current kernel fail closed around artifact, permission, budget, and output contracts.
2. Add **Option 2** to introduce capability-based workers and resolved scopes.
3. Use **Option 3** only for QA/simulation where helpful.
4. Add **Option 4** after real command/mutation workers exist and the artifact/scope model stabilizes.
5. Avoid **Option 5**.

## Runtime Contract Principles

- A plan is executable only after kernel acceptance.
- A step is executable only after input artifacts resolve.
- A write is executable only after write scope resolves to concrete paths.
- A worker receives only the capabilities declared by the step.
- A result is accepted only if artifacts match expected outputs and schemas.
- A failure is surfaced as structured diagnostics, not hidden by fallback behavior.
- A replan recommendation can be emitted, but replan is not run inside the kernel.

## Artifact Schema Direction

Use typed runtime artifacts for high-risk boundaries, not for every low-risk note immediately.

Priority schemas:

- `mutation_scope`
- `rollback_plan`
- `verification_plan`
- `change_summary`
- `rollback_patch`
- `verification_result`
- `final_report`

Minimum provenance for every artifact:

- artifact ID
- producer step ID
- worker type
- phase
- task ID
- creation order
- content payload
- metadata

## Confined Write Flow

Recommended mutation flow at runtime:

1. `DISCOVER` outputs candidate locations.
2. `ANALYZE` or `RESEARCH` outputs evidence.
3. `DESIGN` outputs `mutation_scope`, `rollback_plan`, and `verification_plan`.
4. Kernel validates `mutation_scope` structure.
5. Kernel resolves `write_paths_from_artifacts=["mutation_scope"]` into canonical allowed paths.
6. Kernel injects write capability limited to those paths.
7. `MUTATE` writes only through the injected capability.
8. Kernel records actual touched files and validates they are inside scope.
9. `MUTATE` outputs `change_summary` and rollback artifact.
10. `VERIFY` consumes change summary, scope, evidence, and rollback plan.
11. Kernel records verification output and stops/finalizes according to result status.

## Risks And Mitigations

Risk: Kernel starts interpreting semantics.

Mitigation: Restrict kernel checks to shape, provenance, path resolution, permissions, and status. Do not evaluate whether root cause is correct.

Risk: Envelope artifacts leak into runtime authority.

Mitigation: Rename them to `artifact_hints` in kernel context and prevent them from satisfying `input_artifacts`.

Risk: In-process capabilities are bypassable.

Mitigation: Treat built-in workers as trusted initially; add subprocess/container sandbox for untrusted commands later.

Risk: Strict output validation breaks current worker stubs.

Mitigation: Update stubs in implementation phase or introduce an explicit compatibility window. Do not silently accept missing dependency outputs for mutating flows.

Risk: Path scopes are too broad or malformed.

Mitigation: Canonicalize paths, reject root/broad glob scopes, require DESIGN provenance, and block unresolved scope.

Risk: Verification failures need rollback.

Mitigation: Surface structured `verification_failed` and rollback artifact availability first. Do not auto-run rollback until rollback execution policy is designed.

## Next Planning Questions

- What exact Pydantic models should represent runtime artifacts and scope artifacts?
- Should `Task` gain resolved scopes directly, or should this live in `Task.metadata` first?
- Should the worker protocol become `run(task, context)`?
- What is the strictness mode for existing tests and stubs?
- What is the first real worker to implement under capabilities: repo discovery, verify command runner, or code mutation?
- How should kernel result statuses map to graph-level behavior?

## Decision

Recommended next implementation plan should target contract-aware kernel hardening plus capability/scoping foundations before real mutation or command execution.
