# Phase 019: Hermes Protected System Head Sizing

## Scope

Port the Hermes compaction rule that treats a leading `role: system` message as implicitly protected head context while `protect_first_n` counts additional non-system messages.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_protect_head_size`
  - `compress()` head boundary calculation before tool-pair boundary alignment

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added `_protect_head_size()`.
  - Updated `_message_text()` so structural system messages contribute to token accounting.
  - Updated `compress()` to use `_protect_head_size()` before boundary alignment.

- `appV2.2/tests/test_compaction.py`
  - Added a structural `_SystemMessage` test helper for Hermes-style leading system messages.
  - Added direct coverage for `_protect_head_size()` with and without a leading system message.
  - Added end-to-end compression coverage proving the leading system message and configured non-system head message remain live and are excluded from the summarizer prompt.

## Verification

- Focused red test before helper implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_protect_head_size_counts_leading_system_separately -q`
  - Result before port: `1 failed`

- Focused red test before wiring `compress()`:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_compress_protects_system_plus_configured_non_system_head -q`
  - Result before port: `1 failed`

- Focused green tests after implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_protect_head_size_counts_leading_system_separately tests/test_compaction.py::test_compress_protects_system_plus_configured_non_system_head -q`
  - Result: `2 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py -q`
  - Result: `15 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `105 passed`

- Syntax:
  - `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Result: passed

- Diff hygiene:
  - `git diff --check -- appV2.2/appv22/compaction/compressor.py appV2.2/tests/test_compaction.py plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md plan/appv22-pi-hermes-full-scan-20260619-190049/research/appv22-pi-hermes-audit.md`
  - Result: passed

## Remaining Gaps

- Hermes protected-tail soft-ceiling and bounded message floor details are still simplified.
- Secret redaction instructions, temporal anchoring, focused compression, summary-model fallback, and failure bookkeeping remain simplified.
