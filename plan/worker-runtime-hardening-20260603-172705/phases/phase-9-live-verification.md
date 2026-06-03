# Phase 9 - Live Verification

Status: Deferred until real worker implementations are available.

Goal: prove the full runtime behaves correctly with LLM decompressor/planner and mocked or real worker groups.

Workflow:

```text
complex user prompt
  -> decompressor envelope
  -> planner plan
  -> worker kernel validates
  -> worker group runs
  -> forced instance failure retry
  -> forced plan-level failure replan
  -> planner replacement plan
  -> final result
```

Artifacts to save:

- full envelope
- initial plan
- kernel run state summary
- worker group attempt logs
- replan request
- replacement plan
- final result

Acceptance:

- Full output is saved under `plan/`.
- Replan remains internal runtime.
- The final payload clearly distinguishes completed artifacts, partial artifacts, failed artifacts, issues, and replacement plan.

Verification:

```bash
uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_kernel.py tests/test_graph.py
```
