# Decompressor + Planner Runtime Composition (230509 Runtime Trace)

## Problem Statement
The `230509` run (`live-decompressor-planner-runs-20260531-230509.json`) shows a high-complexity flow (read `v8.md` + web comparison + evidence-backed fix + mutation + verification). I need to decide whether decompressor and planner should remain separate runtimes or be merged into one.

## Observed Envelope and Plan Shape
- Envelope says high complexity: `algorithm_markdown_review_with_web_comparison_and_code_remediation`, `complexity_hint=high`, `confidence=0.9`.
- Decompressor captured ambiguity around algorithm detail, language, success criteria, and integration location; it also inferred risks including `mutation_requested`, `needs_verification`, and `external_source_quality_variance`.
- Decompressor runtime metadata shows 1 LLM call with ~12.7s elapsed in that run.
- Planner metadata shows 1 LLM planning call, no repair attempts.
- Plan is phase-aware and sequential:
  - `DISCOVER` -> `ANALYZE` -> `RESEARCH` -> `DESIGN` -> `MUTATE` -> `VERIFY` -> `FINALIZE`.
- Plan uses separate worker types per phase (`repo_worker`, `research_worker`, `web_research_worker`, `code_worker`, `verify_worker`), bounded permissions, and explicit scope/rollback/verification requirements.

## Constraint Analysis
- `app/graph.py` wires fixed pipeline: decompressor node -> planner node -> worker-kernel node.
- `DecompressorRuntime` and `PlannerRuntime` are currently narrow and stable, each owning one stage of orchestration plus dedicated metrics/contracts.
- `PlannerPromptChain` is already enforcing phase contracts, permissions, and budget/alignment logic.
- `WorkerKernelRuntime` expects a complete, validated `Plan` and executes sequential tasks through a compiler + dispatcher.
- Latency budget concern is real: this architecture forces at least one planner-level LLM call after decompressor for almost every request.

## Option A: Keep Separate Runtimes (Current Architecture)
- Feasibility: very high (already implemented and used).
- Effort: none/low.
- Maintainability: high (decompressor contracts stay isolated from planning policy).
- Risk: moderate (extra 1+ LLM hop on simple asks).
- Latency: stable and predictable for complex workflows, but can be wasteful for simple direct support.

## Option B: Merge Decompressor + Planner into One Runtime
- Feasibility: medium (requires schema redesign).
- Effort: high (contract migration, prompt redesign, fallback handling, tests).
- Maintainability: low-to-moderate (one large prompt + validation surface is harder to evolve safely).
- Risk: high (regression risk around envelope/plan shape, validation bypasses, and tool permission discipline).
- Latency: potentially lower on paper, but likely increases prompt size and model drift risk.

## Option C: Keep Them Separate, Add Fast-Path Router in Front
- Feasibility: high.
- Effort: medium.
- Maintainability: high.
- Risk: low.
- Latency: lower for direct-support cases, unchanged for complex mutation/evidence flows.

## Option D: Consolidate by Running Planner Only and Remove Decompressor
- Feasibility: medium.
- Effort: high.
- Maintainability: low.
- Risk: high (removes explicit compression/scoping stage; harder to keep prompt-policy consistent across all plan types).
- Latency: similar or worse for mixed workloads because planner must infer everything every time.

## Recommendation
Do not merge decompressor and planner right now. For your `230509`-type runs, the split is behaviorally correct and already expressing the right control pattern: structured extraction first, then governed execution planning, then worker orchestration.

Recommended path:
1. Keep decompressor + planner boundaries as-is.
2. Add an explicit complexity/ambiguity router to select direct path vs full path.
3. Keep worker-kernel unchanged and move policy improvements into planner prompts/contracts.
4. Keep planner-plausibility checks focused on context completeness before any mutation.

## Risks and Mitigations
- Latency for simple prompts: mitigate with Option C fast path.
- Overly heavy plan output for short tasks: mitigate with direct support archetype threshold + hard confidence gate.
- Drift between runtime contracts: mitigate with small compatibility tests for `Envelope -> Plan -> Task` flow and a shared trace schema.

## Next Steps
- Add fast-path policy that routes to a direct-support plan for non-mutating questions.
- Keep decomposition output schema strict and add explicit `required_minimal_context` signals for planner escalation.
- Run one small QA batch comparing direct-support prompts vs high-complexity prompts for latency delta.

## Saved Path
- ` /abs/path/allthebest/brainstorm/decompressor-planner-composition-20260602-110000.md`
