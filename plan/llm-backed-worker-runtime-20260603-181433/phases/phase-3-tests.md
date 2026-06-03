# Phase 3 - Tests

- [x] Add focused tests for env config, permissions, fanout, artifact handoff, and graph wiring.
- [x] Run full regression verification.
- [x] Add regressions for OpenAI/OpenRouter tool-call shapes, root-level final results, bare artifact ids, prompt tool budget gating, repo-noise filtering, nested write-scope extraction, and safe env-prefixed verification commands.
- [x] Run live decompressor -> planner -> worker probe with `qwen/qwen3.7-max` against `live_worker_mock_repo`.

## Verification

- `uv run pytest tests/test_worker_agentic.py tests/test_worker_kernel.py` -> 39 passed.
- `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_kernel.py tests/test_worker_agentic.py tests/test_graph.py` -> 123 passed.
- `uv run python scripts/live_worker_runtime_probe.py --worker-model qwen/qwen3.7-max` -> `plan/live-worker-qwen-qwen3-7-max-20260603-202500.json`, final status `completed`, after pytest `2 passed`.
