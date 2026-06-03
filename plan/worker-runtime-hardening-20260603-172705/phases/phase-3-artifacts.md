# Phase 3 - Artifact Resolution

Status: Completed.

Goal: missing input artifacts never silently disappear.

Changes:

- Update `TaskCompiler` to return either a compiled task or a compile failure object.
- Missing required input artifacts produce a plan-level issue.
- Kernel converts missing artifacts into `needs_replan`, `blocked`, or `failed` according to policy.
- Add `missing_artifacts` to result metadata.

Recommendation:

Missing artifact should usually be `needs_replan` when planner runtime and envelope are available; otherwise `blocked`.

Acceptance:

- A step requiring absent artifacts does not run.
- Result metadata includes missing artifact IDs.
- Replan request includes issue code and failed step ID.

Implementation notes:

- `TaskCompiler` now raises `MissingInputArtifacts`.
- Kernel converts missing runtime artifacts to `needs_replan` when internal replan is available, otherwise `blocked`.
- Metadata includes `missing_artifacts`.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py
```
