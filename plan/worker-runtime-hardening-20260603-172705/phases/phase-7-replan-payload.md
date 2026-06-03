# Phase 7 - Replan Payload Cleanup

Status: Completed.

Goal: make replan requests precise and safe.

Changes:

- Split artifact stores:
  - completed artifacts
  - partial artifacts
  - failed-step artifacts
- Ensure `completed_artifacts` only includes artifacts from completed steps.
- Attach failed-step/partial artifacts under separate replan metadata.
- Preserve `completed_step_ids`.

Acceptance:

- Replan request does not treat failed-step artifacts as completed truth.
- Planner still receives enough context to produce a full replacement plan.
- Existing internal replan tests pass with updated assertions.

Implementation notes:

- Completed artifacts, partial artifacts, and failed-step artifacts are tracked separately.
- Replan requests exclude failed-step artifacts from `completed_artifacts`.
- Replan metadata includes original issues and separated artifact stores.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py tests/test_planner.py
```
