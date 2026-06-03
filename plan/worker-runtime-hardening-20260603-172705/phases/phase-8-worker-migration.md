# Phase 8 - Worker Migration

Status: Deferred.

Goal: replace placeholder workers with real group-compatible implementations.

Order:

1. `direct_worker`
2. `repo_worker`
3. `research_worker`
4. `web_research_worker`
5. `verify_worker`
6. `code_worker`
7. `infra_worker`

Reasoning:

- Start with non-mutating workers.
- Add repo and research evidence before mutation.
- Add verify before real code mutation.
- Add code worker last among risky local mutation paths.

Acceptance:

- Mock output no longer claims real work unless real work happened.
- Workers return typed artifacts and issues.
- Mutation workers enforce path scope from artifacts.

Verification:

```bash
uv run pytest tests/test_worker_kernel.py tests/test_graph.py
```
