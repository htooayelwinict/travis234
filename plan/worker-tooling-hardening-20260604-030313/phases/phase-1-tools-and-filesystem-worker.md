# Phase 1: Tools And Filesystem Worker

Status: completed

Tasks:

- [x] Add `runtime_capabilities` as a command-permissioned structured tool.
- [x] Add `write_many_files` for scoped batch file creation.
- [x] Add optional `move_file` and `delete_file` with write-scope validation.
- [x] Add `filesystem_worker` templates.
- [x] Wire planner contracts and validator.
- [x] Add regression tests.
- [x] Resolve flexible mutation-scope proposals into strict kernel write scope,
  including operation lists, file-management moves, common root config dotfiles,
  and forbidden globs.

Verification:

- `uv run pytest tests/test_worker_agentic.py tests/test_planner.py -q`
  passed with 102 tests.
- `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_graph.py tests/test_worker_agentic.py tests/test_worker_kernel.py -q`
  passed with 166 tests.
- Non-LLM replay of blocked live artifacts from
  `plan/live-worker-qwen-qwen3-7-max-20260604-032858.json` and
  `plan/live-worker-qwen-qwen3-7-max-20260604-033443.json` now resolves their
  mutation proposals into strict write scopes.
