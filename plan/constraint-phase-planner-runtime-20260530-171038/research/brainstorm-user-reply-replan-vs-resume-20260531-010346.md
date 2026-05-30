# Brainstorm: User Reply Handling — Replan vs Resume

## Problem statement

Choose the safer fit for the current runtime design when a user replies after an assistant/worker interaction:

1. Treat the reply as a fresh `user_input` and run the normal pipeline again: `decompressor -> planner -> worker kernel`.
2. Preserve continuity inside the worker kernel with an explicit `await_user_input` status, resume token, and artifact carryover.

The active architecture keeps the graph simple: `decompressor_node -> planner_node -> worker_kernel_node -> END`, and explicitly defers kernel-level automatic replan loops.

## Options considered

### Option A — Fresh input through decompressor + planner

Every user reply becomes a new top-level input. The decompressor classifies it in context, the planner decides whether to continue, revise, answer, or start a new plan, then the worker kernel executes a bounded plan and ends.

**Pros**

- Best match for the current acyclic runtime contract.
- Keeps planner as the single authority for interpreting user intent and choosing next steps.
- Avoids hidden state machines inside the worker kernel.
- Easier to test, log, audit, and recover from malformed replies.
- Safer for ambiguous replies because decompressor/planner can re-evaluate constraints and risks.

**Cons**

- More model calls and latency for simple confirmations like “yes” or “continue”.
- Requires carrying conversation/artifact summary at the orchestration/client layer if continuity is needed.

### Option B — `await_user_input` + resume token + artifact carryover in worker kernel

The worker kernel can pause execution, emit a resumable status/token, then continue from the prior plan when the user responds.

**Pros**

- Better UX and efficiency for short clarifications.
- Can preserve exact in-flight artifacts without asking planner to reconstruct context.
- Useful later if the system needs durable human-in-the-loop workflow execution.

**Cons**

- Adds a stateful execution loop to the worker kernel, which conflicts with the active plan’s “no kernel-level automatic replan loop” constraint.
- Splits intent interpretation between planner and kernel.
- Raises complexity around token validity, artifact freshness, timeout/TTL, authorization, replay protection, and stale plans.
- Harder to reason about partial execution and rollback.

## Recommended path

Use **Option A now**: treat every user reply as a new `user_input` and rerun decompressor + planner.

This is the suitable choice for the current design because it preserves the simple topology, keeps the worker kernel bounded and terminal, and aligns with the active plan’s explicit constraint not to add kernel-level replan/resume loops.

For continuity, carry only a compact conversation/artifact summary outside the worker kernel and let the decompressor/planner decide whether the reply is a continuation, confirmation, correction, or new request.

Defer Option B as a future worker-kernel capability only if product requirements clearly demand human-in-the-loop resumable execution. If added later, it should be explicit, tokenized, TTL-bound, and treated as a separate kernel feature rather than an implicit default path.

## Risks and mitigations

- **Risk: Higher latency/cost for simple replies.**
  - Mitigation: Add a future planner fast path for trivial continuation/confirmation, but still route through decompressor/planner.

- **Risk: Context loss between turns.**
  - Mitigation: Store a small durable conversation summary and relevant artifact IDs outside the kernel; feed that as context to the next decompressor/planner run.

- **Risk: Replanning changes direction unexpectedly.**
  - Mitigation: Planner should compare new input against prior objective/artifacts and preserve continuity unless the new reply clearly changes intent.

- **Risk: Future need for true pause/resume.**
  - Mitigation: Reserve `await_user_input` + resume token + artifact carryover as a later, explicit feature with TTLs, validation, stale-plan checks, and fallback to full replan.

## Open Bridge second-opinion summary

Open Bridge agreed that Option A is the best current fit because Option B introduces execution-level state and conflicts with the active no-kernel-replan-loop constraint. It suggested mitigating Option A’s cost with a future fast path/caching approach while retaining decompressor/planner authority.

## Decision

Recommended: **Option A now; Option B later only as an explicit, carefully bounded human-in-the-loop resume feature.**
