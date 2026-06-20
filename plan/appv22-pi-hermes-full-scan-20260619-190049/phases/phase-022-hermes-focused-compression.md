# Phase 022: Hermes Focused Manual Compression

## Scope

Port the Hermes focus-topic path used by manual `/compress <focus>`-style compression.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_generate_summary(..., focus_topic=...)`
  - `compress(..., focus_topic=...)`
- `hermes-agent/agent/conversation_compression.py`
  - `focus_topic` pass-through
- `hermes-agent/tests/agent/test_compress_focus.py`

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added `focus_topic` to `generate_summary()` and `compress()`.
  - Appends Hermes-style `FOCUS TOPIC` guidance to the summarizer prompt.
  - Redacts the focus topic before embedding it into the prompt.

- `appV2.2/appv22/compaction/timing.py`
  - Carries `focus` through `CompactionManager.compress_manual()` into `ContextCompressor.compress()`.

- `appV2.2/tests/test_compaction_timing.py`
  - Added a regression proving manual compression focus reaches the summarizer prompt.

## Verification

- Focused red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_manual_compression_focus_reaches_summary_prompt -q`
  - Result before port: `1 failed`

- Focused green test after implementation:
  - Same command
  - Result: `1 passed`

- Compaction suites:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - Result: `25 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `109 passed`

- Syntax:
  - `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Result: passed

## Remaining Gaps

- Summary-model fallback and failure bookkeeping remain simplified.
