# Planner Contract Discipline QA

Date: 2026-05-30 22:42:55

## Question

How should the planner contract be tightened without turning deterministic validation back into semantic planning?

## Research Summary

- Repo-local review showed the planner already had phase ordering, artifact lineage, write-scope, rollback, and verification checks.
- The main gap was contract shape: `mode` was an open string even though the kernel needs stable runtime modes.
- User feedback clarified that semantic meaning belongs in `phase`, while `mode` should be one of a small runtime enum.
- Open Bridge second-opinion review agreed to keep contract-shape checks and avoid broad deterministic evidence/dependency synthesis.

## Decisions

- Add a small allowed mode enum: `observe_only`, `plan_only`, `bounded_mutation`, `verify_only`, `summarize_only`.
- Validate phase-to-mode mapping instead of silently normalizing bad modes.
- Require any mutation plan to have prior `DESIGN` outputs for `mutation_scope` and `rollback_plan`.
- Require `MUTATE` to consume write scope, use `write_paths_from_artifacts`, and output `change_summary`.
- Require post-mutation `VERIFY` to consume `change_summary`, write scope, and evidence/root-cause context.
- Remove over-heavy deterministic semantic checks for dependency/evidence separation, stop/replan metadata, and low-confidence mutation gating.
- Keep actual runtime enforcement concerns in worker-kernel follow-up work.

## Files Updated

- `app/schemas.py`
- `app/planner/contracts.py`
- `app/planner/prompt_chain.py`
- `app/planner/validator.py`
- `tests/test_planner.py`

## Verification

```bash
uv run pytest tests/test_planner.py -q
# 36 passed

uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_kernel.py tests/test_graph.py -q
# 75 passed

uv run pytest -q
# 75 passed
```

## Live QA

Saved full live output:

- `plan/live-planner-contract-qa-20260530-224255.json`

Live batch summary:

- `run_count`: 4
- `success_count`: 4
- `failure_count`: 0
- `qa_issue_count`: 0

Manual QA checks performed on each planner output:

- all `mode` values came from the allowed runtime enum
- `DESIGN` produced `mutation_scope` and `rollback_plan`
- `MUTATE` used `bounded_mutation`, `write_files=true`, and `write_paths_from_artifacts=["mutation_scope"]`
- `MUTATE` consumed the design-produced scope and rollback plan
- `MUTATE` output `change_summary`
- `VERIFY` consumed `change_summary`, write scope, and evidence/root-cause context

## Follow-Up

Worker-kernel runtime enforcement remains separate and should handle path resolution, blocking writes outside scope, forbidden command handling, runtime budget checks, worker output validation, and stop-on-verification-failure behavior.
