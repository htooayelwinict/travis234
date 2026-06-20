# Phase 015: Hermes Protected Tail Anchoring

## Scope

Port the Hermes compaction behavior that keeps the active user request and the last user-visible assistant reply out of the summarized middle region.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_find_last_user_message_idx`
  - `_find_last_assistant_message_idx`
  - `_ensure_last_user_message_in_tail`
  - `_ensure_last_assistant_message_in_tail`
  - `_find_tail_cut_by_tokens`

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added dataclass-shaped latest-user and latest-assistant lookup helpers.
  - Added protected-tail anchoring helpers for latest user messages and content-bearing assistant replies.
  - Applied the helpers after token-budget tail selection and boundary alignment.
  - Removed stale duplicate post-`_find_tail_start()` boundary alignment in `compress()` because `_find_tail_start()` now returns an already aligned and anchored boundary.

- `appV2.2/tests/test_compaction.py`
  - Added regression coverage for preserving the latest user request when a large tool sequence follows it.
  - Added regression coverage for preserving the last visible assistant reply when only the newest user message fits the tail budget.

## Verification

- Focused red tests before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_compress_keeps_latest_user_before_large_tool_tail tests/test_compaction.py::test_compress_keeps_last_visible_assistant_before_latest_user -q`
  - Result before port: `2 failed`

- Focused green tests after implementation:
  - Same command
  - Result: `2 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - Result: `16 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `100 passed`

## Remaining Gaps

- Historical image stripping is not ported.
- Hermes protected-tail soft-ceiling and bounded message floor details are still simplified.
- Summary role selection/merge behavior, explicit summary end markers, secret redaction instructions, temporal anchoring, and summary failure bookkeeping remain simplified.
