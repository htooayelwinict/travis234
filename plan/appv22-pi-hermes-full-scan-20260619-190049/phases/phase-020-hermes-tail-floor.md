# Phase 020: Hermes Protected Tail Floor

## Scope

Port the Hermes protected-tail boundary behavior that uses a bounded recent-message floor, a 1.5x soft token ceiling, and a raw-budget fallback when the soft ceiling would otherwise keep the whole transcript.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_MAX_TAIL_MESSAGE_FLOOR`
  - `_find_tail_cut_by_tokens`
- `hermes-agent/tests/agent/test_context_compressor.py`
  - `test_tiny_budget_preserves_bounded_recent_turns`
- `hermes-agent/tests/run_agent/test_infinite_compaction_loop.py`
  - Raw-budget fallback coverage for meaningful middle windows

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added `_MAX_TAIL_MESSAGE_FLOOR` and `_MESSAGE_TOKEN_OVERHEAD`.
  - Replaced the simpler `protect_last_n` loop in `_find_tail_start()` with the Hermes bounded floor, soft ceiling, raw-budget fallback, boundary alignment, and existing tail anchoring.
  - Added `_tail_message_tokens()` for the tail-budget rough estimate.

- `appV2.2/tests/test_compaction.py`
  - Added a tiny-budget regression proving `protect_last_n=20` caps to an 8-message recent tail instead of keeping almost the whole transcript live.
  - Updated the summary-merge regression to allow the summary to be prepended to the first message of a bounded Hermes tail rather than assuming a one-message tail.

## Verification

- Focused red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_find_tail_start_uses_bounded_floor_with_tiny_budget -q`
  - Result before port: `1 failed`

- Focused green test after implementation:
  - Same command
  - Result: `1 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py -q`
  - Result: `16 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `106 passed`

- Syntax:
  - `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Result: passed

## Remaining Gaps

- Secret redaction instructions, temporal anchoring, focused compression, summary-model fallback, and failure bookkeeping remain simplified.
