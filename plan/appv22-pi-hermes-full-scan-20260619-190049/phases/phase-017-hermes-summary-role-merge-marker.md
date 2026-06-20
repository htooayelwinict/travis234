# Phase 017: Hermes Summary Role, Merge, and End Marker

## Scope

Port the Hermes compaction behavior that inserts or merges the summary without creating consecutive same-role messages and marks the summary boundary explicitly.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - Summary role selection near compression assembly.
  - Merge-into-tail fallback when both standalone roles collide.
  - `_SUMMARY_END_MARKER`.
  - `_append_text_to_content`.

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added `SUMMARY_END_MARKER`.
  - Added summary neighbor-role selection, assistant summary construction, and prepend-to-message helpers.
  - Replaced unconditional `UserMessage` summary insertion with Hermes-style role selection and merge-into-first-tail behavior.
  - Keeps existing post-assembly tool-pair sanitization and historical media stripping order.

- `appV2.2/tests/test_compaction.py`
  - Updated the head/summary/tail regression so a user-head/user-tail standalone summary becomes assistant-role and contains the end marker.
  - Added regression coverage for the role-collision case where the summary is merged into the first tail assistant message.

- `appV2.2/tests/test_app_integration.py`
  - Updated the post-response compaction assertion to accept the Hermes-selected summary role and require the explicit end marker.

## Verification

- Focused red tests before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_compress_assembles_head_summary_tail tests/test_compaction.py::test_compress_merges_summary_into_tail_when_both_roles_collide -q`
  - Result before port: `2 failed`

- Focused green tests after implementation:
  - Same command
  - Result: `2 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - Result: `18 passed`

- Integration focused check:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_app_integration.py::test_coding_app_runs_hermes_post_response_compaction -q`
  - Result: `1 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `102 passed`

## Remaining Gaps

- Persisted summary-prefix detection and rehydration are not ported.
- Hermes protected-head system-message handling and bounded tail soft-ceiling details are still simplified.
- Secret redaction instructions, temporal anchoring, focused compression, summary-model fallback, and failure bookkeeping remain simplified.
