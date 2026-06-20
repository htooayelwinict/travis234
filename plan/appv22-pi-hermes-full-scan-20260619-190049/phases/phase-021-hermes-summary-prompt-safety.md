# Phase 021: Hermes Summary Prompt Safety

## Scope

Port the Hermes compaction summarizer prompt safety design: redaction instructions, temporal anchoring, historical checkpoint headings, and secret redaction at summary input/output boundaries.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_generate_summary`
  - `_serialize_for_summary`
  - historical summary headings
  - summary output redaction
- `hermes-agent/tests/agent/test_context_compressor_temporal_anchoring.py`
- `hermes-agent/tests/agent/test_redact.py`

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added Hermes historical headings: `## Historical Task Snapshot`, `## Historical In-Progress State`, `## Historical Pending User Asks`, and `## Historical Remaining Work`.
  - Replaced the short appv22 summary prompt with a Hermes-style shared summarizer preamble and structured checkpoint template.
  - Added date-based temporal anchoring via `_current_date_string()` and `_temporal_anchoring_rule()`.
  - Changed iterative-update prompt labels to Hermes-style `PREVIOUS SUMMARY` and `NEW TURNS TO INCORPORATE`.
  - Added local secret redaction for common API key/token/password/credential patterns.
  - Redacts serialized summary input, previous summaries embedded into prompts, and the returned summary text.

- `appV2.2/tests/test_compaction.py`
  - Added prompt-safety regression coverage for the redaction directive, temporal anchoring rule, resolved date interpolation, historical headings, and first-compaction marker.
  - Added redaction-boundary regression coverage proving raw secret values are absent from the summarizer prompt and returned summary.
  - Updated iterative-summary tests for Hermes prompt labels.

## Verification

- Focused red prompt-safety test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_summary_prompt_includes_redaction_and_temporal_anchoring_rules -q`
  - Result before port: `1 failed`

- Focused green prompt-safety test after implementation:
  - Same command
  - Result: `1 passed`

- Focused red redaction-boundary test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_summary_redacts_secret_values_in_prompt_and_output -q`
  - Result before port: `1 failed`

- Focused green redaction-boundary test after implementation:
  - Same command
  - Result: `1 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py -q`
  - Result: `18 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `108 passed`

- Syntax:
  - `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Result: passed

## Remaining Gaps

- Focused compression guidance, summary-model fallback, and failure bookkeeping remain simplified.
