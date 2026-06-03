# Phase 1 - Contracts And Compatibility

Status: Completed.

Goal: add strict worker-facing schemas without breaking existing planner output or tests.

Changes:

- Add `PermissionSet`, `ArtifactRef`, `ArtifactPayload`, `ResultStatus`, `WorkerIssue`, and `ReplanSignal`.
- Convert `PlanStep.permissions` and `Task.permissions` to typed permissions if compatibility allows.
- Keep dict input compatibility so existing planner JSON still validates.
- Add result status literal or enum while preserving current status strings.

Acceptance:

- Existing runtime tests still pass.
- New tests prove permission dicts normalize to `PermissionSet`.
- New tests reject malformed permission values in strict runtime path.

Implementation notes:

- Added `PermissionSet`, `ArtifactPayload`, `ResultStatus`, `WorkerIssue`, and `ReplanSignal`.
- Preserved dict-style compatibility for existing worker and validator code.
- Extended `ReplanRequest` with issues and separated partial/failed artifacts.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py tests/test_planner.py
```
