# Phase 024 - Hermes Summary-Model Fallback

## Scope

Port the Hermes behavior where a configured auxiliary compression model can fail, then compression retries once on the main model before falling back to a deterministic handoff.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `summary_model`
  - `_fallback_to_main_for_compression()`
  - `_last_aux_model_failure_error`
  - `_last_aux_model_failure_model`
- `hermes-agent/tests/agent/test_context_compressor.py`
  - `TestSummaryFallbackToMainModel`
  - `TestAuxModelFallbackSurfacedToCallers`

## Appv22 Changes

- Added `model`, `summary_model_override`, and `summary_summarizer` to `ContextCompressor`.
- Kept the existing `summarizer` callback as the main-model summarizer.
- Added aux failure fields:
  - `_last_aux_model_failure_error`
  - `_last_aux_model_failure_model`
  - `_summary_model_fallen_back`
- Added one retry from `summary_summarizer` to `summarizer` only when `summary_model` differs from `model`.
- Cleared `summary_model` after aux failure so subsequent compression uses the main summarizer, matching Hermes' handoff.

## Regression

- Added `test_summary_model_failure_falls_back_to_main_summarizer_and_records_aux_failure`.
- Red run:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_summary_model_failure_falls_back_to_main_summarizer_and_records_aux_failure -q`
  - Result: failed with `TypeError: ContextCompressor.__init__() got an unexpected keyword argument 'model'`.
- Green run:
  - Same focused test.
  - Result: `1 passed`.

## Verification

- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_summary_model_failure_falls_back_to_main_summarizer_and_records_aux_failure -q`
  - `1 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - `29 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - `113 passed`
- `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Passed
- `git diff --check -- appV2.2/appv22/compaction/compressor.py appV2.2/tests/test_compaction.py plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md plan/appv22-pi-hermes-full-scan-20260619-190049/research/appv22-pi-hermes-audit.md`
  - Passed

## Remaining Gap

Compaction timing still lacks Hermes' richer real-usage deferral and rough-estimate noise handling.
