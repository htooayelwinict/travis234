# Phase 6 - Worker Groups

Status: Completed for the kernel abstraction; real multi-role worker implementations remain future work.

Goal: make `worker_type` represent a worker group with kernel-controlled instances.

Changes:

- Add worker group registry entry for each worker type.
- Add group runner abstraction.
- Start with sequential group instances.
- Later allow controlled parallelism for independent instance roles.

Example:

```text
web_research_worker group
  instance 1: source_discovery
  instance 2: source_extraction
  instance 3: citation_formatter
```

Acceptance:

- Existing one-object workers can be wrapped as single-instance groups.
- New web research group test proves multiple internal attempts produce one step result.
- Planner still only emits `worker_type="web_research_worker"`.

Implementation notes:

- Added single-instance worker-group wrapper for existing workers.
- Added sequential worker-group runner for multi-instance group tests.
- Registry now stores worker groups while preserving `register(worker)` compatibility.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py
```
