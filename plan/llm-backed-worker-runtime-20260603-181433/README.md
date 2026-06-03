# LLM-Backed Worker Runtime

## Goal

Implement production-shaped worker groups behind the existing planner-facing `worker_type` contract.

## Status

- Planned: 2026-06-03
- Implemented: 2026-06-03
- Completed: worker LLM env wiring, permission-gated tools, agentic worker groups, scoped task context, worker-owned templates/system prompts, kernel budget normalization, write-scope artifact extraction, and regression tests.

## Notes

- Keep decompressor and planner schemas stable.
- Keep `worker_type` as the group identity.
- Use stubs only when worker LLM mode is disabled or tests inject workers.
- Latest live QA: `plan/live-worker-qwen-qwen3-7-max-20260603-202500.json`.
- Live QA completed end to end with `qwen/qwen3.7-max`: baseline tests failed, worker runtime mutated `live_worker_mock_repo/src/checkout.py`, verification passed, and final result status was `completed`.
- Follow-up: add wall-clock ceilings and parallel fanout where step dependencies allow; current worker path is correct but slow because model calls are serialized.
- Follow-up: improve replan hygiene after partial mutation so recovery plans do not overcorrect already-applied logic changes.
