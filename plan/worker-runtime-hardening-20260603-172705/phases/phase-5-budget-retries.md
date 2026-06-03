# Phase 5 - Budget And Retries

Status: Completed.

Goal: make retry budget real and kernel-owned.

Changes:

- Add attempt accounting to `BudgetGate`.
- Increment retries/attempts when replacing a failed instance.
- Enforce max retries before spawning a replacement.
- Include attempt usage in final result metadata.

Acceptance:

- Retry budget is consumed.
- Exhausted retries return structured failure.
- Tool/model budgets include all attempts.

Implementation notes:

- `BudgetGate` now enforces non-negative `max_retries`.
- Retry budget is consumed for replacement attempts after retryable instance failures.
- Final result metadata includes retry and attempt counts.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py
```
