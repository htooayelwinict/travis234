# Phase 2 - Agentic Workers

- [x] Add permission-gated tools.
- [x] Add LLM-backed group runner.
- [x] Add worker templates for all planner-visible worker types.
- [x] Move worker-owned system prompts/templates into `app/worker_kernel/workers/`.
- [x] Split `repo_worker` into locator, reader, and summarizer instances.
- [x] Normalize common live LLM output variants without treating malformed artifacts as completed truth.
- [x] Gate tools by remaining runtime budget and hide tools after tool budget is spent.
- [x] Support safe readonly verification commands including `python -m pytest` and `PYTHONPATH=. pytest ...`.
- [x] Resolve nested mutation-scope artifact paths for scoped writes.
