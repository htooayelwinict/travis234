# Phase 4 - Issue Taxonomy And Failure Handling

Status: Completed.

Goal: separate instance failures, plan failures, and kernel failures.

Changes:

- Add issue classification helper.
- Wrap dispatcher/worker exceptions.
- Store `WorkerIssue` entries in kernel run metadata.
- Allow workers to emit `WorkerIssue` or `ReplanSignal` in metadata.

Acceptance:

- Instance exceptions become retryable instance failures.
- Plan-level worker signals become `needs_replan`.
- Kernel misconfiguration becomes terminal structured result.

Implementation notes:

- Added `WorkerIssue` taxonomy.
- Worker exceptions become retryable instance failures.
- Unknown worker groups become `kernel_error`.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py
```
