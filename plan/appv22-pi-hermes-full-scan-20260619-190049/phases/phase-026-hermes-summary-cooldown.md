# Phase 026 - Hermes Summary Failure Cooldown

## Scope

Port the Hermes compressor-owned summary failure cooldown behavior and force-clearing path for manual compression.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_summary_failure_cooldown_until`
  - `_SUMMARY_FAILURE_COOLDOWN_SECONDS`
  - `compress(..., force=True)`
- `hermes-agent/tests/agent/test_context_compressor.py`
  - `test_summary_failure_enters_cooldown_and_skips_retry`
  - `test_force_true_bypasses_failure_cooldown`

## Appv22 Changes

- Added compressor-owned `_summary_failure_cooldown_until` and a monotonic `clock` dependency.
- Made summary generation skip the summarizer while cooldown is active and fall back to the deterministic summary path.
- Made failed summarizer calls set a 60-second compressor cooldown.
- Made successful summary generation clear the compressor cooldown and summary error state.
- Added `force` to `ContextCompressor.compress()` so manual/overflow compression clears an active compressor cooldown before retrying.
- Made `CompactionManager._run_compress()` pass its force flag through to the compressor.
- Updated the previous summary success-clearing regression so it advances past the cooldown before expecting a successful retry.

## Regressions

- Added `test_summary_failure_sets_compressor_cooldown_and_skips_retry`.
  - Red: failed with `TypeError: ContextCompressor.__init__() got an unexpected keyword argument 'clock'`.
  - Green: passed after adding compressor-owned clock/cooldown and skipped-retry handling.
- Added `test_manual_compression_force_clears_compressor_cooldown`.
  - Red: failed because forced manual compression skipped the summarizer while the compressor cooldown was active.
  - Green: passed after adding `force` to `ContextCompressor.compress()` and passing it from `CompactionManager`.

## Verification

- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_manual_compression_force_clears_compressor_cooldown -q`
  - `1 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_summary_failure_sets_compressor_cooldown_and_skips_retry -q`
  - `1 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_summary_failure_flags_clear_on_subsequent_success -q`
  - `1 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - `33 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - `117 passed`
- `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Passed

## Remaining Gap

Session rotation/lineage persistence still needs to survive restarts. Current appv22 lineage coverage is only the in-memory `SessionLineage` helper.
