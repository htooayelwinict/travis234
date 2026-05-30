# Constraint-Phase Planner Runtime

## Goal

Design a minimal, constraint-driven planner architecture that reasons in generic phases (`DISCOVER/ANALYZE/RESEARCH/DESIGN/MUTATE/VERIFY/FINALIZE`) without domain hardcoding, while preserving the current runtime topology.

## Scope

- Research only in this pass.
- No runtime code changes.
- Produce implementation-ready plan artifacts.

## Key Decision

Keep one planner runtime and one worker-kernel runtime, but add a phase-aware planning contract and validator policy layer.

- Do not add graph nodes.
- Do not add new worker types yet.
- Do not build a new scheduler.

## Artifacts

- `plan.md`: source-of-truth implementation plan.
- `research/requirements.md`: user requirements and desired schema/behavior.
- `research/existing-code.md`: current architecture constraints and opportunities.
- `research/references.md`: code and external synthesis references.
- `phases/`: incremental implementation phases.

## Status

Planner contract discipline pass complete. Current contract validation and prompt repairs are aligned with the LLM-heavy prompt-chain approach.

Latest QA artifact:

- `research/planner-contract-discipline-qa.md`
