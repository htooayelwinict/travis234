# Phase 028 - Hermes Manual Compression Feedback

## Scope

Port Hermes-style user-facing manual compression feedback/status while preserving appv22's existing list-returning `compress_manual()` API.

## Reference

- `hermes-agent/agent/manual_compression_feedback.py`
  - `summarize_manual_compression()`
- `hermes-agent/gateway/slash_commands.py`
  - `/compress` headline, focus line, token line, note, abort warning, and auxiliary model recovery info
- `hermes-agent/tests/gateway/test_compress_command.py`
  - no-op messaging
  - denser-summary note
  - compression-abort warning
  - auxiliary compression model recovery info

## Appv22 Changes

- Added `ManualCompressionStatus`.
- Added `summarize_manual_compression()` with Hermes-style:
  - `No changes from compression: N messages`
  - `Compressed: before → after messages`
  - approximate request-size token line
  - denser-summary note when message count falls but token estimate rises
- Added `CompactionManager.compress_manual_with_status()`.
  - Returns compressed messages and status fields for TUI/CLI rendering.
  - Carries the manual focus topic.
  - Emits a visible warning when compression aborts and no messages are dropped.
  - Emits a visible warning when deterministic fallback summary handoff is used.
  - Emits an info note when an auxiliary compression model fails and main-model recovery succeeds.
- Kept `CompactionManager.compress_manual()` as a compatibility wrapper returning only `messages`.
- Exported `ManualCompressionStatus` and `summarize_manual_compression` from `appv22.compaction`.

## Regressions

- Added `test_manual_compression_status_reports_success_and_focus`.
  - Red: failed with `AttributeError: 'CompactionManager' object has no attribute 'compress_manual_with_status'`.
  - Green: passed after adding the status-returning manual compression path.
- Added `test_manual_compression_status_warns_when_compression_aborts`.
  - Red: failed with the same missing-method error.
  - Green: passed after surfacing the abort warning from compressor bookkeeping.

## Verification

- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_manual_compression_status_reports_success_and_focus tests/test_compaction_timing.py::test_manual_compression_status_warns_when_compression_aborts -q`
  - `2 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_manual_compression_focus_reaches_summary_prompt tests/test_compaction_timing.py::test_manual_compression_force_clears_compressor_cooldown tests/test_compaction_timing.py::test_manual_force_clears_cooldown -q`
  - `3 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - `36 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - `120 passed`
- `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Passed

## Remaining Gap

Phase 5 Hermes compaction/timing items are complete in the current plan. The next highest-priority unchecked area is Phase 2 Pi agent-loop update-drain/event ordering.
