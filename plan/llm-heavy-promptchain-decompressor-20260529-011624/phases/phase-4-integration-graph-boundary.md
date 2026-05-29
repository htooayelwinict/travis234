# Phase 4 — Integration and Graph-Boundary Protection

## Goal

Verify LLM-mode envelopes remain compatible with planner selection and that the default graph stays deterministic and model-free.

## Status

Completed. Added planner integration tests for semantic LLM envelopes and observe-first precedence, plus a graph test guard that default invocation has no prompt-chain metadata.

2026-05-29 update: Planner selection no longer reads decompressor strategy leaks. It infers fallback/observe-first planning from descriptive ambiguity signals (`ambiguous_request`, `ambiguous_scope`, `scope_clarification`, low confidence, and target-scope constraints) while graph invocation remains model-free by default.

2026-05-29 coalesced update: Planner and graph integration tests now use one coalesced `decompress_request` fake response. Focused verification passes with `31 passed`.

## Files

- Update `tests/test_planner.py`
- Update `tests/test_graph.py`
- Avoid changing `app/graph.py` unless optional injection support is strictly necessary

## Tasks

1. Test that semantic fields from an LLM envelope route to the same planners as deterministic decompression.
2. Test that observe-first semantics take precedence over mutation routing when ambiguity is present.
3. Test that default graph invocation does not require or call a model client.
4. Reconfirm graph node keys remain `decompressor_node`, `planner_node`, and `worker_kernel_node`.

## Risks

- Overfitting tests to LangGraph internals.
- Introducing graph injection changes that are not needed for the feature.

## Rollback

Revert integration-test changes and any optional graph changes.

## Verification

```bash
uv run pytest tests/test_graph.py tests/test_planner.py -q
uv run pytest -q
```
