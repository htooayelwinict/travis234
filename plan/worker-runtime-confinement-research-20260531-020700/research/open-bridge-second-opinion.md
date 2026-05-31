# Open Bridge Second-Opinion Summary

## Question

How should the next-phase worker runtime absorb and confine validated `Plan` plus selected `Envelope` context/artifacts without becoming a semantic planner?

## Context Sent

The second-opinion prompt summarized the current architecture:

- Decompressor emits descriptive `Envelope` and strips planner/kernel-shaped fields.
- Planner emits validated phase-aware `Plan` with strict phase/mode/artifact/permission/mutation/verification contracts.
- Current worker kernel is a stub with budget enforcement, task compilation, dispatcher, and artifact store.
- Current worker kernel does not receive envelope, validate runtime permissions, resolve write scope, confine tools/files/commands, or validate outputs against expected artifacts.

## Useful Advice Kept

Open Bridge agreed with the core boundary split:

- Planner determines what should be done and declares execution boundaries.
- Worker kernel enforces those boundaries at runtime.
- Worker kernel should not generate, reorder, or semantically repair steps.

It recommended these worker-kernel responsibilities:

- Plan context absorption.
- Dynamic scope resolution for `write_paths_from_artifacts`.
- Active filesystem, command/process, and network gates.
- Artifact schema and content integrity enforcement.
- Structured result validation.

It compared three confinement options:

- Language-level/in-process virtual interceptor.
- OS-level syscall jailer.
- Ephemeral container or micro-VM sandbox.

It recommended phased adoption:

1. Passive/structural contract adoption and tracing.
2. Active filesystem and process confinement.
3. Containerized sandboxing and network gating.

## Adjustments Based On Repo Reality

The advice was useful but needs local adaptation:

- The current dev environment is macOS, so Linux-specific Landlock/seccomp should not be the immediate foundation.
- The current workers are deterministic stubs, so before hard sandboxing we need artifact schema, expected-output validation, scope resolution, and capability injection.
- Network access is not currently represented in plan permissions, so it should default to denied until a future explicit contract exists.
- The repo's architecture explicitly avoids kernel-level automatic replan loops for now, so the kernel should emit structured diagnostics and `replan_recommended` metadata rather than re-entering planner.

## How It Changed The Recommendation

The final recommendation keeps the Open Bridge enforcement-host framing, but reorders the implementation path for this repository:

1. Make the stub kernel contract-aware and fail-closed.
2. Add typed runtime artifacts and provenance.
3. Add write-scope resolution from DESIGN-produced artifacts.
4. Add capability injection for trusted in-process workers.
5. Only then add real file/command execution.
6. Defer OS/container sandboxing until real high-risk execution exists and platform constraints are addressed.

## Saved Path

`plan/worker-runtime-confinement-research-20260531-020700/research/open-bridge-second-opinion.md`
