# Phase 023: Hermes Summary Failure Bookkeeping

## Scope

Port the Hermes default summary-failure behavior and bookkeeping fields into appv22 compaction.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_last_summary_error`
  - `_last_summary_dropped_count`
  - `_last_summary_fallback_used`
  - `_last_compress_aborted`
  - `abort_on_summary_failure`
  - deterministic fallback branch in `compress()`
- `hermes-agent/tests/agent/test_context_compressor.py`
  - summary failure fallback and abort-on-summary-failure coverage

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added failure bookkeeping fields.
  - Resets failure fields at the start of each `compress()` call.
  - Catches summarizer failures inside the compressor.
  - Default behavior inserts a deterministic fallback summary and records dropped-message count and error text.
  - Added `abort_on_summary_failure=True` mode, preserving messages unchanged and setting `_last_compress_aborted`.

- `appV2.2/tests/test_compaction.py`
  - Added regression coverage for deterministic fallback summary insertion and bookkeeping.
  - Added coverage that fallback fields clear on a later successful compression.
  - Added coverage for abort-on-summary-failure preserving messages and not inserting fallback text.

- `appV2.2/tests/test_compaction_timing.py`
  - Updated manual-force cooldown coverage to match Hermes default fallback behavior.

## Verification

- Focused red fallback/bookkeeping test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_summary_failure_uses_deterministic_fallback_and_bookkeeping -q`
  - Result before port: `1 failed`

- Focused green fallback/bookkeeping test after implementation:
  - Same command
  - Result: `1 passed`

- Focused red abort-mode test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_summary_failure_abort_option_preserves_messages_and_sets_abort_flag -q`
  - Result before port: `1 failed`

- Focused green abort-mode test after implementation:
  - Same command
  - Result: `1 passed`

- Compaction suites:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - Result: `28 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `112 passed`

- Syntax:
  - `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Result: passed

## Remaining Gaps

- Summary-model fallback behavior remains simplified.
