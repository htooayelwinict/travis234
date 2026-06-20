# Phase 014: Hermes Tool-Pair Compaction Safety

## Scope

Port the Hermes compaction behavior that keeps assistant tool calls and tool result messages well-formed after middle turns are summarized.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_align_boundary_forward`
  - `_align_boundary_backward`
  - `_sanitize_tool_pairs`

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added dataclass-shaped boundary alignment helpers for `ToolResultMessage` and `ToolCall`.
  - Added post-assembly sanitizer that removes orphaned tool results and inserts stub results for surviving calls whose results were summarized away.
  - Applied the helpers inside `ContextCompressor.compress()` before summary slicing and after result assembly.

- `appV2.2/tests/test_compaction.py`
  - Added regression coverage for a protected head boundary landing on a tool result.
  - Added regression coverage for an orphaned tail tool result after its assistant call is summarized.

## Verification

- Focused red tests before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_compress_keeps_tool_result_when_head_boundary_lands_on_it tests/test_compaction.py::test_compress_removes_orphaned_tool_result_from_tail -q`
  - Result before port: `2 failed`

- Focused green tests after implementation:
  - Same command
  - Result: `2 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - Result: `14 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `98 passed`

## Remaining Gaps

- Protected tail still lacks Hermes newest-user/newest-assistant anchoring.
- Historical image stripping is not ported.
- Summary role selection/merge behavior, explicit summary end markers, secret redaction instructions, temporal anchoring, and summary failure bookkeeping remain simplified.
