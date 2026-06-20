# Phase 025 - Hermes Real-Usage Preflight Deferral

## Scope

Port the Hermes timing behavior that tracks real provider prompt usage after compression and defers repeated preflight compression when a rough schema-heavy estimate stays high but the provider proved the prompt fit.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `update_from_response()`
  - `should_defer_preflight_to_real_usage()`
  - `last_real_prompt_tokens`
  - `last_compression_rough_tokens`
  - `last_rough_tokens_when_real_prompt_fit`
  - `awaiting_real_usage_after_compression`
- `hermes-agent/agent/turn_context.py`
  - preflight `should_defer_preflight_to_real_usage()` gate
- `hermes-agent/agent/conversation_compression.py`
  - post-compression rough token bookkeeping
- `hermes-agent/tests/agent/test_context_compressor.py`
  - `TestUpdateFromResponse`
  - `TestPreflightDeferral`

## Appv22 Changes

- Added compressor-owned token usage fields:
  - `last_prompt_tokens`
  - `last_completion_tokens`
  - `last_total_tokens`
  - `last_real_prompt_tokens`
  - `last_compression_rough_tokens`
  - `last_rough_tokens_when_real_prompt_fit`
  - `awaiting_real_usage_after_compression`
- Added `ContextCompressor.update_from_response()`.
- Added `ContextCompressor.should_defer_preflight_to_real_usage()`.
- Made `CompactionManager.last_prompt_tokens` and `CompactionManager.awaiting_real_usage_after_compression` delegate to the compressor.
- Made preflight call the compressor deferral gate before compressing.
- Made post-response update real usage through the compressor.
- Made successful compression record a post-compression rough estimate and set the `-1` prompt-token sentinel.

## Regressions

- Added `test_compressor_update_from_response_tracks_real_usage_for_deferral`.
  - Red: failed with `AttributeError: 'ContextCompressor' object has no attribute 'update_from_response'`.
  - Green: passed after porting compressor usage fields and methods.
- Added `test_preflight_defers_after_real_usage_proved_rough_estimate_noisy`.
  - Red: failed because `maybe_compress_preflight()` compressed instead of returning the unchanged messages.
  - Green: passed after wiring `CompactionManager` to compressor-owned usage state.

## Verification

- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_compressor_update_from_response_tracks_real_usage_for_deferral tests/test_compaction_timing.py::test_preflight_defers_after_real_usage_proved_rough_estimate_noisy -q`
  - `2 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - `31 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - `115 passed`
- `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Passed

## Remaining Gap

Summary-failure cooldown still needs to move closer to Hermes' compressor-owned `_summary_failure_cooldown_until` behavior, while preserving appv22's default deterministic fallback handoff.
