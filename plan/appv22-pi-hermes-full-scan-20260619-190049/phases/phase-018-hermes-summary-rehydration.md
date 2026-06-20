# Phase 018: Hermes Persisted Summary Rehydration

## Scope

Port the Hermes behavior that recognizes existing context-summary messages, strips handoff markers, and uses the stripped body as iterative summary state.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_strip_summary_prefix`
  - `_is_context_summary_content`
  - `_find_latest_context_summary`
  - Summary rehydration block before `_generate_summary`

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added `LEGACY_SUMMARY_PREFIX`.
  - Added prefix/end-marker stripping for appv22 summary text.
  - Added context-summary message detection across user and assistant messages.
  - Rehydrates `_previous_summary` from the newest existing summary before summarization.
  - Excludes the existing summary message from the `NEW CONVERSATION` window passed to the summarizer.

- `appV2.2/tests/test_compaction.py`
  - Added regression coverage for an existing summary message that should become `EXISTING SUMMARY` in the summarizer prompt.
  - Asserts the old summary prefix, end marker, and old body do not leak into `NEW CONVERSATION`.

## Verification

- Focused red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_compress_rehydrates_existing_summary_message -q`
  - Result before port: `1 failed`

- Focused green test after implementation:
  - Same command
  - Result: `1 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - Result: `19 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `103 passed`

## Remaining Gaps

- Protected head sizing with implicit system-message handling is not ported.
- Hermes protected-tail soft-ceiling and bounded message floor details are still simplified.
- Secret redaction instructions, temporal anchoring, focused compression, summary-model fallback, and failure bookkeeping remain simplified.
